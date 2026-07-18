from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from legacy_ops.chargebacks import DisputeNotice, DisputeReason
from legacy_ops.lightspeed_chargeback_sales import LightspeedChargebackSaleClient


class FakeLightspeedClient(LightspeedChargebackSaleClient):
    def __init__(self, pages: list[dict]):
        super().__init__(account_id="1", access_token="test-token")
        self.pages = list(pages)
        self.calls: list[tuple[str, dict | None]] = []

    async def _get_json(self, url: str, *, params=None):  # type: ignore[override]
        self.calls.append((url, dict(params) if params else None))
        if not self.pages:
            raise AssertionError("unexpected Lightspeed request")
        return self.pages.pop(0)


def sample_sale(*, sale_id: str = "900", total: str = "125.50") -> dict:
    return {
        "saleID": sale_id,
        "completed": "true",
        "archived": "false",
        "voided": "false",
        "completeTime": "2026-07-10T18:22:00+00:00",
        "total": total,
        "referenceNumber": "WEB-900",
        "ticketNumber": "220000000900",
        "shopID": "1",
        "Shop": {"shopID": "1", "name": "Legacy Wine & Liquor"},
        "Customer": {
            "firstName": "Test",
            "lastName": "Customer",
            "email": "customer@example.com",
        },
        "SalePayments": {
            "SalePayment": {
                "salePaymentID": "700",
                "amount": total,
                "PaymentType": {"name": "Credit Card"},
                "CCCharge": {
                    "ccChargeID": "800",
                    "maskedCardNumber": "************4242",
                    "authorizationCode": "A12345",
                    "entryMethod": "chip",
                },
            }
        },
        "SaleLines": {
            "SaleLine": [
                {
                    "saleLineID": "1",
                    "unitQuantity": "1",
                    "unitPrice": "75.50",
                    "Item": {"description": "Bottle One"},
                },
                {
                    "saleLineID": "2",
                    "unitQuantity": "2",
                    "unitPrice": "25.00",
                    "Item": {"description": "Bottle Two"},
                },
            ]
        },
    }


class LightspeedChargebackSaleTests(unittest.TestCase):
    def test_parse_sale_maps_only_required_payment_fields(self) -> None:
        sale = LightspeedChargebackSaleClient.parse_sale(sample_sale())
        self.assertEqual(sale.transaction_id, "900")
        self.assertEqual(sale.total, Decimal("125.50"))
        self.assertEqual(sale.location, "Legacy Wine & Liquor")
        self.assertEqual(sale.payment_type, "Credit Card")
        self.assertEqual(sale.payment_id, "700")
        self.assertEqual(sale.card_last4, "4242")
        self.assertEqual(sale.approval_code, "A12345")
        self.assertEqual(sale.entry_method, "chip")
        self.assertEqual(sale.receipt_reference, "220000000900")
        self.assertEqual(len(sale.lines), 2)
        self.assertEqual(sale.lines[1].line_total, Decimal("50.00"))

    def test_get_sale_uses_documented_sale_endpoint_and_relations(self) -> None:
        client = FakeLightspeedClient([{"Sale": sample_sale()}])
        sale = asyncio.run(client.get_sale("900"))
        self.assertEqual(sale.transaction_id, "900")
        self.assertIn("/Account/1/Sale/900.json", client.calls[0][0])
        params = client.calls[0][1]
        self.assertIsNotNone(params)
        assert params is not None
        self.assertIn("SalePayments", params["load_relations"])
        self.assertIn("SaleLines", params["load_relations"])

    def test_query_sales_follows_cursor(self) -> None:
        client = FakeLightspeedClient(
            [
                {
                    "@attributes": {"next": "https://next.example/Sale.json"},
                    "Sale": sample_sale(sale_id="900"),
                },
                {
                    "@attributes": {"next": ""},
                    "Sale": sample_sale(sale_id="901", total="40.00"),
                },
            ]
        )
        sales = asyncio.run(client.query_sales(page_size=100, max_pages=2))
        self.assertEqual([item.transaction_id for item in sales], ["900", "901"])
        self.assertEqual(client.calls[1][0], "https://next.example/Sale.json")
        self.assertIsNone(client.calls[1][1])

    def test_find_candidates_filters_amount_date_and_last4(self) -> None:
        client = FakeLightspeedClient(
            [
                {"Sale": []},
                {"Sale": []},
                {
                    "@attributes": {"next": ""},
                    "Sale": [
                        sample_sale(sale_id="900", total="125.50"),
                        sample_sale(sale_id="901", total="50.00"),
                    ],
                },
            ]
        )
        notice = DisputeNotice(
            case_id="CB-1",
            amount=Decimal("125.50"),
            reason=DisputeReason.FRAUDULENT,
            reason_text="unauthorized",
            source_email_id="m1",
            source_subject="Dispute",
            received_at=datetime.now(timezone.utc),
            transaction_date=datetime(2026, 7, 10, tzinfo=timezone.utc),
            transaction_id="WEB-900",
            card_last4="4242",
        )
        candidates = asyncio.run(
            client.find_candidates(notice, page_size=100, max_pages=1)
        )
        self.assertEqual([item.transaction_id for item in candidates], ["900"])


if __name__ == "__main__":
    unittest.main()
