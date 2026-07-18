from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from browser_automation.merchantos_dispute import MerchantOSDisputeFiler
from legacy_ops.chargeback_production import ProductionChargebackWorkflow
from legacy_ops.chargebacks import (
    CaseStatus,
    ChargebackError,
    EmailMessage,
    EvidenceDocument,
    EvidenceType,
    PosSale,
)
from legacy_ops.control_plane import ApprovalService, SQLiteStore


class ProductionChargebackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = SQLiteStore(self.root / "ops.db")
        self.approvals = ApprovalService(self.store)
        self.workflow = ProductionChargebackWorkflow(self.store, self.approvals)
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(days=7)
        sale_time = now - timedelta(days=1)
        self.email = EmailMessage(
            message_id="m-production",
            subject="Chargeback CB-9001",
            sender="processor@example.com",
            received_at=now,
            body_text=(
                "Dispute ID: CB-9001\n"
                "Dispute amount: $50.00\n"
                f"Respond by: {deadline.strftime('%m/%d/%Y')}\n"
                f"Transaction date: {sale_time.strftime('%m/%d/%Y')}\n"
                "Transaction ID: 1001\n"
                "Card ending in 4242\n"
                "Dispute reason: unauthorized transaction\n"
                "https://us.merchantos.com/disputes/CB-9001"
            ),
        )
        self.sale = PosSale(
            transaction_id="1001",
            sold_at=sale_time,
            total=Decimal("50.00"),
            location="Legacy Wine & Liquor",
            payment_type="Credit Card",
            payment_id="p-1001",
            card_last4="4242",
            approval_code="APP1",
            entry_method="chip",
        )
        self.receipt = self.root / "receipt.pdf"
        self.auth = self.root / "auth.pdf"
        self.emv = self.root / "emv.pdf"
        for path in (self.receipt, self.auth, self.emv):
            path.write_bytes(b"verified")
        self.evidence = [
            EvidenceDocument(
                EvidenceType.DISPUTE_NOTICE,
                "gmail:m-production",
                "Notice",
                True,
                "gmail",
            ),
            EvidenceDocument(
                EvidenceType.ITEMIZED_RECEIPT,
                str(self.receipt),
                "Receipt",
                True,
                "lightspeed",
            ),
            EvidenceDocument(
                EvidenceType.PAYMENT_AUTHORIZATION,
                str(self.auth),
                "Authorization",
                True,
                "lightspeed",
            ),
            EvidenceDocument(
                EvidenceType.CARD_PRESENT_PROOF,
                str(self.emv),
                "EMV",
                True,
                "lightspeed",
            ),
        ]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _prepare(self):
        return self.workflow.prepare(
            email=self.email,
            sales=[self.sale],
            evidence=self.evidence,
        )

    def test_filed_case_is_not_reopened_by_rescan(self) -> None:
        package = self._prepare()
        assert package.approval_id
        self.approvals.decide(
            package.approval_id,
            approve=True,
            decided_by="owner",
        )
        self.workflow.record_submission(
            case_id=package.notice.case_id,
            approval_id=package.approval_id,
            submission_reference="CONFIRM-9001",
        )
        rescanned = self._prepare()
        self.assertEqual(rescanned.status, CaseStatus.FILED)
        self.assertEqual(rescanned.approval_id, package.approval_id)
        self.assertEqual(
            self.workflow.repository.get_case("CB-9001")["submission_reference"],
            "CONFIRM-9001",
        )

    def test_expired_package_cannot_be_authorized(self) -> None:
        package = self._prepare()
        assert package.approval_id
        self.approvals.decide(
            package.approval_id,
            approve=True,
            decided_by="owner",
        )
        case = self.workflow.repository.get_case("CB-9001")
        package_json = case["package"]
        package_json["expired"] = True
        package_json["ready_for_approval"] = False
        package_json["notice"]["deadline"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        with self.store.connection() as connection:
            connection.execute(
                "UPDATE chargeback_cases SET package_json = ? WHERE case_id = ?",
                (json.dumps(package_json), "CB-9001"),
            )
        with self.assertRaises(ChargebackError):
            self.workflow.authorize_submission(
                case_id="CB-9001",
                approval_id=package.approval_id,
            )

    def test_browser_rejects_changed_package_and_missing_file(self) -> None:
        package = self._prepare()
        assert package.approval_id
        self.approvals.decide(
            package.approval_id,
            approve=True,
            decided_by="owner",
        )
        filer = MerchantOSDisputeFiler(allowed_evidence_roots=[self.root])
        original = package.reason_for_challenge
        package.reason_for_challenge = original + " Materially changed."
        with self.assertRaises(ChargebackError):
            filer._validate_approval(package, self.approvals, package.approval_id)
        package.reason_for_challenge = original
        self.receipt.unlink()
        with self.assertRaises(ChargebackError):
            filer._evidence_paths(package)


if __name__ == "__main__":
    unittest.main()
