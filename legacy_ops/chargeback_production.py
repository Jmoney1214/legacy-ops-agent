from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .chargeback_integrity import (
    AtomicChargebackWorkflow,
    StrictLightspeedChargebackSaleClient,
)
from .chargebacks import (
    CaseStatus,
    ChargebackError,
    DisputePackage,
    EmailMessage,
    EvidenceDocument,
    PosSale,
    build_dispute_package,
)
from .control_plane import ApprovalError, ApprovalService, SQLiteStore
from .domain import ApprovalStatus, Severity


def normalized_evidence(items: Sequence[Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in items:
        value = item.to_dict() if hasattr(item, "to_dict") else dict(item)
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


def approval_payload(package: DisputePackage) -> dict[str, Any]:
    return {
        "case_id": package.notice.case_id,
        "amount": str(package.notice.amount),
        "portal_url": package.notice.portal_url,
        "reason_for_challenge": package.reason_for_challenge,
        "evidence": [item.to_dict() for item in package.evidence if item.verified],
    }


def approval_matches_package(payload: Mapping[str, Any], package: DisputePackage) -> bool:
    return (
        str(payload.get("case_id")) == package.notice.case_id
        and str(payload.get("amount")) == str(package.notice.amount)
        and str(payload.get("portal_url") or "")
        == str(package.notice.portal_url or "")
        and str(payload.get("reason_for_challenge") or "")
        == package.reason_for_challenge
        and normalized_evidence(payload.get("evidence") or [])
        == normalized_evidence(package.evidence)
    )


class ProductionLightspeedChargebackSaleClient(
    StrictLightspeedChargebackSaleClient
):
    """Strict, date-bounded candidate retrieval for production disputes."""

    production_hardened = True

    async def find_candidates(
        self,
        notice,
        *,
        page_size: int = 100,
        max_pages: int = 10,
        date_window_days: int = 3,
    ) -> list[PosSale]:
        output: dict[str, PosSale] = {}
        transaction_reference = str(notice.transaction_id or "").strip()
        payment_reference = str(notice.payment_id or "").strip()

        if transaction_reference.isdigit():
            try:
                direct = await self.get_sale(transaction_reference)
                output[direct.transaction_id] = direct
            except ChargebackError:
                pass

        filter_attempts: list[tuple[str, str]] = []
        if transaction_reference:
            filter_attempts.extend(
                [
                    ("referenceNumber", transaction_reference),
                    ("ticketNumber", transaction_reference),
                ]
            )
        if payment_reference:
            filter_attempts.extend(
                [
                    ("salePaymentID", payment_reference),
                    ("SalePayments.salePaymentID", payment_reference),
                ]
            )

        for field, reference in filter_attempts:
            try:
                candidates = await self.query_sales(
                    filters={field: reference},
                    page_size=min(page_size, 100),
                    max_pages=2,
                )
            except ChargebackError:
                continue
            for candidate in candidates:
                if payment_reference and candidate.payment_id == payment_reference:
                    output[candidate.transaction_id] = candidate
                elif transaction_reference:
                    output[candidate.transaction_id] = candidate

        if not output:
            filters: dict[str, Any] = {}
            if notice.transaction_date:
                start = notice.transaction_date - timedelta(days=date_window_days)
                end = notice.transaction_date + timedelta(days=date_window_days)
                filters["completeTime"] = (
                    "><,"
                    f"{start.strftime('%Y-%m-%dT%H:%M:%S')},"
                    f"{end.strftime('%Y-%m-%dT%H:%M:%S')}"
                )
            candidates = await self.query_sales(
                filters=filters,
                page_size=page_size,
                max_pages=max_pages,
            )
            for candidate in candidates:
                if abs(candidate.total - notice.amount) > Decimal("0.01"):
                    continue
                if notice.transaction_date and abs(
                    candidate.sold_at - notice.transaction_date
                ) > timedelta(days=date_window_days):
                    continue
                if notice.card_last4 and candidate.card_last4 != notice.card_last4:
                    continue
                if payment_reference and candidate.payment_id != payment_reference:
                    continue
                output[candidate.transaction_id] = candidate
        return list(output.values())


class ProductionChargebackWorkflow(AtomicChargebackWorkflow):
    """Final production workflow with immutable filed cases and approval binding."""

    production_hardened = True

    def prepare(
        self,
        *,
        email: EmailMessage,
        sales: Sequence[PosSale],
        evidence: Sequence[EvidenceDocument],
        default_timezone: str = "America/New_York",
    ) -> DisputePackage:
        notice = self.parser.parse(
            email,
            default_timezone=default_timezone,
            allowed_hosts=self.policy.allowed_portal_hosts,
        )
        sale_match = self.matcher.match(notice, sales)
        package = build_dispute_package(
            notice=notice,
            sale_match=sale_match,
            evidence=evidence,
            policy=self.policy,
        )
        existing = self.repository.get_case(notice.case_id)
        if existing and existing.get("status") == CaseStatus.FILED.value:
            package.status = CaseStatus.FILED
            package.approval_id = existing.get("approval_id")
            self.repository.save_package(package)
            return package

        existing_approval_id = existing.get("approval_id") if existing else None
        if package.ready_for_approval and existing_approval_id:
            approval = self.store.get_approval(existing_approval_id)
            if (
                approval
                and approval.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
                and approval_matches_package(approval.payload, package)
            ):
                package.approval_id = approval.id
                package.status = (
                    CaseStatus.APPROVED
                    if approval.status is ApprovalStatus.APPROVED
                    else CaseStatus.APPROVAL_PENDING
                )

        if package.ready_for_approval and not package.approval_id:
            approval = self.approvals.request(
                action_type="file_chargeback_dispute",
                proposed_action=(
                    f"File chargeback challenge {notice.case_id} for ${notice.amount}"
                ),
                risk_level=Severity.HIGH,
                requested_by_agent="chargeback_dispute_agent",
                payload=approval_payload(package),
            )
            package.approval_id = approval.id
            package.status = CaseStatus.APPROVAL_PENDING

        self.repository.save_package(package)
        return package

    def _validate_current_package(
        self, *, case_id: str, approval_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(f"Approval not found: {approval_id}")
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalError("Chargeback submission requires approved status")
        if approval.action_type != "file_chargeback_dispute":
            raise ApprovalError(
                "Approval action type does not permit chargeback filing"
            )
        case = self.repository.get_case(case_id)
        if case is None:
            raise ChargebackError(f"Chargeback case not found: {case_id}")
        if case.get("approval_id") != approval_id:
            raise ApprovalError("Chargeback case is linked to a different approval")
        if case.get("status") not in {
            CaseStatus.APPROVAL_PENDING.value,
            CaseStatus.APPROVED.value,
        }:
            raise ChargebackError(
                f"Chargeback case is not currently fileable: {case.get('status')}"
            )
        package = case.get("package") or {}
        if not bool(package.get("ready_for_approval")) or bool(package.get("expired")):
            raise ChargebackError(
                "Chargeback package is expired or no longer submission-ready"
            )
        notice = package.get("notice") or {}
        deadline_text = notice.get("deadline")
        if not deadline_text:
            raise ChargebackError("Chargeback deadline is missing")
        deadline = datetime.fromisoformat(str(deadline_text).replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline <= datetime.now(timezone.utc):
            raise ChargebackError("Chargeback response deadline has passed")
        package_evidence = package.get("evidence") or []
        expected = {
            "case_id": str(notice.get("case_id") or case_id),
            "amount": str(notice.get("amount") or ""),
            "portal_url": notice.get("portal_url"),
            "reason_for_challenge": package.get("reason_for_challenge"),
            "evidence": package_evidence,
        }
        if not (
            str(approval.payload.get("case_id")) == expected["case_id"]
            and str(approval.payload.get("amount")) == expected["amount"]
            and str(approval.payload.get("portal_url") or "")
            == str(expected["portal_url"] or "")
            and str(approval.payload.get("reason_for_challenge") or "")
            == str(expected["reason_for_challenge"] or "")
            and normalized_evidence(approval.payload.get("evidence") or [])
            == normalized_evidence(package_evidence)
        ):
            raise ApprovalError(
                "Stored chargeback package no longer matches the approved payload"
            )
        return case, package

    def authorize_submission(self, *, case_id: str, approval_id: str) -> dict[str, Any]:
        case, _ = self._validate_current_package(
            case_id=case_id, approval_id=approval_id
        )
        self.repository.mark_approved(case_id, approval_id)
        return case

    def record_submission(
        self,
        *,
        case_id: str,
        approval_id: str,
        submission_reference: str,
        actor: str = "merchantos_dispute_filer",
    ) -> None:
        self._validate_current_package(case_id=case_id, approval_id=approval_id)
        super().record_submission(
            case_id=case_id,
            approval_id=approval_id,
            submission_reference=submission_reference,
            actor=actor,
        )
