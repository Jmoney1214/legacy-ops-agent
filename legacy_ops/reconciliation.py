from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from .control_plane import SQLiteStore, redact_data
from .domain import AuditEvent, Severity as AuditSeverity

CENT = Decimal("0.01")


class ReconciliationError(ValueError):
    pass


class Marketplace(StrEnum):
    UBER_EATS = "uber_eats"
    DOORDASH = "doordash"


class MatchStatus(StrEnum):
    MATCHED = "matched"
    AMOUNT_MISMATCH = "amount_mismatch"
    REFUND_MISMATCH = "refund_mismatch"
    MISSING_IN_LIGHTSPEED = "missing_in_lightspeed"
    MISSING_IN_PLATFORM = "missing_in_platform"
    DUPLICATE_LIGHTSPEED = "duplicate_lightspeed"
    DUPLICATE_PLATFORM = "duplicate_platform"
    AMBIGUOUS_MATCH = "ambiguous_match"
    PAYOUT_MISMATCH = "payout_mismatch"
    BANK_DEPOSIT_MISMATCH = "bank_deposit_mismatch"
    BANK_DEPOSIT_MISSING = "bank_deposit_missing"


class ExceptionSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def money(value: Any, *, default: Decimal | None = None) -> Decimal:
    if value is None or value == "":
        if default is not None:
            return default
        raise ReconciliationError("Money value is required")
    if isinstance(value, Decimal):
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").strip()
    if text in {"", "-"}:
        if default is not None:
            return default
        raise ReconciliationError("Money value is empty")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ReconciliationError(f"Invalid money value: {value!r}") from exc
    return (-result if negative else result).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_datetime(
    value: Any, *, default_timezone: str = "America/New_York"
) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ReconciliationError("Datetime is required")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ReconciliationError(
                f"Invalid datetime {value!r}; use ISO-8601"
            ) from exc
    if result.tzinfo is None:
        try:
            result = result.replace(tzinfo=ZoneInfo(default_timezone))
        except Exception as exc:
            raise ReconciliationError(
                f"Invalid timezone: {default_timezone}"
            ) from exc
    return result.astimezone(timezone.utc)


def _id(value: Any) -> str | None:
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).lower()
    return normalized or None


def _location(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


@dataclass(frozen=True, slots=True)
class PlatformOrder:
    marketplace: Marketplace
    order_id: str
    ordered_at: datetime
    location: str
    customer_total: Decimal
    merchandise_subtotal: Decimal
    tax_to_merchant: Decimal = Decimal("0")
    tips: Decimal = Decimal("0")
    platform_funded_promotions: Decimal = Decimal("0")
    merchant_funded_promotions: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    refunds: Decimal = Decimal("0")
    chargebacks: Decimal = Decimal("0")
    adjustments: Decimal = Decimal("0")
    payout_id: str | None = None

    @property
    def normalized_order_id(self) -> str:
        result = _id(self.order_id)
        if result is None:
            raise ReconciliationError("Platform order ID cannot be empty")
        return result

    @property
    def expected_net_payout(self) -> Decimal:
        return (
            self.merchandise_subtotal
            + self.tax_to_merchant
            + self.tips
            + abs(self.platform_funded_promotions)
            - abs(self.merchant_funded_promotions)
            - abs(self.commission)
            - abs(self.fees)
            - abs(self.refunds)
            - abs(self.chargebacks)
            + self.adjustments
        ).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class LightspeedSale:
    transaction_id: str
    sold_at: datetime
    location: str
    customer_total: Decimal
    payment_type: str
    external_order_id: str | None = None
    refunds: Decimal = Decimal("0")

    @property
    def normalized_external_order_id(self) -> str | None:
        return _id(self.external_order_id)


@dataclass(frozen=True, slots=True)
class Settlement:
    marketplace: Marketplace
    payout_id: str
    period_start: datetime
    period_end: datetime
    reported_net_payout: Decimal
    payout_date: datetime | None = None
    bank_deposit_amount: Decimal | None = None
    bank_deposit_reference: str | None = None


@dataclass(frozen=True, slots=True)
class OrderMatch:
    platform_order_id: str
    lightspeed_transaction_id: str | None
    status: MatchStatus
    platform_amount: Decimal
    lightspeed_amount: Decimal | None
    variance: Decimal
    method: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(
            status=self.status.value,
            platform_amount=str(self.platform_amount),
            lightspeed_amount=(
                str(self.lightspeed_amount)
                if self.lightspeed_amount is not None
                else None
            ),
            variance=str(self.variance),
        )
        return result


@dataclass(frozen=True, slots=True)
class ReconciliationException:
    exception_type: MatchStatus
    severity: ExceptionSeverity
    marketplace: Marketplace
    explanation: str
    variance: Decimal = Decimal("0")
    platform_order_id: str | None = None
    lightspeed_transaction_id: str | None = None
    payout_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(
            exception_type=self.exception_type.value,
            severity=self.severity.value,
            marketplace=self.marketplace.value,
            variance=str(self.variance),
        )
        return result


@dataclass(frozen=True, slots=True)
class ReconciliationPolicy:
    amount_tolerance: Decimal = Decimal("0.01")
    payout_tolerance: Decimal = Decimal("0.02")
    fallback_time_window_seconds: int = 600
    bank_check_required: bool = False

    def severity(self, variance: Decimal) -> ExceptionSeverity:
        value = abs(variance)
        if value < Decimal("2"):
            return ExceptionSeverity.LOW
        if value < Decimal("25"):
            return ExceptionSeverity.MEDIUM
        if value < Decimal("100"):
            return ExceptionSeverity.HIGH
        return ExceptionSeverity.CRITICAL


@dataclass(slots=True)
class ReconciliationResult:
    marketplace: Marketplace
    period_start: datetime
    period_end: datetime
    input_platform_order_count: int
    matches: list[OrderMatch]
    exceptions: list[ReconciliationException]
    expected_payout: Decimal
    reported_payout: Decimal | None
    bank_deposit_amount: Decimal | None
    run_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def matched_order_count(self) -> int:
        return sum(match.status is MatchStatus.MATCHED for match in self.matches)

    @property
    def order_match_rate(self) -> Decimal:
        if not self.input_platform_order_count:
            return Decimal("1.0000")
        return (
            Decimal(self.matched_order_count)
            / Decimal(self.input_platform_order_count)
        ).quantize(Decimal("0.0001"))

    @property
    def payout_variance(self) -> Decimal:
        if self.reported_payout is None:
            return Decimal("0")
        return (self.reported_payout - self.expected_payout).quantize(CENT)

    @property
    def bank_variance(self) -> Decimal:
        if self.reported_payout is None or self.bank_deposit_amount is None:
            return Decimal("0")
        return (self.bank_deposit_amount - self.reported_payout).quantize(CENT)

    @property
    def unresolved_variance(self) -> Decimal:
        ignored = {
            MatchStatus.PAYOUT_MISMATCH,
            MatchStatus.BANK_DEPOSIT_MISMATCH,
            MatchStatus.BANK_DEPOSIT_MISSING,
        }
        exception_exposure = sum(
            (
                abs(item.variance)
                for item in self.exceptions
                if item.exception_type not in ignored
            ),
            Decimal("0"),
        )
        bank_missing = (
            self.reported_payout.copy_abs()
            if self.reported_payout is not None
            and any(
                item.exception_type is MatchStatus.BANK_DEPOSIT_MISSING
                for item in self.exceptions
            )
            else Decimal("0")
        )
        return max(
            exception_exposure,
            abs(self.payout_variance),
            abs(self.bank_variance),
            bank_missing,
        ).quantize(CENT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "marketplace": self.marketplace.value,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "created_at": self.created_at.isoformat(),
            "input_platform_order_count": self.input_platform_order_count,
            "matched_order_count": self.matched_order_count,
            "order_match_rate": str(self.order_match_rate),
            "expected_payout": str(self.expected_payout),
            "reported_payout": (
                str(self.reported_payout)
                if self.reported_payout is not None
                else None
            ),
            "bank_deposit_amount": (
                str(self.bank_deposit_amount)
                if self.bank_deposit_amount is not None
                else None
            ),
            "payout_variance": str(self.payout_variance),
            "bank_variance": str(self.bank_variance),
            "unresolved_variance": str(self.unresolved_variance),
            "matches": [item.to_dict() for item in self.matches],
            "exceptions": [item.to_dict() for item in self.exceptions],
        }


_PAYMENT_TYPES = {
    Marketplace.UBER_EATS: {"uber", "uber eats", "ubereats"},
    Marketplace.DOORDASH: {"doordash", "door dash"},
}


class ReconciliationEngine:
    def __init__(self, policy: ReconciliationPolicy | None = None):
        self.policy = policy or ReconciliationPolicy()

    def run(
        self,
        *,
        marketplace: Marketplace,
        period_start: datetime,
        period_end: datetime,
        platform_orders: Sequence[PlatformOrder],
        lightspeed_sales: Sequence[LightspeedSale],
        settlement: Settlement | None = None,
    ) -> ReconciliationResult:
        start, end = parse_datetime(period_start), parse_datetime(period_end)
        if end <= start:
            raise ReconciliationError("period_end must be after period_start")

        raw_platform = [
            item
            for item in platform_orders
            if item.marketplace is marketplace and start <= item.ordered_at < end
        ]
        exceptions: list[ReconciliationException] = []
        unique_platform: list[PlatformOrder] = []
        seen: set[str] = set()
        for order in raw_platform:
            key = order.normalized_order_id
            if key in seen:
                exceptions.append(
                    self._exception(
                        marketplace,
                        MatchStatus.DUPLICATE_PLATFORM,
                        order.customer_total,
                        f"Duplicate marketplace order {order.order_id}",
                        platform_order_id=order.order_id,
                    )
                )
            else:
                seen.add(key)
                unique_platform.append(order)

        lightspeed = [
            item
            for item in lightspeed_sales
            if start <= item.sold_at < end
            and _location(item.payment_type) in _PAYMENT_TYPES[marketplace]
        ]
        by_external: dict[str, list[LightspeedSale]] = {}
        for sale in lightspeed:
            if sale.normalized_external_order_id:
                by_external.setdefault(
                    sale.normalized_external_order_id, []
                ).append(sale)
        available = {item.transaction_id: item for item in lightspeed}
        matches: list[OrderMatch] = []

        for order in unique_platform:
            exact = [
                item
                for item in by_external.get(order.normalized_order_id, [])
                if item.transaction_id in available
            ]
            if len(exact) > 1:
                lightspeed_total = sum(
                    (item.customer_total for item in exact), Decimal("0")
                )
                variance = lightspeed_total - order.customer_total
                for item in exact:
                    available.pop(item.transaction_id, None)
                matches.append(
                    OrderMatch(
                        order.order_id,
                        None,
                        MatchStatus.DUPLICATE_LIGHTSPEED,
                        order.customer_total,
                        lightspeed_total,
                        variance,
                        "external_order_id",
                    )
                )
                exceptions.append(
                    self._exception(
                        marketplace,
                        MatchStatus.DUPLICATE_LIGHTSPEED,
                        variance,
                        f"Multiple Lightspeed transactions match {order.order_id}",
                        platform_order_id=order.order_id,
                    )
                )
                continue

            candidate = exact[0] if exact else None
            method = "external_order_id" if candidate else "amount_time_location"
            if candidate is None:
                candidates = self._fallback_candidates(order, available.values())
                if len(candidates) > 1 and self._is_ambiguous(order, candidates):
                    exceptions.append(
                        self._exception(
                            marketplace,
                            MatchStatus.AMBIGUOUS_MATCH,
                            order.customer_total,
                            f"Multiple fallback candidates for {order.order_id}; manual review required",
                            platform_order_id=order.order_id,
                        )
                    )
                    matches.append(
                        OrderMatch(
                            order.order_id,
                            None,
                            MatchStatus.AMBIGUOUS_MATCH,
                            order.customer_total,
                            None,
                            order.customer_total,
                            method,
                        )
                    )
                    continue
                candidate = candidates[0] if candidates else None

            if candidate is None:
                matches.append(
                    OrderMatch(
                        order.order_id,
                        None,
                        MatchStatus.MISSING_IN_LIGHTSPEED,
                        order.customer_total,
                        None,
                        order.customer_total,
                        "none",
                    )
                )
                exceptions.append(
                    self._exception(
                        marketplace,
                        MatchStatus.MISSING_IN_LIGHTSPEED,
                        order.customer_total,
                        f"Order {order.order_id} is absent from Lightspeed",
                        platform_order_id=order.order_id,
                    )
                )
                continue

            available.pop(candidate.transaction_id, None)
            amount_variance = (
                candidate.customer_total - order.customer_total
            ).quantize(CENT)
            refund_variance = (
                abs(candidate.refunds) - abs(order.refunds)
            ).quantize(CENT)
            status = MatchStatus.MATCHED
            variance = Decimal("0")
            if abs(amount_variance) > self.policy.amount_tolerance:
                status, variance = MatchStatus.AMOUNT_MISMATCH, amount_variance
            elif abs(refund_variance) > self.policy.amount_tolerance:
                status, variance = MatchStatus.REFUND_MISMATCH, refund_variance
            matches.append(
                OrderMatch(
                    order.order_id,
                    candidate.transaction_id,
                    status,
                    order.customer_total,
                    candidate.customer_total,
                    variance,
                    method,
                )
            )
            if status is not MatchStatus.MATCHED:
                exceptions.append(
                    self._exception(
                        marketplace,
                        status,
                        variance,
                        f"{status.value.replace('_', ' ').title()} for order {order.order_id}",
                        platform_order_id=order.order_id,
                        lightspeed_transaction_id=candidate.transaction_id,
                    )
                )

        for sale in available.values():
            exceptions.append(
                self._exception(
                    marketplace,
                    MatchStatus.MISSING_IN_PLATFORM,
                    sale.customer_total,
                    f"Lightspeed transaction {sale.transaction_id} has no marketplace order",
                    lightspeed_transaction_id=sale.transaction_id,
                )
            )

        expected = sum(
            (item.expected_net_payout for item in unique_platform), Decimal("0")
        ).quantize(CENT)
        reported = bank = None
        if settlement is not None:
            if settlement.marketplace is not marketplace:
                raise ReconciliationError("Settlement marketplace does not match")
            if (
                parse_datetime(settlement.period_start) != start
                or parse_datetime(settlement.period_end) != end
            ):
                raise ReconciliationError(
                    "Settlement period does not match audit period"
                )
            reported = settlement.reported_net_payout
            bank = settlement.bank_deposit_amount
            payout_variance = (reported - expected).quantize(CENT)
            if abs(payout_variance) > self.policy.payout_tolerance:
                exceptions.append(
                    self._exception(
                        marketplace,
                        MatchStatus.PAYOUT_MISMATCH,
                        payout_variance,
                        f"Settlement {settlement.payout_id} differs from calculated payout",
                        payout_id=settlement.payout_id,
                    )
                )
            if bank is None and self.policy.bank_check_required:
                exceptions.append(
                    self._exception(
                        marketplace,
                        MatchStatus.BANK_DEPOSIT_MISSING,
                        reported,
                        f"No bank deposit found for {settlement.payout_id}",
                        payout_id=settlement.payout_id,
                    )
                )
            elif bank is not None:
                bank_variance = (bank - reported).quantize(CENT)
                if abs(bank_variance) > self.policy.payout_tolerance:
                    exceptions.append(
                        self._exception(
                            marketplace,
                            MatchStatus.BANK_DEPOSIT_MISMATCH,
                            bank_variance,
                            f"Bank deposit differs from settlement {settlement.payout_id}",
                            payout_id=settlement.payout_id,
                        )
                    )

        return ReconciliationResult(
            marketplace,
            start,
            end,
            len(raw_platform),
            matches,
            exceptions,
            expected,
            reported,
            bank,
        )

    def _fallback_candidates(
        self, order: PlatformOrder, sales: Iterable[LightspeedSale]
    ) -> list[LightspeedSale]:
        candidates = [
            sale
            for sale in sales
            if _location(sale.location) == _location(order.location)
            and abs(sale.customer_total - order.customer_total)
            <= self.policy.amount_tolerance
            and abs((sale.sold_at - order.ordered_at).total_seconds())
            <= self.policy.fallback_time_window_seconds
        ]
        return sorted(
            candidates,
            key=lambda sale: (
                abs((sale.sold_at - order.ordered_at).total_seconds()),
                sale.transaction_id,
            ),
        )

    @staticmethod
    def _is_ambiguous(
        order: PlatformOrder, candidates: Sequence[LightspeedSale]
    ) -> bool:
        if len(candidates) < 2:
            return False
        first = abs((candidates[0].sold_at - order.ordered_at).total_seconds())
        second = abs((candidates[1].sold_at - order.ordered_at).total_seconds())
        return first == second

    def _exception(
        self,
        marketplace: Marketplace,
        status: MatchStatus,
        variance: Decimal,
        explanation: str,
        **identifiers: Any,
    ) -> ReconciliationException:
        return ReconciliationException(
            status,
            self.policy.severity(variance),
            marketplace,
            explanation,
            variance.quantize(CENT),
            **identifiers,
        )


_ALIASES = {
    "order_id": ("order id", "platform order id", "delivery id"),
    "ordered_at": ("order date", "ordered at", "date", "time"),
    "location": ("store", "store name", "location", "shop name"),
    "customer_total": (
        "order total",
        "customer total",
        "gross sales",
        "grand total",
    ),
    "subtotal": ("subtotal", "merchandise subtotal", "item subtotal"),
    "tax": ("tax", "merchant tax", "tax to merchant"),
    "tips": ("tip", "tips"),
    "platform_promo": (
        "platform promotion",
        "platform funded promotion",
    ),
    "merchant_promo": (
        "merchant promotion",
        "merchant funded promotion",
    ),
    "commission": ("commission", "commission fee"),
    "fees": ("fees", "other fees", "marketplace fees"),
    "refunds": ("refund", "refunds"),
    "chargebacks": ("chargeback", "chargebacks"),
    "adjustments": ("adjustment", "adjustments"),
    "payout_id": ("payout id", "settlement id"),
    "transaction_id": ("sale id", "transaction id", "receipt id"),
    "external_order_id": (
        "external id",
        "external order id",
        "online order id",
    ),
    "sold_at": ("sale date", "sold at", "completed at"),
    "payment_type": ("payment type", "payment", "tender"),
}


def read_csv_rows(content: str) -> list[dict[str, str]]:
    return [
        dict(row)
        for row in csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    ]


def _canonical(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        re.sub(r"[_\-/]+", " ", str(key)).strip().lower(): value
        for key, value in row.items()
    }


def _field(
    row: Mapping[str, Any], name: str, *, required: bool = False
) -> Any:
    canonical = _canonical(row)
    for alias in _ALIASES[name]:
        if alias in canonical and canonical[alias] not in {None, ""}:
            return canonical[alias]
    if required:
        raise ReconciliationError(f"Missing required field: {name}")
    return None


def parse_platform_rows(
    rows: Iterable[Mapping[str, Any]],
    marketplace: Marketplace,
    *,
    default_timezone: str = "America/New_York",
) -> list[PlatformOrder]:
    output: list[PlatformOrder] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            payout_value = _field(row, "payout_id")
            output.append(
                PlatformOrder(
                    marketplace,
                    str(_field(row, "order_id", required=True)),
                    parse_datetime(
                        _field(row, "ordered_at", required=True),
                        default_timezone=default_timezone,
                    ),
                    str(_field(row, "location", required=True)),
                    money(_field(row, "customer_total", required=True)),
                    money(_field(row, "subtotal", required=True)),
                    money(_field(row, "tax"), default=Decimal("0")),
                    money(_field(row, "tips"), default=Decimal("0")),
                    money(_field(row, "platform_promo"), default=Decimal("0")),
                    money(_field(row, "merchant_promo"), default=Decimal("0")),
                    money(_field(row, "commission"), default=Decimal("0")),
                    money(_field(row, "fees"), default=Decimal("0")),
                    money(_field(row, "refunds"), default=Decimal("0")),
                    money(_field(row, "chargebacks"), default=Decimal("0")),
                    money(_field(row, "adjustments"), default=Decimal("0")),
                    str(payout_value) if payout_value is not None else None,
                )
            )
        except ReconciliationError as exc:
            raise ReconciliationError(
                f"Platform CSV row {row_number}: {exc}"
            ) from exc
    return output


def parse_lightspeed_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    default_timezone: str = "America/New_York",
) -> list[LightspeedSale]:
    output: list[LightspeedSale] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            external_value = _field(row, "external_order_id")
            output.append(
                LightspeedSale(
                    str(_field(row, "transaction_id", required=True)),
                    parse_datetime(
                        _field(row, "sold_at", required=True),
                        default_timezone=default_timezone,
                    ),
                    str(_field(row, "location", required=True)),
                    money(_field(row, "customer_total", required=True)),
                    str(_field(row, "payment_type", required=True)),
                    str(external_value) if external_value is not None else None,
                    money(_field(row, "refunds"), default=Decimal("0")),
                )
            )
        except ReconciliationError as exc:
            raise ReconciliationError(
                f"Lightspeed CSV row {row_number}: {exc}"
            ) from exc
    return output


def build_weekly_report(result: ReconciliationResult) -> str:
    lines = [
        f"# {result.marketplace.value.replace('_', ' ').title()} Reconciliation",
        "",
        f"Period: {result.period_start.date()} through {result.period_end.date()}",
        f"Platform orders: {result.input_platform_order_count}",
        f"Matched orders: {result.matched_order_count}",
        f"Order match rate: {(result.order_match_rate * 100):.2f}%",
        f"Expected payout: ${result.expected_payout:,.2f}",
        (
            f"Reported payout: ${result.reported_payout:,.2f}"
            if result.reported_payout is not None
            else "Reported payout: not supplied"
        ),
        (
            f"Bank deposit: ${result.bank_deposit_amount:,.2f}"
            if result.bank_deposit_amount is not None
            else "Bank deposit: not checked"
        ),
        f"Unresolved exposure: ${result.unresolved_variance:,.2f}",
        "",
        "## Exceptions",
    ]
    lines.extend(
        f"- [{item.severity.value.upper()}] {item.explanation} (${item.variance:,.2f})"
        for item in result.exceptions
    )
    if not result.exceptions:
        lines.append("- None")
    return "\n".join(lines)


class ReconciliationRepository:
    def __init__(self, store: SQLiteStore):
        self.store = store
        with self.store.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reconciliation_runs (
                    id TEXT PRIMARY KEY,
                    marketplace TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliation_exceptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES reconciliation_runs(id)
                        ON DELETE CASCADE,
                    exception_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    variance TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reconciliation_period
                    ON reconciliation_runs(marketplace, period_start, period_end);
                CREATE INDEX IF NOT EXISTS idx_reconciliation_exception_run
                    ON reconciliation_exceptions(run_id);
                """
            )

    def save(self, result: ReconciliationResult) -> None:
        payload = json.dumps(redact_data(result.to_dict()), sort_keys=True)
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO reconciliation_runs(
                    id, marketplace, period_start, period_end,
                    result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    result_json=excluded.result_json,
                    created_at=excluded.created_at
                """,
                (
                    result.run_id,
                    result.marketplace.value,
                    result.period_start.isoformat(),
                    result.period_end.isoformat(),
                    payload,
                    result.created_at.isoformat(),
                ),
            )
            connection.execute(
                "DELETE FROM reconciliation_exceptions WHERE run_id = ?",
                (result.run_id,),
            )
            for item in result.exceptions:
                connection.execute(
                    """
                    INSERT INTO reconciliation_exceptions(
                        run_id, exception_type, severity, variance, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        item.exception_type.value,
                        item.severity.value,
                        str(item.variance),
                        json.dumps(redact_data(item.to_dict()), sort_keys=True),
                    ),
                )
            SQLiteStore._insert_audit(
                connection,
                AuditEvent(
                    "reconciliation_completed",
                    "marketplace_reconciliation",
                    "save",
                    "reconciliation_run",
                    result.run_id,
                    {
                        "marketplace": result.marketplace.value,
                        "exception_count": len(result.exceptions),
                        "unresolved_variance": str(result.unresolved_variance),
                    },
                    (
                        AuditSeverity.HIGH
                        if result.exceptions
                        else AuditSeverity.INFO
                    ),
                ),
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT result_json FROM reconciliation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def list_exceptions(self, run_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM reconciliation_exceptions
                WHERE run_id = ? ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
