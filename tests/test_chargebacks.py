from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from legacy_ops.chargebacks import (
    CaseStatus,
    ChargebackError,
    ChargebackWorkflow,
    DisputeEmailParser,
    DisputeReason,
    EmailMessage,
    EvidenceDocument,
    EvidenceType,
    PosSale,
    PosSaleMatcher,
    SaleMatchStatus,
    build_dispute_package,
    build_gmail_dispute_query,
    decode_gmail_message,
    validate_reason,
)
from legacy_ops.control_plane import ApprovalError, ApprovalService, SQLiteStore
from legacy_ops.domain import ApprovalStatus


NOW = datetime.now(timezone.utc)
DEADLINE = NOW + timedelta(days=7)
SALE_DATE = NOW - timedelta(days=3)


def dispute_email() -> EmailMessage:
    return EmailMessage(
        message_id="gmail-1",
        subject="Chargeback case CB-12345 requires response",
        sender="payments@example.com",
        received_at=NOW,
        body_text=(
            "Dispute ID: CB-12345\n"
            "Dispute amount: $125.50\n"
            f"Respond by: {DEADLINE.strftime('%m/%d/%Y')}\n"
            f"Transaction date: {SALE_DATE.strftime('%m/%d/%Y')}\n"
            "Transaction ID: SALE-900\n"
            "Card ending in 4242\n"
            "Dispute reason: unauthorized transaction\n"
            "https://us.merchantos.com/disputes/CB-12345"
        ),
    )


def sale(tx: str = "SALE-900", *, amount: Decimal = Decimal("125.50"), last4: str = "4242") -> PosSale:
    return PosSale(
        transaction_id=tx,
        sold_at=SALE_DATE,
        total=amount,
        location="Legacy Wine & Liquor",
        payment_type="credit",
        payment_id="PAY-777",
        card_last4=last4,
        approval_code="A12345",
        entry_method="chip",
        receipt_reference="R-900",
        receipt_path="/evidence/receipt.pdf",
    )


def complete_evidence() -> list[EvidenceDocument]:
    return [
        EvidenceDocument(
            EvidenceType.DISPUTE_NOTICE,
            "gmail:gmail-1",
            "Processor dispute email",
            True,
            "gmail",
        ),
        EvidenceDocument(
            EvidenceType.ITEMIZED_RECEIPT,
            "/evidence/receipt.pdf",
            "Lightspeed itemized receipt",
            True,
            "lightspeed",
        ),
        EvidenceDocument(
            EvidenceType.PAYMENT_AUTHORIZATION,
            "/evidence/auth.pdf",
            "Payment authorization record",
            True,
            "lightspeed",
        ),
        EvidenceDocument(
            EvidenceType.CARD_PRESENT_PROOF,
            "/evidence/emv.pdf",
            "EMV terminal record",
            True,
            "lightspeed",
        ),
    ]


class ChargebackTests(unittest.TestCase):
    def test_gmail_query_and_decoder(self) -> None:
        query = build_gmail_dispute_query(lookback_days=30)
        self.assertIn("newer_than:30d", query)
        text = "Dispute ID: CB-12345"
        encoded = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
        raw = {
            "id": "m1",
            "threadId": "t1",
            "internalDate": str(int(NOW.timestamp() * 1000)),
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Chargeback alert"},
                    {"name": "From", "value": "processor@example.com"},
                ],
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "filename": "",
                        "body": {"data": encoded},
                    },
                    {
                        "mimeType": "application/pdf",
                        "filename": "notice.pdf",
                        "body": {"attachmentId": "a1", "size": 1234},
                    },
                ],
            },
        }
        message = decode_gmail_message(raw)
        self.assertEqual(message.message_id, "m1")
        self.assertIn("CB-12345", message.body_text)
        self.assertEqual(message.attachments[0].filename, "notice.pdf")

    def test_email_parser(self) -> None:
        notice = DisputeEmailParser().parse(dispute_email())
        self.assertEqual(notice.case_id, "CB-12345")
        self.assertEqual(notice.amount, Decimal("125.50"))
        self.assertEqual(notice.reason, DisputeReason.FRAUDULENT)
        self.assertEqual(notice.transaction_id, "SALE-900")
        self.assertEqual(notice.card_last4, "4242")
        self.assertEqual(
            notice.portal_url,
            "https://us.merchantos.com/disputes/CB-12345",
        )
        self.assertIsNotNone(notice.deadline)

    def test_exact_match_and_ready_package(self) -> None:
        notice = DisputeEmailParser().parse(dispute_email())
        match = PosSaleMatcher().match(notice, [sale()])
        self.assertEqual(match.status, SaleMatchStatus.EXACT)
        package = build_dispute_package(
            notice=notice,
            sale_match=match,
            evidence=complete_evidence(),
        )
        self.assertEqual(package.status, CaseStatus.SALE_MATCHED)
        self.assertTrue(package.ready_for_approval)
        self.assertGreaterEqual(len(package.reason_for_challenge), 100)
        self.assertLessEqual(len(package.reason_for_challenge), 1000)
        self.assertIn("SALE-900", package.reason_for_challenge)

    def test_ambiguous_fallback_does_not_guess(self) -> None:
        email = EmailMessage(
            message_id="gmail-2",
            subject="Dispute CB-8888",
            sender="processor@example.com",
            received_at=NOW,
            body_text=(
                "Dispute ID: CB-8888\n"
                "Dispute amount: $40.00\n"
                f"Respond by: {DEADLINE.strftime('%m/%d/%Y')}\n"
                f"Transaction date: {SALE_DATE.strftime('%m/%d/%Y')}\n"
                "Card ending in 1111\n"
                "Reason: not recognized\n"
                "https://us.merchantos.com/disputes/CB-8888"
            ),
        )
        notice = DisputeEmailParser().parse(email)
        sales = [
            sale("A", amount=Decimal("40.00"), last4="1111"),
            sale("B", amount=Decimal("40.00"), last4="1111"),
        ]
        match = PosSaleMatcher().match(notice, sales)
        self.assertEqual(match.status, SaleMatchStatus.AMBIGUOUS)
        self.assertIsNone(match.sale)

    def test_reason_rejects_full_card_number(self) -> None:
        with self.assertRaises(ChargebackError):
            validate_reason(
                "This is a deliberately long explanation containing card "
                "4111111111111111 and enough additional text to exceed the minimum."
            )

    def test_workflow_requires_approval_before_filing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = SQLiteStore(Path(tempdir) / "ops.db")
            approvals = ApprovalService(store)
            workflow = ChargebackWorkflow(store, approvals)
            package = workflow.prepare(
                email=dispute_email(),
                sales=[sale()],
                evidence=complete_evidence(),
            )
            self.assertEqual(package.status, CaseStatus.APPROVAL_PENDING)
            self.assertIsNotNone(package.approval_id)
            assert package.approval_id is not None
            approval = store.get_approval(package.approval_id)
            self.assertIsNotNone(approval)
            assert approval is not None
            self.assertEqual(approval.status, ApprovalStatus.PENDING)

            with self.assertRaises(ApprovalError):
                workflow.record_submission(
                    case_id=package.notice.case_id,
                    approval_id=package.approval_id,
                    submission_reference="SUB-1",
                )

            approvals.decide(
                package.approval_id,
                approve=True,
                decided_by="owner",
                reason="Evidence verified",
            )
            workflow.record_submission(
                case_id=package.notice.case_id,
                approval_id=package.approval_id,
                submission_reference="SUB-1",
            )
            case = workflow.repository.get_case(package.notice.case_id)
            self.assertIsNotNone(case)
            assert case is not None
            self.assertEqual(case["status"], CaseStatus.FILED.value)
            executed = store.get_approval(package.approval_id)
            self.assertIsNotNone(executed)
            assert executed is not None
            self.assertEqual(executed.status, ApprovalStatus.EXECUTED)


if __name__ == "__main__":
    unittest.main()
