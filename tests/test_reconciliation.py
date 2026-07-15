from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from legacy_ops.control_plane import SQLiteStore
from legacy_ops.reconciliation import (
    LightspeedSale,
    Marketplace,
    MatchStatus,
    PlatformOrder,
    ReconciliationEngine,
    ReconciliationError,
    ReconciliationPolicy,
    ReconciliationRepository,
    Settlement,
    build_weekly_report,
    money,
    parse_datetime,
    parse_lightspeed_rows,
    parse_platform_rows,
    read_csv_rows,
)

UTC = timezone.utc
START = datetime(2026, 7, 6, tzinfo=UTC)
END = datetime(2026, 7, 13, tzinfo=UTC)


def platform(order_id="DD-100", total="25.00", at=None):
    return PlatformOrder(
        Marketplace.DOORDASH,
        order_id,
        at or datetime(2026, 7, 7, 18, tzinfo=UTC),
        "Legacy Sanford",
        Decimal(total),
        Decimal("20"),
        Decimal("1.40"),
        Decimal("3.60"),
        commission=Decimal("4"),
        fees=Decimal("1"),
    )


def sale(sale_id="LS-1", total="25.00", external="DD100", at=None):
    return LightspeedSale(
        sale_id,
        at or datetime(2026, 7, 7, 18, 2, tzinfo=UTC),
        "Legacy Sanford",
        Decimal(total),
        "DoorDash",
        external,
    )


class ReconciliationTests(unittest.TestCase):
    def test_money_and_timezone(self):
        self.assertEqual(money("($12.10)"), Decimal("-12.10"))
        self.assertEqual(parse_datetime("2026-07-07T18:00:00").hour, 22)

    def test_csv_aliases(self):
        platform_csv = (
            "Order ID,Order Date,Store,Order Total,Subtotal,Tax,Tip,"
            "Commission Fee,Other Fees\n"
            "DD-100,2026-07-07T18:00:00Z,Legacy Sanford,25,20,1.4,3.6,4,1\n"
        )
        lightspeed_csv = (
            "Sale ID,External ID,Sale Date,Shop Name,Grand Total,Payment Type\n"
            "LS-1,DD100,2026-07-07T18:02:00Z,Legacy Sanford,25,DoorDash\n"
        )
        orders = parse_platform_rows(
            read_csv_rows(platform_csv), Marketplace.DOORDASH
        )
        sales = parse_lightspeed_rows(read_csv_rows(lightspeed_csv))
        self.assertEqual(orders[0].expected_net_payout, Decimal("20.00"))
        self.assertEqual(sales[0].normalized_external_order_id, "dd100")

    def test_csv_row_error(self):
        with self.assertRaisesRegex(ReconciliationError, "row 2"):
            parse_platform_rows([{"Order ID": "x"}], Marketplace.DOORDASH)

    def test_exact_and_fallback_matches(self):
        engine = ReconciliationEngine()
        exact = engine.run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform()],
            lightspeed_sales=[sale()],
        )
        fallback = engine.run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform()],
            lightspeed_sales=[sale(external=None)],
        )
        self.assertEqual(exact.matches[0].method, "external_order_id")
        self.assertEqual(fallback.matches[0].method, "amount_time_location")

    def test_ambiguous_fallback_requires_review(self):
        engine = ReconciliationEngine()
        result = engine.run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform()],
            lightspeed_sales=[
                sale(
                    "LS-1",
                    external=None,
                    at=datetime(2026, 7, 7, 17, 59, tzinfo=UTC),
                ),
                sale(
                    "LS-2",
                    external=None,
                    at=datetime(2026, 7, 7, 18, 1, tzinfo=UTC),
                ),
            ],
        )
        self.assertEqual(result.matches[0].status, MatchStatus.AMBIGUOUS_MATCH)

    def test_duplicate_platform_excluded_from_payout(self):
        result = ReconciliationEngine().run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform(), platform()],
            lightspeed_sales=[sale()],
        )
        self.assertEqual(result.expected_payout, Decimal("20.00"))
        self.assertEqual(result.input_platform_order_count, 2)

    def test_settlement_and_optional_bank_check(self):
        settlement = Settlement(
            Marketplace.DOORDASH, "P-1", START, END, Decimal("18")
        )
        optional = ReconciliationEngine().run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform()],
            lightspeed_sales=[sale()],
            settlement=settlement,
        )
        required = ReconciliationEngine(
            ReconciliationPolicy(bank_check_required=True)
        ).run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform()],
            lightspeed_sales=[sale()],
            settlement=settlement,
        )
        self.assertNotIn(
            MatchStatus.BANK_DEPOSIT_MISSING,
            {item.exception_type for item in optional.exceptions},
        )
        self.assertIn(
            MatchStatus.BANK_DEPOSIT_MISSING,
            {item.exception_type for item in required.exceptions},
        )

    def test_repository_save_is_idempotent(self):
        result = ReconciliationEngine().run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform()],
            lightspeed_sales=[sale()],
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            repository = ReconciliationRepository(store)
            repository.save(result)
            repository.save(result)
            self.assertEqual(
                repository.get(result.run_id)["run_id"], result.run_id
            )
            self.assertEqual(repository.list_exceptions(result.run_id), [])
            self.assertEqual(len(store.list_audit_events(result.run_id)), 2)

    def test_report(self):
        result = ReconciliationEngine().run(
            marketplace=Marketplace.DOORDASH,
            period_start=START,
            period_end=END,
            platform_orders=[platform()],
            lightspeed_sales=[sale()],
        )
        self.assertIn("Order match rate: 100.00%", build_weekly_report(result))


if __name__ == "__main__":
    unittest.main()
