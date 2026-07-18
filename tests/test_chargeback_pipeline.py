from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from legacy_ops.chargeback_pipeline import ChargebackPipeline
from legacy_ops.chargeback_production import ProductionChargebackWorkflow
from legacy_ops.chargebacks import (
    CaseStatus,
    EmailMessage,
    EvidenceDocument,
    EvidenceType,
    PosSale,
)
from legacy_ops.control_plane import ApprovalService, SQLiteStore


NOW = datetime.now(timezone.utc)
SALE_TIME = NOW - timedelta(days=2)
DEADLINE = NOW + timedelta(days=8)


class FakeGmail:
    async def search_disputes(self, **kwargs):
        return [
            EmailMessage(
                message_id="m1",
                subject="Chargeback case CB-5555",
                sender="processor@example.com",
                received_at=NOW,
                body_text=(
                    "Dispute ID: CB-5555\n"
                    "Dispute amount: $75.00\n"
                    f"Respond by: {DEADLINE.strftime('%m/%d/%Y')}\n"
                    f"Transaction date: {SALE_TIME.strftime('%m/%d/%Y')}\n"
                    "Transaction ID: 500\n"
                    "Card ending in 4242\n"
                    "Dispute reason: unauthorized transaction\n"
                    "https://us.merchantos.com/disputes/CB-5555"
                ),
            )
        ]


class FakeLightspeed:
    strict_matching = True
    production_hardened = True

    async def find_candidates(self, notice, **kwargs):
        return [
            PosSale(
                transaction_id="500",
                sold_at=SALE_TIME,
                total=Decimal("75.00"),
                location="Legacy Wine & Liquor",
                payment_type="Credit Card",
                payment_id="700",
                card_last4="4242",
                approval_code="A55",
                entry_method="chip",
            )
        ]


class BrokenLightspeed(FakeLightspeed):
    async def find_candidates(self, notice, **kwargs):
        raise TimeoutError("upstream timed out with access_token=secret")


async def evidence_resolver(email, sales):
    return [
        EvidenceDocument(
            EvidenceType.DISPUTE_NOTICE,
            "gmail:m1",
            "Dispute notice",
            True,
            "gmail",
        ),
        EvidenceDocument(
            EvidenceType.ITEMIZED_RECEIPT,
            "/secure/receipt.pdf",
            "Receipt",
            True,
            "lightspeed",
        ),
        EvidenceDocument(
            EvidenceType.PAYMENT_AUTHORIZATION,
            "/secure/auth.pdf",
            "Authorization",
            True,
            "lightspeed",
        ),
        EvidenceDocument(
            EvidenceType.CARD_PRESENT_PROOF,
            "/secure/emv.pdf",
            "EMV record",
            True,
            "lightspeed",
        ),
    ]


class ChargebackPipelineTests(unittest.TestCase):
    def _workflow(self, tempdir: str):
        store = SQLiteStore(Path(tempdir) / "ops.db")
        approvals = ApprovalService(store)
        return store, ProductionChargebackWorkflow(store, approvals)

    def test_scan_prepares_approval_gated_package(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            _, workflow = self._workflow(tempdir)
            pipeline = ChargebackPipeline(
                gmail=FakeGmail(),  # type: ignore[arg-type]
                lightspeed=FakeLightspeed(),  # type: ignore[arg-type]
                workflow=workflow,
                evidence_resolver=evidence_resolver,
            )
            result = asyncio.run(pipeline.scan_and_prepare())
            self.assertEqual(len(result.failures), 0)
            self.assertEqual(len(result.packages), 1)
            self.assertEqual(result.approval_pending_count, 1)
            package = result.packages[0]
            self.assertEqual(package.status, CaseStatus.APPROVAL_PENDING)
            self.assertEqual(package.notice.case_id, "CB-5555")
            self.assertIsNotNone(package.approval_id)

    def test_transport_failure_isolated_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            _, workflow = self._workflow(tempdir)
            pipeline = ChargebackPipeline(
                gmail=FakeGmail(),  # type: ignore[arg-type]
                lightspeed=BrokenLightspeed(),  # type: ignore[arg-type]
                workflow=workflow,
                evidence_resolver=evidence_resolver,
            )
            result = asyncio.run(pipeline.scan_and_prepare())
            self.assertEqual(len(result.packages), 0)
            self.assertEqual(len(result.failures), 1)
            self.assertNotIn("secret", result.failures[0].explanation)


if __name__ == "__main__":
    unittest.main()
