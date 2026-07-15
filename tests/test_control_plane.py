from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_ops.control_plane import ApprovalError, ApprovalService, SQLiteStore, redact_data, redact_text
from legacy_ops.domain import ApprovalStatus, Severity


class ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "test.db")
        self.approvals = ApprovalService(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_approval_lifecycle_is_audited(self) -> None:
        request = self.approvals.request(
            action_type="send_vendor_email",
            proposed_action="Send payout dispute to DoorDash",
            risk_level=Severity.HIGH,
            requested_by_agent="marketplace_reconciliation",
            payload={"access_token": "do-not-store", "variance": 142.25},
        )
        self.assertEqual(request.status, ApprovalStatus.PENDING)
        stored = self.store.get_approval(request.id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.payload["access_token"], "[REDACTED]")
        decided = self.approvals.decide(request.id, approve=True, decided_by="owner", reason="Verified")
        self.assertEqual(decided.status, ApprovalStatus.APPROVED)
        executed = self.approvals.mark_executed(request.id, actor="email_executor")
        self.assertEqual(executed.status, ApprovalStatus.EXECUTED)
        self.assertGreaterEqual(len(self.store.list_audit_events(request.id)), 3)

    def test_rejects_second_decision(self) -> None:
        request = self.approvals.request(
            action_type="change_price",
            proposed_action="Change retail price",
            risk_level=Severity.MEDIUM,
            requested_by_agent="inventory",
        )
        self.approvals.decide(request.id, approve=False, decided_by="owner")
        with self.assertRaises(ApprovalError):
            self.approvals.decide(request.id, approve=True, decided_by="owner")

    def test_secret_redaction(self) -> None:
        self.assertNotIn("abc.def.ghi", redact_text("Authorization: Bearer abc.def.ghi"))
        payload = redact_data({"password": "secret", "nested": {"api_key": "x"}})
        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertEqual(payload["nested"]["api_key"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
