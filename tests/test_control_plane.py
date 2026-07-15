from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_ops.control_plane import (
    ApprovalError,
    ApprovalService,
    SQLiteStore,
    redact_data,
    redact_text,
)
from legacy_ops.domain import ApprovalStatus, AuditEvent, Severity


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
        stored = self.store.get_approval(request.id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.payload["access_token"], "[REDACTED]")

        decided = self.approvals.decide(
            request.id,
            approve=True,
            decided_by="owner",
            reason="Verified",
        )
        self.assertEqual(decided.status, ApprovalStatus.APPROVED)

        executed = self.approvals.mark_executed(
            request.id, actor="email_executor"
        )
        self.assertEqual(executed.status, ApprovalStatus.EXECUTED)
        self.assertEqual(len(self.store.list_audit_events(request.id)), 3)

    def test_stale_transition_is_rejected_without_extra_audit(self) -> None:
        request = self.approvals.request(
            action_type="change_price",
            proposed_action="Change retail price",
            risk_level=Severity.MEDIUM,
            requested_by_agent="inventory",
        )
        self.approvals.decide(
            request.id, approve=False, decided_by="owner"
        )
        with self.assertRaises(ApprovalError):
            self.approvals.decide(
                request.id, approve=True, decided_by="owner"
            )
        self.assertEqual(len(self.store.list_audit_events(request.id)), 2)

    def test_secret_redaction(self) -> None:
        self.assertNotIn(
            "abc.def.ghi",
            redact_text("Authorization: Bearer abc.def.ghi"),
        )
        self.assertNotIn("opaque", redact_text("token=opaque"))
        payload = redact_data(
            {
                "password": "secret",
                "nested": {"api-key": "x", "token": "y"},
            }
        )
        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertEqual(payload["nested"]["api-key"], "[REDACTED]")
        self.assertEqual(payload["nested"]["token"], "[REDACTED]")

    def test_audit_payload_is_redacted(self) -> None:
        event = AuditEvent(
            event_type="tool_call",
            actor="agent",
            action="read",
            resource_type="connector",
            details={"token": "do-not-store"},
        )
        self.store.save_audit_event(event)
        stored_event = self.store.list_audit_events()[0]
        self.assertEqual(stored_event.details["token"], "[REDACTED]")

    def test_wal_mode_enabled(self) -> None:
        with self.store.connection() as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()
