from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Protocol, Sequence

from .chargebacks import (
    CENT,
    CaseStatus,
    ChargebackError,
    ChargebackWorkflow,
    DisputeNotice,
    PosSale,
    PosSaleMatcher,
    SaleLine,
    SaleMatch,
    SaleMatchStatus,
    money,
)
from .control_plane import ApprovalError, ApprovalService, SQLiteStore
from .domain import ApprovalStatus, AuditEvent, Severity
from .lightspeed_chargeback_sales import LightspeedChargebackSaleClient


class PortalSubmissionResult(Protocol):
    case_id: str
    status: str
    submission_reference: str | None


class PortalFiler(Protocol):
    def fill_or_submit(self, **kwargs: Any) -> PortalSubmissionResult: ...


class StrictPosSaleMatcher(PosSaleMatcher):
    """Fail closed when a reference match conflicts with financial evidence."""

    def match(self, notice: DisputeNotice, sales: Sequence[PosSale]) -> SaleMatch:
        result = super().match(notice, sales)
        sale = result.sale
        if result.status is SaleMatchStatus.EXACT and sale is not None:
            amount_variance = abs(sale.total - notice.amount)
            card_conflict = bool(
                notice.card_last4
                and sale.card_last4
                and notice.card_last4 != sale.card_last4
            )
            if amount_variance > self.policy.amount_tolerance or card_conflict:
                reasons = [
                    "exact reference conflicts with the dispute evidence",
                    f"amount variance: {amount_variance}",
                ]
                if card_conflict:
                    reasons.append("card last four conflict")
                return SaleMatch(
                    status=SaleMatchStatus.AMBIGUOUS,
                    sale=None,
                    score=result.score,
                    method="exact_reference_conflict",
                    candidate_transaction_ids=(sale.transaction_id,),
                    reasons=tuple(reasons),
                )
        return result


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _relation(
    container: Mapping[str, Any], parent: str, child: str
) -> list[dict[str, Any]]:
    parent_value = container.get(parent) or {}
    if not isinstance(parent_value, Mapping):
        return []
    value = parent_value.get(child) or []
    return [item for item in _as_list(value) if isinstance(item, dict)]


class StrictLightspeedChargebackSaleClient(LightspeedChargebackSaleClient):
    """Production-safe Lightspeed adapter used by the chargeback pipeline."""

    strict_matching = True

    @staticmethod
    def parse_sale(record: Mapping[str, Any]) -> PosSale:
        timestamp = (
            record.get("completeTime")
            or record.get("timeStamp")
            or record.get("createTime")
        )
        if timestamp in (None, ""):
            raise ChargebackError(
                "Lightspeed sale is missing its financial transaction timestamp"
            )

        sale = LightspeedChargebackSaleClient.parse_sale(record)
        corrected_lines: list[SaleLine] = []
        raw_lines = _relation(record, "SaleLines", "SaleLine")
        for raw_line in raw_lines:
            quantity = money(raw_line.get("unitQuantity"), default=Decimal("1"))
            if quantity == 0:
                raise ChargebackError("Lightspeed sale line has zero quantity")
            item = raw_line.get("Item")
            item = item if isinstance(item, Mapping) else {}
            description = str(
                raw_line.get("description")
                or item.get("description")
                or item.get("customSku")
                or raw_line.get("itemID")
                or "Item"
            ).strip()
            direct_unit_price = raw_line.get("unitPrice")
            if direct_unit_price in (None, ""):
                direct_unit_price = raw_line.get("price")
            if direct_unit_price not in (None, ""):
                unit_price = money(direct_unit_price)
            else:
                subtotal = money(
                    raw_line.get("calcSubtotal"), default=Decimal("0")
                )
                unit_price = (subtotal / quantity).quantize(
                    CENT, rounding=ROUND_HALF_UP
                )
            corrected_lines.append(
                SaleLine(
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                )
            )
        return replace(
            sale,
            lines=tuple(corrected_lines) if raw_lines else sale.lines,
        )

    async def find_candidates(
        self,
        notice: DisputeNotice,
        *,
        page_size: int = 100,
        max_pages: int = 10,
        date_window_days: int = 3,
    ) -> list[PosSale]:
        output: dict[str, PosSale] = {}
        reference = str(notice.transaction_id or "").strip()
        if reference.isdigit():
            try:
                direct = await self.get_sale(reference)
                output[direct.transaction_id] = direct
            except ChargebackError:
                pass

        for field in ("referenceNumber", "ticketNumber"):
            if not reference:
                continue
            try:
                candidates = await self.query_sales(
                    filters={field: reference},
                    page_size=min(page_size, 100),
                    max_pages=2,
                )
            except ChargebackError:
                # Some Lightspeed tenants reject one of these optional filters.
                # Continue to the remaining deterministic search strategies.
                continue
            for candidate in candidates:
                output[candidate.transaction_id] = candidate

        if not output:
            for candidate in await self.query_sales(
                page_size=page_size,
                max_pages=max_pages,
            ):
                if abs(candidate.total - notice.amount) > Decimal("0.01"):
                    continue
                if notice.transaction_date and abs(
                    candidate.sold_at - notice.transaction_date
                ) > timedelta(days=date_window_days):
                    continue
                if notice.card_last4 and candidate.card_last4 != notice.card_last4:
                    continue
                output[candidate.transaction_id] = candidate
        return list(output.values())


class AtomicChargebackWorkflow(ChargebackWorkflow):
    """Chargeback workflow with strict matching and atomic local finalization."""

    atomic_submission = True

    def __init__(
        self,
        store: SQLiteStore,
        approvals: ApprovalService,
        **kwargs: Any,
    ):
        super().__init__(store, approvals, **kwargs)
        self.matcher = StrictPosSaleMatcher(self.policy)

    def record_submission(
        self,
        *,
        case_id: str,
        approval_id: str,
        submission_reference: str,
        actor: str = "merchantos_dispute_filer",
    ) -> None:
        reference = submission_reference.strip()
        if not reference:
            raise ChargebackError("Submission reference is required")

        now = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approval_row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if approval_row is None:
                raise ApprovalError(f"Approval not found: {approval_id}")
            case_row = connection.execute(
                "SELECT * FROM chargeback_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case_row is None:
                raise ChargebackError(f"Chargeback case not found: {case_id}")

            approval_payload = json.loads(approval_row["payload_json"])
            if approval_row["action_type"] != "file_chargeback_dispute":
                raise ApprovalError(
                    "Approval action type does not permit chargeback filing"
                )
            if str(approval_payload.get("case_id")) != case_id:
                raise ApprovalError(
                    "Approval does not belong to this chargeback case"
                )
            if case_row["approval_id"] not in (None, "", approval_id):
                raise ApprovalError(
                    "Chargeback case is linked to a different approval"
                )

            already_complete = (
                approval_row["status"] == ApprovalStatus.EXECUTED.value
                and case_row["status"] == CaseStatus.FILED.value
                and case_row["approval_id"] == approval_id
                and case_row["submission_reference"] == reference
            )
            if already_complete:
                return

            if approval_row["status"] != ApprovalStatus.APPROVED.value:
                raise ApprovalError(
                    "Chargeback submission requires an approved action"
                )
            if case_row["status"] not in {
                CaseStatus.APPROVAL_PENDING.value,
                CaseStatus.APPROVED.value,
                CaseStatus.FILED.value,
            }:
                raise ChargebackError(
                    f"Chargeback case cannot be finalized from status "
                    f"{case_row['status']}"
                )
            if (
                case_row["status"] == CaseStatus.FILED.value
                and case_row["submission_reference"] not in (None, "", reference)
            ):
                raise ChargebackError(
                    "Chargeback case already has a different submission reference"
                )

            package_json = (
                json.loads(case_row["package_json"])
                if case_row["package_json"]
                else {}
            )
            package_json["status"] = CaseStatus.FILED.value
            package_json["approval_id"] = approval_id

            case_cursor = connection.execute(
                """
                UPDATE chargeback_cases
                SET status = ?, approval_id = ?, submission_reference = ?,
                    submitted_at = ?, updated_at = ?, package_json = ?
                WHERE case_id = ?
                """,
                (
                    CaseStatus.FILED.value,
                    approval_id,
                    reference,
                    now,
                    now,
                    json.dumps(package_json, sort_keys=True),
                    case_id,
                ),
            )
            if case_cursor.rowcount != 1:
                raise ChargebackError(
                    "Chargeback case finalization did not update exactly one row"
                )

            approval_cursor = connection.execute(
                """
                UPDATE approvals SET status = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ApprovalStatus.EXECUTED.value,
                    approval_id,
                    ApprovalStatus.APPROVED.value,
                ),
            )
            if approval_cursor.rowcount != 1:
                raise ApprovalError(
                    "Approval execution transition lost a concurrent update"
                )

            SQLiteStore._insert_audit(
                connection,
                AuditEvent(
                    event_type="approved_action_executed",
                    actor=actor,
                    action="file_chargeback_dispute",
                    resource_type="approval",
                    resource_id=approval_id,
                    details={"case_id": case_id},
                    severity=Severity.HIGH,
                ),
            )
            SQLiteStore._insert_audit(
                connection,
                AuditEvent(
                    event_type="chargeback_dispute_filed",
                    actor=actor,
                    action="submitted",
                    resource_type="chargeback_case",
                    resource_id=case_id,
                    details={
                        "approval_id": approval_id,
                        "submission_reference": reference,
                    },
                    severity=Severity.HIGH,
                ),
            )


class PostSubmissionPersistenceError(ChargebackError):
    def __init__(self, case_id: str, submission_reference: str):
        self.case_id = case_id
        self.submission_reference = submission_reference
        super().__init__(
            f"MerchantOS confirmed submission {submission_reference} for {case_id}, "
            "but local finalization failed. Do not resubmit the portal form; retry "
            "AtomicChargebackWorkflow.record_submission with this reference."
        )


class ChargebackSubmissionExecutor:
    """One controlled path for portal submission and durable local finalization."""

    def __init__(
        self,
        *,
        workflow: AtomicChargebackWorkflow,
        filer: PortalFiler,
    ):
        if not getattr(workflow, "atomic_submission", False):
            raise ChargebackError(
                "ChargebackSubmissionExecutor requires AtomicChargebackWorkflow"
            )
        self.workflow = workflow
        self.filer = filer

    def submit(
        self,
        *,
        package: Any,
        approval_id: str,
        **portal_options: Any,
    ) -> PortalSubmissionResult:
        result = self.filer.fill_or_submit(
            package=package,
            approvals=self.workflow.approvals,
            approval_id=approval_id,
            submit=True,
            **portal_options,
        )
        if result.status != "submitted" or not result.submission_reference:
            raise ChargebackError(
                "MerchantOS did not return a confirmed submission reference"
            )
        try:
            self.workflow.record_submission(
                case_id=package.notice.case_id,
                approval_id=approval_id,
                submission_reference=result.submission_reference,
            )
        except Exception as exc:
            raise PostSubmissionPersistenceError(
                package.notice.case_id,
                result.submission_reference,
            ) from exc
        return result
