from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from legacy_ops.chargebacks import (
    ChargebackError,
    DisputePackage,
    validate_portal_url,
    validate_reason,
)
from legacy_ops.control_plane import ApprovalService
from legacy_ops.domain import ApprovalStatus


@dataclass(frozen=True, slots=True)
class MerchantOSSelectors:
    reason_textarea_selectors: tuple[str, ...] = (
        'textarea[placeholder*="Explain your side"]',
        'textarea[aria-label*="Reason for challenge"]',
        'textarea',
    )
    file_input_selector: str = 'input[type="file"]'
    submit_button_selector: str | None = None
    confirmation_selector: str | None = None


@dataclass(frozen=True, slots=True)
class PortalResult:
    case_id: str
    status: str
    evidence_uploaded: tuple[str, ...]
    screenshot_path: str | None = None
    submission_reference: str | None = None


class MerchantOSDisputeFiler:
    """Fill or submit the MerchantOS dispute form through Playwright.

    `submit=False` is the default. A final submission additionally requires an
    approved control-plane action plus explicit, live-validated selectors for
    the submit button and confirmation element.
    """

    def __init__(
        self,
        *,
        allowed_evidence_roots: Sequence[str | Path],
        selectors: MerchantOSSelectors | None = None,
    ):
        roots = tuple(Path(item).expanduser().resolve() for item in allowed_evidence_roots)
        if not roots:
            raise ChargebackError("At least one evidence root is required")
        self.allowed_evidence_roots = roots
        self.selectors = selectors or MerchantOSSelectors()

    def _evidence_paths(self, package: DisputePackage) -> tuple[Path, ...]:
        output: list[Path] = []
        for item in package.evidence:
            if not item.verified:
                continue
            candidate = Path(item.reference).expanduser()
            if not candidate.exists() or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if not any(
                resolved == root or root in resolved.parents
                for root in self.allowed_evidence_roots
            ):
                raise ChargebackError(
                    f"Evidence file is outside the approved roots: {candidate.name}"
                )
            output.append(resolved)
        if not output:
            raise ChargebackError(
                "No verified evidence files are available for portal upload"
            )
        return tuple(output)

    @staticmethod
    def _validate_approval(
        package: DisputePackage,
        approvals: ApprovalService,
        approval_id: str | None,
    ) -> str:
        if not approval_id:
            raise ChargebackError("An approval ID is required for final submission")
        approval = approvals.store.get_approval(approval_id)
        if approval is None:
            raise ChargebackError(f"Approval not found: {approval_id}")
        if approval.status is not ApprovalStatus.APPROVED:
            raise ChargebackError("Final submission requires an approved action")
        if approval.action_type != "file_chargeback_dispute":
            raise ChargebackError("Approval does not authorize chargeback submission")
        if str(approval.payload.get("case_id")) != package.notice.case_id:
            raise ChargebackError("Approval belongs to a different chargeback case")
        return approval_id

    def fill_or_submit(
        self,
        *,
        package: DisputePackage,
        approvals: ApprovalService,
        approval_id: str | None = None,
        storage_state_path: str | Path | None = None,
        submit: bool = False,
        headless: bool = True,
        screenshot_path: str | Path | None = None,
        timeout_ms: int = 30_000,
    ) -> PortalResult:
        if not package.ready_for_approval:
            raise ChargebackError(
                "The dispute package is not complete enough to fill the portal"
            )
        portal_url = validate_portal_url(package.notice.portal_url)
        assert portal_url is not None
        reason = validate_reason(package.reason_for_challenge)
        evidence_paths = self._evidence_paths(package)

        if submit:
            self._validate_approval(package, approvals, approval_id)
            if not self.selectors.submit_button_selector:
                raise ChargebackError(
                    "A live-validated submit button selector is required"
                )
            if not self.selectors.confirmation_selector:
                raise ChargebackError(
                    "A live-validated confirmation selector is required"
                )

        state_path = None
        if storage_state_path:
            state_path = Path(storage_state_path).expanduser().resolve()
            if not state_path.exists() or not state_path.is_file():
                raise ChargebackError("Browser storage state file was not found")

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ChargebackError(
                "Playwright is required for MerchantOS browser automation"
            ) from exc

        capture_path = None
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context_args = {}
            if state_path:
                context_args["storage_state"] = str(state_path)
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(portal_url, wait_until="domcontentloaded")
            final_host = (urlparse(page.url).hostname or "").lower()
            if final_host != "us.merchantos.com":
                browser.close()
                raise ChargebackError(
                    "MerchantOS navigation left the approved portal host"
                )

            textarea = None
            for selector in self.selectors.reason_textarea_selectors:
                locator = page.locator(selector)
                if locator.count() > 0:
                    textarea = locator.first
                    break
            if textarea is None:
                browser.close()
                raise ChargebackError("Reason-for-challenge textarea was not found")
            textarea.fill(reason)

            upload_input = page.locator(self.selectors.file_input_selector)
            if upload_input.count() == 0:
                browser.close()
                raise ChargebackError("Evidence upload input was not found")
            upload_input.first.set_input_files([str(item) for item in evidence_paths])

            if screenshot_path:
                capture_path = Path(screenshot_path).expanduser().resolve()
                capture_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(capture_path), full_page=True)

            if not submit:
                browser.close()
                return PortalResult(
                    case_id=package.notice.case_id,
                    status="form_filled",
                    evidence_uploaded=tuple(item.name for item in evidence_paths),
                    screenshot_path=str(capture_path) if capture_path else None,
                )

            page.locator(self.selectors.submit_button_selector).click()
            try:
                confirmation = page.locator(
                    self.selectors.confirmation_selector
                ).first
                confirmation.wait_for(state="visible")
                confirmation_text = " ".join(
                    confirmation.inner_text().split()
                ).strip()
            except PlaywrightTimeoutError as exc:
                browser.close()
                raise ChargebackError(
                    "Submission was clicked but confirmation was not observed"
                ) from exc

            if not confirmation_text:
                browser.close()
                raise ChargebackError(
                    "Submission confirmation did not contain a reference"
                )
            submission_reference = (
                confirmation_text[:240]
                or f"{package.notice.case_id}-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            )
            if state_path:
                context.storage_state(path=str(state_path))
            browser.close()

        return PortalResult(
            case_id=package.notice.case_id,
            status="submitted",
            evidence_uploaded=tuple(item.name for item in evidence_paths),
            screenshot_path=str(capture_path) if capture_path else None,
            submission_reference=submission_reference,
        )
