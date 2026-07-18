from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.header import decode_header, make_header
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from .control_plane import ApprovalError, ApprovalService, SQLiteStore, redact_data
from .domain import ApprovalStatus, AuditEvent, Severity as AuditSeverity


CENT = Decimal("0.01")
_ALLOWED_PORTAL_HOSTS = {"us.merchantos.com"}
_CARD_NUMBER_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_TAG_PATTERN = re.compile(r"<[^>]+>")
_SPACE_PATTERN = re.compile(r"\s+")


class ChargebackError(ValueError):
    pass


class DisputeReason(StrEnum):
    FRAUDULENT = "fraudulent"
    NOT_RECEIVED = "not_received"
    DUPLICATE = "duplicate"
    CREDIT_NOT_PROCESSED = "credit_not_processed"
    CANCELLED = "cancelled"
    NOT_AS_DESCRIBED = "not_as_described"
    PROCESSING_ERROR = "processing_error"
    OTHER = "other"


class CaseStatus(StrEnum):
    DETECTED = "detected"
    NEEDS_SALE_MATCH = "needs_sale_match"
    SALE_MATCHED = "sale_matched"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    FILED = "filed"
    MANUAL_REVIEW = "manual_review"
    EXPIRED = "expired"


class SaleMatchStatus(StrEnum):
    EXACT = "exact"
    PROBABLE = "probable"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class EvidenceType(StrEnum):
    DISPUTE_NOTICE = "dispute_notice"
    ITEMIZED_RECEIPT = "itemized_receipt"
    PAYMENT_AUTHORIZATION = "payment_authorization"
    CARD_PRESENT_PROOF = "card_present_proof"
    SIGNED_RECEIPT = "signed_receipt"
    DELIVERY_PROOF = "delivery_proof"
    CUSTOMER_COMMUNICATION = "customer_communication"
    REFUND_PROOF = "refund_proof"
    CANCELLATION_POLICY = "cancellation_policy"
    PRODUCT_DESCRIPTION = "product_description"
    TRANSACTION_HISTORY = "transaction_history"
    OTHER = "other"


class SubmissionStatus(StrEnum):
    DRAFT = "draft"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    FORM_FILLED = "form_filled"
    SUBMITTED = "submitted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    attachment_id: str | None
    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class EmailMessage:
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    body_text: str
    thread_id: str | None = None
    attachments: tuple[EmailAttachment, ...] = ()
    display_url: str | None = None


@dataclass(frozen=True, slots=True)
class DisputeNotice:
    case_id: str
    amount: Decimal
    reason: DisputeReason
    reason_text: str
    source_email_id: str
    source_subject: str
    received_at: datetime
    currency: str = "USD"
    deadline: datetime | None = None
    transaction_date: datetime | None = None
    transaction_id: str | None = None
    payment_id: str | None = None
    card_last4: str | None = None
    customer_name: str | None = None
    location: str | None = None
    portal_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "reason": self.reason.value,
            "reason_text": self.reason_text,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "transaction_date": (
                self.transaction_date.isoformat() if self.transaction_date else None
            ),
            "transaction_id": self.transaction_id,
            "payment_id": self.payment_id,
            "card_last4": self.card_last4,
            "customer_name": self.customer_name,
            "location": self.location,
            "portal_url": self.portal_url,
            "source_email_id": self.source_email_id,
            "source_subject": self.source_subject,
            "received_at": self.received_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SaleLine:
    description: str
    quantity: Decimal
    unit_price: Decimal

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(
            CENT, rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True, slots=True)
class PosSale:
    transaction_id: str
    sold_at: datetime
    total: Decimal
    location: str
    payment_type: str
    payment_id: str | None = None
    external_order_id: str | None = None
    card_last4: str | None = None
    approval_code: str | None = None
    entry_method: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    receipt_reference: str | None = None
    receipt_path: str | None = None
    refunded_amount: Decimal = Decimal("0")
    lines: tuple[SaleLine, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "sold_at": self.sold_at.isoformat(),
            "total": str(self.total),
            "location": self.location,
            "payment_type": self.payment_type,
            "payment_id": self.payment_id,
            "external_order_id": self.external_order_id,
            "card_last4": self.card_last4,
            "approval_code": self.approval_code,
            "entry_method": self.entry_method,
            "customer_name": self.customer_name,
            "customer_email": self.customer_email,
            "receipt_reference": self.receipt_reference,
            "receipt_path": self.receipt_path,
            "refunded_amount": str(self.refunded_amount),
            "lines": [
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "line_total": str(line.line_total),
                }
                for line in self.lines
            ],
        }


@dataclass(frozen=True, slots=True)
class SaleMatch:
    status: SaleMatchStatus
    sale: PosSale | None
    score: int
    method: str
    candidate_transaction_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "sale": self.sale.to_dict() if self.sale else None,
            "score": self.score,
            "method": self.method,
            "candidate_transaction_ids": list(self.candidate_transaction_ids),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    evidence_type: EvidenceType
    reference: str
    description: str
    verified: bool
    source: str
    sha256: str | None = None

    @classmethod
    def from_path(
        cls,
        *,
        evidence_type: EvidenceType,
        path: str | Path,
        description: str,
        verified: bool,
        source: str,
    ) -> "EvidenceDocument":
        file_path = Path(path)
        digest = None
        if file_path.exists() and file_path.is_file():
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        return cls(
            evidence_type=evidence_type,
            reference=str(file_path),
            description=description,
            verified=verified,
            source=source,
            sha256=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type": self.evidence_type.value,
            "reference": self.reference,
            "description": self.description,
            "verified": self.verified,
            "source": self.source,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class DisputePolicy:
    amount_tolerance: Decimal = Decimal("0.01")
    date_window_days: int = 3
    minimum_probable_score: int = 85
    minimum_score_gap: int = 20
    reason_min_chars: int = 100
    reason_max_chars: int = 1000
    allowed_portal_hosts: frozenset[str] = frozenset(_ALLOWED_PORTAL_HOSTS)


@dataclass(slots=True)
class DisputePackage:
    notice: DisputeNotice
    sale_match: SaleMatch
    reason_for_challenge: str
    evidence: list[EvidenceDocument]
    missing_evidence: list[EvidenceType]
    status: CaseStatus
    approval_id: str | None = None
    package_id: str = field(default_factory=lambda: str(uuid4()))
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def ready_for_approval(self) -> bool:
        return (
            self.sale_match.status
            in {SaleMatchStatus.EXACT, SaleMatchStatus.PROBABLE}
            and not self.missing_evidence
            and bool(self.notice.deadline)
            and not self.is_expired
            and 100 <= len(self.reason_for_challenge) <= 1000
            and bool(self.notice.portal_url)
        )

    @property
    def is_expired(self) -> bool:
        return bool(
            self.notice.deadline
            and self.notice.deadline < datetime.now(timezone.utc)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "generated_at": self.generated_at.isoformat(),
            "notice": self.notice.to_dict(),
            "sale_match": self.sale_match.to_dict(),
            "reason_for_challenge": self.reason_for_challenge,
            "reason_character_count": len(self.reason_for_challenge),
            "evidence": [item.to_dict() for item in self.evidence],
            "missing_evidence": [item.value for item in self.missing_evidence],
            "status": self.status.value,
            "approval_id": self.approval_id,
            "ready_for_approval": self.ready_for_approval,
            "expired": self.is_expired,
        }


def money(value: Any, *, default: Decimal | None = None) -> Decimal:
    if value is None or value == "":
        if default is not None:
            return default
        raise ChargebackError("Money value is required")
    if isinstance(value, Decimal):
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").strip()
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        if default is not None:
            return default
        raise ChargebackError(f"Invalid money value: {value!r}") from exc
    if negative:
        result = -result
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_datetime(
    value: Any,
    *,
    default_timezone: str = "America/New_York",
    end_of_day: bool = False,
) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ChargebackError("Datetime is required")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        result = None
        for parser in (
            datetime.fromisoformat,
            lambda candidate: datetime.strptime(candidate, "%m/%d/%Y"),
            lambda candidate: datetime.strptime(candidate, "%m/%d/%Y %I:%M %p"),
            lambda candidate: datetime.strptime(candidate, "%Y-%m-%d"),
            lambda candidate: datetime.strptime(candidate, "%B %d, %Y"),
            lambda candidate: datetime.strptime(candidate, "%b %d, %Y"),
        ):
            try:
                result = parser(text)
                break
            except ValueError:
                continue
        if result is None:
            raise ChargebackError(f"Invalid datetime: {value!r}")
    if end_of_day and result.hour == 0 and result.minute == 0:
        result = result.replace(hour=23, minute=59, second=59)
    if result.tzinfo is None:
        try:
            result = result.replace(tzinfo=ZoneInfo(default_timezone))
        except Exception as exc:
            raise ChargebackError(
                f"Invalid timezone: {default_timezone}"
            ) from exc
    return result.astimezone(timezone.utc)


def strip_html(value: str) -> str:
    without_script = re.sub(
        r"(?is)<(script|style).*?>.*?</\1>", " ", value or ""
    )
    return _SPACE_PATTERN.sub(
        " ", html.unescape(_TAG_PATTERN.sub(" ", without_script))
    ).strip()


def validate_portal_url(
    url: str | None, allowed_hosts: frozenset[str] = frozenset(_ALLOWED_PORTAL_HOSTS)
) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in allowed_hosts:
        raise ChargebackError("Dispute portal URL is not on the approved host list")
    return url


def validate_reason(reason: str, policy: DisputePolicy | None = None) -> str:
    active = policy or DisputePolicy()
    normalized = _SPACE_PATTERN.sub(" ", reason).strip()
    if len(normalized) < active.reason_min_chars:
        raise ChargebackError(
            f"Challenge reason must be at least {active.reason_min_chars} characters"
        )
    if len(normalized) > active.reason_max_chars:
        raise ChargebackError(
            f"Challenge reason must be no more than {active.reason_max_chars} characters"
        )
    if _CARD_NUMBER_PATTERN.search(normalized):
        raise ChargebackError("Challenge reason must not contain a full card number")
    return normalized


def build_gmail_dispute_query(
    *,
    lookback_days: int = 60,
    senders: Sequence[str] = (),
) -> str:
    if lookback_days < 1 or lookback_days > 3650:
        raise ChargebackError("lookback_days must be between 1 and 3650")
    sender_clause = " ".join(f"from:{sender}" for sender in senders if sender)
    signals = (
        '{subject:chargeback subject:dispute "reason for challenge" '
        '"customer\'s bank" "respond by"}'
    )
    return " ".join(
        part
        for part in (
            f"newer_than:{lookback_days}d",
            sender_clause,
            signals,
            "-in:trash",
        )
        if part
    )


def _decode_header(value: str | None) -> str:
    return str(make_header(decode_header(value or "")))


def _decode_body(data: str | None) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(f"{data}{padding}").decode(
        "utf-8", errors="replace"
    )


def decode_gmail_message(payload: Mapping[str, Any]) -> EmailMessage:
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in ((payload.get("payload") or {}).get("headers") or [])
    }
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[EmailAttachment] = []

    def walk(part: Mapping[str, Any]) -> None:
        mime_type = str(part.get("mimeType") or "")
        body = part.get("body") or {}
        filename = str(part.get("filename") or "")
        if filename:
            attachments.append(
                EmailAttachment(
                    attachment_id=body.get("attachmentId"),
                    filename=filename,
                    mime_type=mime_type or None,
                    size_bytes=body.get("size"),
                )
            )
        data = body.get("data")
        if data and mime_type == "text/plain":
            plain_parts.append(_decode_body(data))
        elif data and mime_type == "text/html":
            html_parts.append(_decode_body(data))
        for child in part.get("parts") or []:
            walk(child)

    root = payload.get("payload") or {}
    walk(root)
    body_text = "\n".join(item for item in plain_parts if item).strip()
    if not body_text:
        body_text = strip_html("\n".join(html_parts))

    internal_date = payload.get("internalDate")
    received_at = (
        datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        if internal_date
        else datetime.now(timezone.utc)
    )
    message_id = str(payload.get("id") or "").strip()
    if not message_id:
        raise ChargebackError("Gmail message ID is missing")

    return EmailMessage(
        message_id=message_id,
        thread_id=str(payload.get("threadId") or "") or None,
        subject=_decode_header(headers.get("subject")),
        sender=_decode_header(headers.get("from")),
        received_at=received_at,
        body_text=body_text,
        attachments=tuple(attachments),
    )


class DisputeEmailParser:
    _CASE_LABELS = (
        "dispute id",
        "case id",
        "chargeback id",
        "dispute reference",
        "case number",
        "reference number",
    )
    _AMOUNT_LABELS = ("dispute amount", "chargeback amount", "amount")
    _DEADLINE_LABELS = (
        "respond by",
        "response due",
        "due date",
        "deadline",
        "submit by",
    )
    _TRANSACTION_DATE_LABELS = (
        "transaction date",
        "sale date",
        "purchase date",
    )
    _TRANSACTION_ID_LABELS = (
        "transaction id",
        "sale id",
        "receipt number",
        "order id",
    )
    _PAYMENT_ID_LABELS = (
        "payment id",
        "payment reference",
        "authorization reference",
    )
    _REASON_LABELS = (
        "dispute reason",
        "reason",
        "claim",
        "reason code",
    )

    @staticmethod
    def _field(
        text: str,
        labels: Sequence[str],
        value_pattern: str,
    ) -> str | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(
            rf"(?im)(?:^|\b)(?:{label_pattern})\s*[:#-]?\s*({value_pattern})",
            text,
        )
        return match.group(1).strip() if match else None

    @staticmethod
    def _reason(value: str | None, text: str) -> tuple[DisputeReason, str]:
        reason_text = (value or "").strip()
        haystack = f"{reason_text} {text}".lower()
        mappings = (
            (DisputeReason.DUPLICATE, ("duplicate", "charged twice")),
            (
                DisputeReason.CREDIT_NOT_PROCESSED,
                ("credit not processed", "refund not processed", "no refund"),
            ),
            (
                DisputeReason.NOT_RECEIVED,
                ("not received", "merchandise not received", "service not received"),
            ),
            (
                DisputeReason.NOT_AS_DESCRIBED,
                ("not as described", "defective", "different from description"),
            ),
            (
                DisputeReason.CANCELLED,
                ("cancelled", "canceled", "recurring transaction cancelled"),
            ),
            (
                DisputeReason.FRAUDULENT,
                ("fraud", "unauthorized", "not recognized", "cardholder denies"),
            ),
            (
                DisputeReason.PROCESSING_ERROR,
                ("processing error", "incorrect amount", "late presentment"),
            ),
        )
        for category, signals in mappings:
            if any(signal in haystack for signal in signals):
                return category, reason_text or category.value.replace("_", " ")
        return DisputeReason.OTHER, reason_text or "Unspecified dispute"

    def parse(
        self,
        email: EmailMessage,
        *,
        default_timezone: str = "America/New_York",
        allowed_hosts: frozenset[str] = frozenset(_ALLOWED_PORTAL_HOSTS),
    ) -> DisputeNotice:
        text = _SPACE_PATTERN.sub(" ", f"{email.subject}\n{email.body_text}").strip()
        case_id = self._field(
            text,
            self._CASE_LABELS,
            r"[A-Za-z0-9][A-Za-z0-9._/-]{3,}",
        )
        if not case_id:
            subject_match = re.search(
                r"(?i)\b(?:dispute|chargeback)\s*(?:#|id|case)?\s*"
                r"([A-Za-z0-9][A-Za-z0-9._/-]{3,})",
                email.subject,
            )
            case_id = subject_match.group(1) if subject_match else None
        if not case_id:
            raise ChargebackError("Dispute case ID was not found in the email")

        amount_text = self._field(
            text,
            self._AMOUNT_LABELS,
            r"(?:USD\s*)?\$?\s*\(?[\d,]+(?:\.\d{1,2})?\)?",
        )
        if amount_text is None:
            generic_amount = re.search(
                r"(?<!\w)(?:USD\s*)?\$\s*\(?[\d,]+(?:\.\d{1,2})?\)?",
                text,
            )
            amount_text = generic_amount.group(0) if generic_amount else None
        if amount_text is None:
            raise ChargebackError("Dispute amount was not found in the email")

        deadline_text = self._field(
            text,
            self._DEADLINE_LABELS,
            r"[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}",
        )
        transaction_date_text = self._field(
            text,
            self._TRANSACTION_DATE_LABELS,
            r"[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}",
        )
        transaction_id = self._field(
            text,
            self._TRANSACTION_ID_LABELS,
            r"[A-Za-z0-9][A-Za-z0-9._/-]{2,}",
        )
        payment_id = self._field(
            text,
            self._PAYMENT_ID_LABELS,
            r"[A-Za-z0-9][A-Za-z0-9._/-]{2,}",
        )
        reason_value = self._field(
            text,
            self._REASON_LABELS,
            r".{3,120}?(?=(?:\s{2,}|respond by|due date|transaction date|$))",
        )
        reason, reason_text = self._reason(reason_value, text)

        card_match = re.search(
            r"(?i)(?:card\s*(?:ending|last\s*4)|ending\s*in|\*{2,})\s*[:#-]?\s*(\d{4})",
            text,
        )
        portal_match = re.search(r"https://us\.merchantos\.com[^\s<>\"]*", text)
        portal_url = (
            validate_portal_url(portal_match.group(0), allowed_hosts)
            if portal_match
            else None
        )

        return DisputeNotice(
            case_id=case_id,
            amount=abs(money(amount_text.replace("USD", "").strip())),
            reason=reason,
            reason_text=reason_text,
            source_email_id=email.message_id,
            source_subject=email.subject,
            received_at=email.received_at,
            deadline=(
                parse_datetime(
                    deadline_text,
                    default_timezone=default_timezone,
                    end_of_day=True,
                )
                if deadline_text
                else None
            ),
            transaction_date=(
                parse_datetime(
                    transaction_date_text,
                    default_timezone=default_timezone,
                )
                if transaction_date_text
                else None
            ),
            transaction_id=transaction_id,
            payment_id=payment_id,
            card_last4=card_match.group(1) if card_match else None,
            portal_url=portal_url,
        )


def _normalize_identifier(value: str | None) -> str | None:
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).lower()
    return normalized or None


def _normalize_text(value: str | None) -> str:
    return _SPACE_PATTERN.sub(" ", str(value or "").strip().lower())


class PosSaleMatcher:
    def __init__(self, policy: DisputePolicy | None = None):
        self.policy = policy or DisputePolicy()

    def match(self, notice: DisputeNotice, sales: Sequence[PosSale]) -> SaleMatch:
        exact_ids = {
            candidate
            for candidate in (
                _normalize_identifier(notice.transaction_id),
                _normalize_identifier(notice.payment_id),
            )
            if candidate
        }
        exact_candidates = []
        for sale in sales:
            sale_ids = {
                candidate
                for candidate in (
                    _normalize_identifier(sale.transaction_id),
                    _normalize_identifier(sale.payment_id),
                    _normalize_identifier(sale.external_order_id),
                    _normalize_identifier(sale.receipt_reference),
                )
                if candidate
            }
            if exact_ids and exact_ids.intersection(sale_ids):
                exact_candidates.append(sale)

        if len(exact_candidates) == 1:
            sale = exact_candidates[0]
            reasons = ["exact transaction or payment reference"]
            if abs(sale.total - notice.amount) > self.policy.amount_tolerance:
                reasons.append("amount differs from dispute notice")
            return SaleMatch(
                status=SaleMatchStatus.EXACT,
                sale=sale,
                score=200,
                method="exact_reference",
                candidate_transaction_ids=(sale.transaction_id,),
                reasons=tuple(reasons),
            )
        if len(exact_candidates) > 1:
            return SaleMatch(
                status=SaleMatchStatus.AMBIGUOUS,
                sale=None,
                score=200,
                method="duplicate_exact_reference",
                candidate_transaction_ids=tuple(
                    item.transaction_id for item in exact_candidates
                ),
                reasons=("multiple sales share the referenced identifier",),
            )

        scored: list[tuple[int, PosSale, tuple[str, ...]]] = []
        for sale in sales:
            score = 0
            reasons: list[str] = []
            amount_match = abs(sale.total - notice.amount) <= self.policy.amount_tolerance
            if amount_match:
                score += 60
                reasons.append("amount")
            else:
                continue

            if notice.card_last4:
                if sale.card_last4 == notice.card_last4:
                    score += 40
                    reasons.append("card last4")
                else:
                    continue

            if notice.transaction_date:
                delta = abs(sale.sold_at - notice.transaction_date)
                if delta <= timedelta(days=1):
                    score += 30
                    reasons.append("date within one day")
                elif delta <= timedelta(days=self.policy.date_window_days):
                    score += 15
                    reasons.append("date within configured window")
                else:
                    continue

            if notice.location and _normalize_text(notice.location) == _normalize_text(
                sale.location
            ):
                score += 15
                reasons.append("location")
            if notice.customer_name and _normalize_text(
                notice.customer_name
            ) == _normalize_text(sale.customer_name):
                score += 10
                reasons.append("customer name")
            scored.append((score, sale, tuple(reasons)))

        if not scored:
            return SaleMatch(
                status=SaleMatchStatus.NOT_FOUND,
                sale=None,
                score=0,
                method="no_candidate",
                reasons=("no sale met the deterministic matching rules",),
            )

        scored.sort(key=lambda item: (-item[0], item[1].sold_at, item[1].transaction_id))
        top_score, top_sale, top_reasons = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1
        if (
            top_score < self.policy.minimum_probable_score
            or top_score - second_score < self.policy.minimum_score_gap
        ):
            return SaleMatch(
                status=SaleMatchStatus.AMBIGUOUS,
                sale=None,
                score=top_score,
                method="scored_fallback",
                candidate_transaction_ids=tuple(
                    item[1].transaction_id for item in scored[:5]
                ),
                reasons=(
                    "top candidate did not clear the score and uniqueness thresholds",
                ),
            )
        return SaleMatch(
            status=SaleMatchStatus.PROBABLE,
            sale=top_sale,
            score=top_score,
            method="amount_date_card_location",
            candidate_transaction_ids=(top_sale.transaction_id,),
            reasons=top_reasons,
        )


def required_evidence(
    notice: DisputeNotice, sale: PosSale | None
) -> tuple[EvidenceType, ...]:
    required = [
        EvidenceType.DISPUTE_NOTICE,
        EvidenceType.ITEMIZED_RECEIPT,
        EvidenceType.PAYMENT_AUTHORIZATION,
    ]
    if notice.reason is DisputeReason.FRAUDULENT:
        if sale and _normalize_text(sale.entry_method) in {
            "chip",
            "emv",
            "contactless",
            "tap",
            "swipe",
        }:
            required.append(EvidenceType.CARD_PRESENT_PROOF)
        else:
            required.append(EvidenceType.DELIVERY_PROOF)
    elif notice.reason is DisputeReason.NOT_RECEIVED:
        required.append(EvidenceType.DELIVERY_PROOF)
    elif notice.reason is DisputeReason.DUPLICATE:
        required.append(EvidenceType.TRANSACTION_HISTORY)
    elif notice.reason is DisputeReason.CREDIT_NOT_PROCESSED:
        required.append(EvidenceType.REFUND_PROOF)
    elif notice.reason is DisputeReason.CANCELLED:
        required.extend(
            [EvidenceType.CANCELLATION_POLICY, EvidenceType.DELIVERY_PROOF]
        )
    elif notice.reason is DisputeReason.NOT_AS_DESCRIBED:
        required.extend(
            [EvidenceType.PRODUCT_DESCRIPTION, EvidenceType.CUSTOMER_COMMUNICATION]
        )
    return tuple(dict.fromkeys(required))


def _verified_types(evidence: Sequence[EvidenceDocument]) -> set[EvidenceType]:
    return {item.evidence_type for item in evidence if item.verified}


def generate_challenge_reason(
    notice: DisputeNotice,
    sale_match: SaleMatch,
    evidence: Sequence[EvidenceDocument],
    policy: DisputePolicy | None = None,
) -> str:
    active = policy or DisputePolicy()
    sale = sale_match.sale
    if sale is None:
        base = (
            f"Legacy Wine & Liquor is reviewing dispute {notice.case_id} for "
            f"${notice.amount}. No unique Lightspeed sale has been verified, so "
            "the dispute should not be submitted until the transaction match is resolved."
        )
        return _SPACE_PATTERN.sub(" ", base).strip()

    verified = _verified_types(evidence)
    sentences = [
        (
            f"Legacy Wine & Liquor challenges dispute {notice.case_id} for "
            f"${notice.amount}."
        ),
        (
            f"The corresponding Lightspeed sale {sale.transaction_id} was completed "
            f"on {sale.sold_at.astimezone(timezone.utc).strftime('%B %d, %Y')} "
            f"at {sale.location} for ${sale.total}."
        ),
    ]
    if sale.card_last4:
        sentences.append(f"The payment record identifies card ending {sale.card_last4}.")
    if sale.approval_code and EvidenceType.PAYMENT_AUTHORIZATION in verified:
        sentences.append(
            f"The attached authorization record includes approval code {sale.approval_code}."
        )
    if (
        notice.reason is DisputeReason.FRAUDULENT
        and EvidenceType.CARD_PRESENT_PROOF in verified
    ):
        method = sale.entry_method or "card-present"
        sentences.append(
            f"The attached terminal record shows a {method} card-present transaction."
        )
    elif (
        notice.reason is DisputeReason.NOT_RECEIVED
        and EvidenceType.DELIVERY_PROOF in verified
    ):
        sentences.append(
            "The attached fulfillment record documents delivery or pickup completion."
        )
    elif (
        notice.reason is DisputeReason.DUPLICATE
        and EvidenceType.TRANSACTION_HISTORY in verified
    ):
        sentences.append(
            "The attached transaction history distinguishes the sale from any other charge."
        )
    elif (
        notice.reason is DisputeReason.CREDIT_NOT_PROCESSED
        and EvidenceType.REFUND_PROOF in verified
    ):
        sentences.append(
            f"The attached refund record shows ${sale.refunded_amount} in processed credits."
        )
    elif notice.reason is DisputeReason.CANCELLED:
        if EvidenceType.CANCELLATION_POLICY in verified:
            sentences.append("The applicable cancellation policy is attached.")
        if EvidenceType.DELIVERY_PROOF in verified:
            sentences.append(
                "The attached fulfillment record documents completion before cancellation."
            )
    elif notice.reason is DisputeReason.NOT_AS_DESCRIBED:
        if EvidenceType.PRODUCT_DESCRIPTION in verified:
            sentences.append("The original product description is attached.")
        if EvidenceType.CUSTOMER_COMMUNICATION in verified:
            sentences.append(
                "The attached customer communications document the transaction history."
            )

    if EvidenceType.ITEMIZED_RECEIPT in verified:
        sentences.append(
            "The attached itemized receipt is a contemporaneous business record for this sale."
        )
    sentences.append(
        "Based on the verified transaction and supporting records, we request reversal of the chargeback."
    )
    reason = _SPACE_PATTERN.sub(" ", " ".join(sentences)).strip()
    if len(reason) < active.reason_min_chars:
        reason += (
            " The evidence package contains only records tied to this specific transaction."
        )
    if len(reason) > active.reason_max_chars:
        reason = reason[: active.reason_max_chars]
        reason = reason.rsplit(" ", 1)[0].rstrip(" ,;:") + "."
    return validate_reason(reason, active)


def build_dispute_package(
    *,
    notice: DisputeNotice,
    sale_match: SaleMatch,
    evidence: Sequence[EvidenceDocument],
    policy: DisputePolicy | None = None,
) -> DisputePackage:
    active = policy or DisputePolicy()
    reason = generate_challenge_reason(notice, sale_match, evidence, active)
    required = required_evidence(notice, sale_match.sale)
    verified = _verified_types(evidence)
    missing = [item for item in required if item not in verified]

    if notice.deadline and notice.deadline < datetime.now(timezone.utc):
        status = CaseStatus.EXPIRED
    elif sale_match.status is SaleMatchStatus.NOT_FOUND:
        status = CaseStatus.NEEDS_SALE_MATCH
    elif sale_match.status is SaleMatchStatus.AMBIGUOUS:
        status = CaseStatus.MANUAL_REVIEW
    elif missing or not notice.deadline or not notice.portal_url:
        status = CaseStatus.EVIDENCE_INCOMPLETE
    else:
        status = CaseStatus.SALE_MATCHED

    return DisputePackage(
        notice=notice,
        sale_match=sale_match,
        reason_for_challenge=reason,
        evidence=list(evidence),
        missing_evidence=missing,
        status=status,
    )


class ChargebackRepository:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self._initialize()

    def _initialize(self) -> None:
        with self.store.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chargeback_cases (
                    case_id TEXT PRIMARY KEY,
                    source_email_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    notice_json TEXT NOT NULL,
                    match_json TEXT,
                    package_json TEXT,
                    matched_sale_id TEXT,
                    approval_id TEXT,
                    submission_reference TEXT,
                    submitted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chargeback_evidence (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    description TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    sha256 TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES chargeback_cases(case_id)
                );
                CREATE INDEX IF NOT EXISTS idx_chargeback_status
                    ON chargeback_cases(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_chargeback_evidence_case
                    ON chargeback_evidence(case_id, evidence_type);
                """
            )

    def save_package(self, package: DisputePackage) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT created_at FROM chargeback_cases WHERE case_id = ?",
                (package.notice.case_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            connection.execute(
                """
                INSERT INTO chargeback_cases (
                    case_id, source_email_id, status, notice_json, match_json,
                    package_json, matched_sale_id, approval_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    source_email_id=excluded.source_email_id,
                    status=excluded.status,
                    notice_json=excluded.notice_json,
                    match_json=excluded.match_json,
                    package_json=excluded.package_json,
                    matched_sale_id=excluded.matched_sale_id,
                    approval_id=excluded.approval_id,
                    updated_at=excluded.updated_at
                """,
                (
                    package.notice.case_id,
                    package.notice.source_email_id,
                    package.status.value,
                    json.dumps(redact_data(package.notice.to_dict()), sort_keys=True),
                    json.dumps(redact_data(package.sale_match.to_dict()), sort_keys=True),
                    json.dumps(redact_data(package.to_dict()), sort_keys=True),
                    (
                        package.sale_match.sale.transaction_id
                        if package.sale_match.sale
                        else None
                    ),
                    package.approval_id,
                    created_at,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM chargeback_evidence WHERE case_id = ?",
                (package.notice.case_id,),
            )
            for item in package.evidence:
                connection.execute(
                    """
                    INSERT INTO chargeback_evidence (
                        id, case_id, evidence_type, reference, description,
                        verified, source, sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        package.notice.case_id,
                        item.evidence_type.value,
                        item.reference,
                        item.description,
                        int(item.verified),
                        item.source,
                        item.sha256,
                        now,
                    ),
                )
        self.store.save_audit_event(
            AuditEvent(
                event_type="chargeback_package_saved",
                actor="chargeback_dispute_agent",
                action=package.status.value,
                resource_type="chargeback_case",
                resource_id=package.notice.case_id,
                details={
                    "approval_id": package.approval_id,
                    "matched_sale_id": (
                        package.sale_match.sale.transaction_id
                        if package.sale_match.sale
                        else None
                    ),
                    "missing_evidence": [
                        item.value for item in package.missing_evidence
                    ],
                },
                severity=(
                    AuditSeverity.HIGH
                    if package.status
                    in {
                        CaseStatus.APPROVAL_PENDING,
                        CaseStatus.APPROVED,
                        CaseStatus.FILED,
                    }
                    else AuditSeverity.MEDIUM
                ),
            )
        )

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM chargeback_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "case_id": row["case_id"],
            "source_email_id": row["source_email_id"],
            "status": row["status"],
            "notice": json.loads(row["notice_json"]),
            "match": json.loads(row["match_json"]) if row["match_json"] else None,
            "package": (
                json.loads(row["package_json"]) if row["package_json"] else None
            ),
            "matched_sale_id": row["matched_sale_id"],
            "approval_id": row["approval_id"],
            "submission_reference": row["submission_reference"],
            "submitted_at": row["submitted_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def mark_approved(self, case_id: str, approval_id: str) -> None:
        with self.store.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE chargeback_cases
                SET status = ?, approval_id = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (
                    CaseStatus.APPROVED.value,
                    approval_id,
                    datetime.now(timezone.utc).isoformat(),
                    case_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ChargebackError(f"Chargeback case not found: {case_id}")

    def mark_filed(
        self, case_id: str, *, approval_id: str, submission_reference: str
    ) -> None:
        with self.store.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE chargeback_cases
                SET status = ?, approval_id = ?, submission_reference = ?,
                    submitted_at = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (
                    CaseStatus.FILED.value,
                    approval_id,
                    submission_reference,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    case_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ChargebackError(f"Chargeback case not found: {case_id}")


class ChargebackWorkflow:
    def __init__(
        self,
        store: SQLiteStore,
        approvals: ApprovalService,
        *,
        policy: DisputePolicy | None = None,
    ):
        self.store = store
        self.approvals = approvals
        self.policy = policy or DisputePolicy()
        self.repository = ChargebackRepository(store)
        self.parser = DisputeEmailParser()
        self.matcher = PosSaleMatcher(self.policy)

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
        existing_approval_id = existing.get("approval_id") if existing else None
        if package.ready_for_approval:
            if existing_approval_id:
                approval = self.store.get_approval(existing_approval_id)
                if approval and approval.status in {
                    ApprovalStatus.PENDING,
                    ApprovalStatus.APPROVED,
                }:
                    package.approval_id = approval.id
                    package.status = (
                        CaseStatus.APPROVED
                        if approval.status is ApprovalStatus.APPROVED
                        else CaseStatus.APPROVAL_PENDING
                    )
            if not package.approval_id:
                approval = self.approvals.request(
                    action_type="file_chargeback_dispute",
                    proposed_action=(
                        f"File chargeback challenge {notice.case_id} for "
                        f"${notice.amount}"
                    ),
                    risk_level=AuditSeverity.HIGH,
                    requested_by_agent="chargeback_dispute_agent",
                    payload={
                        "case_id": notice.case_id,
                        "amount": str(notice.amount),
                        "portal_url": notice.portal_url,
                        "reason_for_challenge": package.reason_for_challenge,
                        "evidence": [
                            item.to_dict() for item in package.evidence if item.verified
                        ],
                    },
                )
                package.approval_id = approval.id
                package.status = CaseStatus.APPROVAL_PENDING
        self.repository.save_package(package)
        return package

    def authorize_submission(self, *, case_id: str, approval_id: str) -> dict[str, Any]:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(f"Approval not found: {approval_id}")
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalError("Chargeback submission requires approved status")
        if approval.action_type != "file_chargeback_dispute":
            raise ApprovalError("Approval action type does not permit chargeback filing")
        if str(approval.payload.get("case_id")) != case_id:
            raise ApprovalError("Approval does not belong to this chargeback case")
        case = self.repository.get_case(case_id)
        if case is None:
            raise ChargebackError(f"Chargeback case not found: {case_id}")
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
        self.authorize_submission(case_id=case_id, approval_id=approval_id)
        if not submission_reference.strip():
            raise ChargebackError("Submission reference is required")
        self.repository.mark_filed(
            case_id,
            approval_id=approval_id,
            submission_reference=submission_reference,
        )
        self.approvals.mark_executed(approval_id, actor=actor)
        self.store.save_audit_event(
            AuditEvent(
                event_type="chargeback_dispute_filed",
                actor=actor,
                action="submitted",
                resource_type="chargeback_case",
                resource_id=case_id,
                details={"submission_reference": submission_reference},
                severity=AuditSeverity.HIGH,
            )
        )


def parse_pos_sale(record: Mapping[str, Any]) -> PosSale:
    lines = tuple(
        SaleLine(
            description=str(item.get("description") or item.get("item") or ""),
            quantity=money(item.get("quantity"), default=Decimal("1")),
            unit_price=money(
                item.get("unit_price") or item.get("price"), default=Decimal("0")
            ),
        )
        for item in (record.get("lines") or [])
    )
    return PosSale(
        transaction_id=str(
            record.get("transaction_id")
            or record.get("sale_id")
            or record.get("id")
            or ""
        ).strip(),
        sold_at=parse_datetime(
            record.get("sold_at")
            or record.get("sale_date")
            or record.get("created_at")
        ),
        total=money(
            record.get("total")
            or record.get("customer_total")
            or record.get("amount")
        ),
        location=str(
            record.get("location")
            or record.get("shop_name")
            or record.get("shop_id")
            or ""
        ).strip(),
        payment_type=str(
            record.get("payment_type")
            or record.get("tender_type")
            or "card"
        ).strip(),
        payment_id=(
            str(record.get("payment_id") or "").strip() or None
        ),
        external_order_id=(
            str(record.get("external_order_id") or "").strip() or None
        ),
        card_last4=(
            str(record.get("card_last4") or record.get("last4") or "").strip()
            or None
        ),
        approval_code=(
            str(record.get("approval_code") or record.get("auth_code") or "").strip()
            or None
        ),
        entry_method=(
            str(record.get("entry_method") or "").strip() or None
        ),
        customer_name=(
            str(record.get("customer_name") or "").strip() or None
        ),
        customer_email=(
            str(record.get("customer_email") or "").strip() or None
        ),
        receipt_reference=(
            str(record.get("receipt_reference") or record.get("receipt_number") or "").strip()
            or None
        ),
        receipt_path=(
            str(record.get("receipt_path") or "").strip() or None
        ),
        refunded_amount=money(
            record.get("refunded_amount"), default=Decimal("0")
        ),
        lines=lines,
    )
