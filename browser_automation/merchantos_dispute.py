from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
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


def _normalized_evidence(items: Sequence[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "to_dict"):
            value = item.to_dict()
        else:
            value = dict(item)
        if not bool(value.get("verified")):
            continue
        output.append(
            {
                "evidence_type": str(value.get("evidence_type") or ""),
                "reference": str(value.get("reference") or ""),
                "sha256": str(value.get("sha256") or ""),
            }
        )
    return sorted(
        output,
        key=lambda value: (
            value["evidence_type"],
            value["reference"],
            value["sha256"],
        ),
    )


class MerchantOSDisputeFiler:
    """Fill or submit the MerchantOS dispute form through Playwright.

    Preview is the default. Final submission requires a current owner approval
    whose exact text, portal URL, amount, and evidence manifest still match the
    package being sent.
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

    @staticmethod
    def _is_external_reference(reference: str) -> bool:
        parsed = urlparse(reference)
        return bool(parsed.scheme and parsed.scheme != "file")

    def _evidence_paths(self, package: DisputePackage) -> tuple[Path, ...]:
        output: list[Path] = []
        for item in package.evidence:
            if not item.verified:
                continue
            reference = str(item.reference or "").strip()
            if not reference:
                raise ChargebackError(
                    f"Verified {item.evidence_type.value} evidence has no reference"
                )
            if self._is_external_reference(reference):
                continue
            candidate = Path(reference.removeprefix("file://")).expanduser()
            if not candidate.exists() or not candidate.is_file():
                raise ChargebackError(
                    f"Verified evidence file is unavailable: {candidate.name or reference}"
                )
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
                "No verified local evidence files are available for portal upload"
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
        payload = approval.payload
        if str(payload.get("case_id")) != package.notice.case_id:
            raise ChargebackError("Approval belongs to a different chargeback case")
        if str(payload.get("amount")) != str(package.notice.amount):
            raise ChargebackError("Disputed amount changed after approval")
        if str(payload.get("portal_url") or "") != str(package.notice.portal_url or ""):
            raise ChargebackError("MerchantOS portal URL changed after approval")
        if str(payload.get("reason_for_challenge") or "") != package.reason_for_challenge:
            raise ChargebackError("Challenge statement changed after approval")
        if _normalized_evidence(payload.get("evidence") or []) != _normalized_evidence(
            package.evidence
        ):
            raise ChargebackError("Evidence manifest changed after approval")
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

            combined_selector = ", ".join(self.selectors.reason_textarea_selectors)
            try:
                textarea = page.locator(combined_selector).first
                textarea.wait_for(state="attached")
                textarea.fill(reason)
            except PlaywrightTimeoutError as exc:
                browser.close()
                raise ChargebackError(
                    "Reason-for-challenge textarea was not found"
                ) from exc

            try:
                upload_input = page.locator(self.selectors.file_input_selector).first
                upload_input.wait_for(state="attached")
                upload_input.set_input_files([str(item) for item in evidence_paths])
            except PlaywrightTimeoutError as exc:
                browser.close()
                raise ChargebackError("Evidence upload input was not found") from exc

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

            try:
                page.locator(self.selectors.submit_button_selector).click()
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
                    "Submission click failed or confirmation was not observed"
                ) from exc

            if not confirmation_text:
                browser.close()
                raise ChargebackError(
                    "Submission confirmation did not contain a reference"
                )
            submission_reference = confirmation_text[:240] or (
                f"{package.notice.case_id}-"
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
