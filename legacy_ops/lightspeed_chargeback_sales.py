from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

import httpx

from .chargebacks import (
    ChargebackError,
    DisputeNotice,
    PosSale,
    SaleLine,
    money,
    parse_datetime,
)

try:
    from ls_auth import ensure_access_token  # type: ignore
except Exception:
    ensure_access_token = None  # type: ignore


_RELATIONS = [
    "SaleLines",
    "SaleLines.Item",
    "SalePayments",
    "SalePayments.PaymentType",
    "SalePayments.CCCharge",
    "Shop",
    "Customer",
]
_DIGITS = re.compile(r"\d")


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _relation(container: Mapping[str, Any], parent: str, child: str) -> list[dict[str, Any]]:
    value = ((container.get(parent) or {}).get(child)) or []
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _last_four(value: Any) -> str | None:
    digits = "".join(_DIGITS.findall(str(value or "")))
    return digits[-4:] if len(digits) >= 4 else None


def _first_value(records: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> Any:
    for record in records:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return value
    return None


def _safe_datetime(value: Any) -> datetime:
    return parse_datetime(value or datetime.now(timezone.utc))


class LightspeedChargebackSaleClient:
    """Read-only Lightspeed Retail R-Series sale source for chargeback matching.

    It uses the documented Sale endpoint and requests SaleLines, SalePayments,
    PaymentType, CCCharge, Shop, and Customer relations. Raw payment responses are
    never persisted. Only approved fields, including card last four, are mapped
    into `PosSale` objects.
    """

    def __init__(
        self,
        *,
        account_id: str | None = None,
        shop_id: str | None = None,
        access_token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        timeout_seconds: float = 30.0,
        base_url: str = "https://api.lightspeedapp.com/API/V3",
    ):
        self.account_id = account_id or os.getenv("LS_ACCOUNT_ID", "").strip()
        if not self.account_id:
            raise ChargebackError("LS_ACCOUNT_ID is required")
        self.shop_id = shop_id or os.getenv("LS_SHOP_ID")
        self._access_token = access_token
        self._token_provider = token_provider
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

    def _token(self) -> str:
        if self._token_provider:
            token = self._token_provider()
            if token:
                return token
        if callable(ensure_access_token):
            try:
                token = ensure_access_token(120)
                if token:
                    return token
            except Exception:
                pass
        token = self._access_token or os.getenv("LS_ACCESS_TOKEN")
        if not token:
            raise ChargebackError(
                "A valid Lightspeed access token or refresh-token provider is required"
            )
        return token

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }

    def _url(self, suffix: str) -> str:
        return f"{self.base_url}/Account/{self.account_id}/{suffix.lstrip('/')}"

    async def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params, headers=self._headers)
        if response.status_code >= 400:
            request_id = (
                response.headers.get("x-request-id")
                or response.headers.get("x-lightspeed-request-id")
                or "unknown"
            )
            raise ChargebackError(
                f"Lightspeed Sale API failed with status {response.status_code}; "
                f"request_id={request_id}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ChargebackError("Lightspeed Sale API returned an unexpected response")
        return payload

    @staticmethod
    def _sales(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        value = payload.get("Sale") or []
        return [item for item in _as_list(value) if isinstance(item, dict)]

    @staticmethod
    def _next_url(payload: Mapping[str, Any]) -> str | None:
        attributes = payload.get("@attributes") or {}
        value = attributes.get("next") if isinstance(attributes, dict) else None
        return str(value) if value else None

    @staticmethod
    def parse_sale(record: Mapping[str, Any]) -> PosSale:
        sale_payments = _relation(record, "SalePayments", "SalePayment")
        payment_types = [
            payment.get("PaymentType")
            for payment in sale_payments
            if isinstance(payment.get("PaymentType"), dict)
        ]
        cc_charges: list[dict[str, Any]] = []
        for payment in sale_payments:
            relation = payment.get("CCCharge")
            if isinstance(relation, dict):
                cc_charges.append(relation)
            elif relation:
                cc_charges.extend(
                    item for item in _as_list(relation) if isinstance(item, dict)
                )

        card_last4 = _last_four(
            _first_value(
                cc_charges + sale_payments,
                (
                    "cardLastFour",
                    "cardLast4",
                    "last4",
                    "maskedCardNumber",
                    "accountNumber",
                    "cardNumber",
                ),
            )
        )
        approval_code = _first_value(
            cc_charges + sale_payments,
            ("authorizationCode", "authCode", "approvalCode", "authorization"),
        )
        entry_method = _first_value(
            cc_charges + sale_payments,
            ("entryMethod", "cardEntryMethod", "captureType", "cardPresent"),
        )
        payment_id = _first_value(
            sale_payments + cc_charges,
            ("salePaymentID", "ccChargeID", "paymentID"),
        )
        payment_type = _first_value(
            [item for item in payment_types if isinstance(item, dict)] + sale_payments,
            ("name", "description", "paymentTypeID"),
        ) or "card"

        negative_payments = Decimal("0")
        for payment in sale_payments:
            amount = money(payment.get("amount"), default=Decimal("0"))
            if amount < 0:
                negative_payments += abs(amount)

        lines: list[SaleLine] = []
        for raw_line in _relation(record, "SaleLines", "SaleLine"):
            quantity = money(raw_line.get("unitQuantity"), default=Decimal("1"))
            item = raw_line.get("Item") if isinstance(raw_line.get("Item"), dict) else {}
            description = str(
                raw_line.get("description")
                or item.get("description")
                or item.get("customSku")
                or raw_line.get("itemID")
                or "Item"
            ).strip()
            unit_price = money(
                raw_line.get("unitPrice")
                or raw_line.get("price")
                or raw_line.get("calcSubtotal"),
                default=Decimal("0"),
            )
            lines.append(
                SaleLine(
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                )
            )

        shop = record.get("Shop") if isinstance(record.get("Shop"), dict) else {}
        customer = (
            record.get("Customer")
            if isinstance(record.get("Customer"), dict)
            else {}
        )
        customer_name = " ".join(
            part
            for part in (
                str(customer.get("firstName") or "").strip(),
                str(customer.get("lastName") or "").strip(),
            )
            if part
        ) or str(customer.get("company") or "").strip()

        sale_id = str(record.get("saleID") or "").strip()
        if not sale_id:
            raise ChargebackError("Lightspeed sale is missing saleID")
        sold_at = _safe_datetime(
            record.get("completeTime")
            or record.get("timeStamp")
            or record.get("createTime")
        )
        location = str(
            shop.get("name")
            or record.get("shopName")
            or record.get("shopID")
            or ""
        ).strip()
        return PosSale(
            transaction_id=sale_id,
            sold_at=sold_at,
            total=money(
                record.get("total")
                or record.get("calcTotal")
                or record.get("displayableTotal")
            ),
            location=location,
            payment_type=str(payment_type),
            payment_id=str(payment_id).strip() if payment_id is not None else None,
            external_order_id=(
                str(record.get("referenceNumber") or "").strip() or None
            ),
            card_last4=card_last4,
            approval_code=(
                str(approval_code).strip() if approval_code is not None else None
            ),
            entry_method=(
                str(entry_method).strip() if entry_method is not None else None
            ),
            customer_name=customer_name or None,
            customer_email=(
                str(customer.get("email") or "").strip() or None
            ),
            receipt_reference=(
                str(record.get("ticketNumber") or "").strip() or None
            ),
            refunded_amount=negative_payments,
            lines=tuple(lines),
        )

    async def get_sale(self, sale_id: str) -> PosSale:
        if not str(sale_id).strip().isdigit():
            raise ChargebackError("Lightspeed saleID must be numeric")
        payload = await self._get_json(
            self._url(f"Sale/{sale_id}.json"),
            params={"load_relations": json.dumps(_RELATIONS)},
        )
        sales = self._sales(payload)
        if len(sales) != 1:
            raise ChargebackError(f"Lightspeed sale was not found: {sale_id}")
        return self.parse_sale(sales[0])

    async def query_sales(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> list[PosSale]:
        if page_size < 1 or page_size > 100:
            raise ChargebackError("page_size must be between 1 and 100")
        if max_pages < 1 or max_pages > 100:
            raise ChargebackError("max_pages must be between 1 and 100")
        params: dict[str, Any] = {
            "limit": page_size,
            "completed": "true",
            "archived": "false",
            "voided": "false",
            "load_relations": json.dumps(_RELATIONS),
        }
        if self.shop_id:
            params["shopID"] = self.shop_id
        if filters:
            params.update(filters)

        url = self._url("Sale.json")
        output: list[PosSale] = []
        for page_index in range(max_pages):
            payload = await self._get_json(url, params=params if page_index == 0 else None)
            for record in self._sales(payload):
                output.append(self.parse_sale(record))
            next_url = self._next_url(payload)
            if not next_url:
                break
            url = next_url
        return output

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
            if reference:
                for candidate in await self.query_sales(
                    filters={field: reference},
                    page_size=min(page_size, 100),
                    max_pages=2,
                ):
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
