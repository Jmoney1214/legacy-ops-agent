from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .domain import ApprovalRequest, ApprovalStatus, AuditEvent, Severity, utc_now_iso

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*['\"]?[^\s,'\"}]+"),
)
_SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "access_token", "refresh_token", "password", "secret", "client_secret"}


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    return redacted


def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else redact_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


class SQLiteStore:
    """Durable bootstrap store for cloud containers.

    All SQL writes are parameterized and all JSON payloads are redacted before
    persistence. A Postgres/Supabase adapter can later implement this same API.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    proposed_action TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    requested_by_agent TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    decision_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    details_json TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_events(resource_type, resource_id, created_at);
                """
            )

    def save_approval(self, approval: ApprovalRequest) -> None:
        payload = json.dumps(redact_data(approval.payload), sort_keys=True)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO approvals (id, action_type, proposed_action, risk_level, requested_by_agent, payload_json, status, created_at, decided_at, decided_by, decision_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status, decided_at=excluded.decided_at, decided_by=excluded.decided_by, decision_reason=excluded.decision_reason, payload_json=excluded.payload_json
                """,
                (approval.id, approval.action_type, approval.proposed_action, approval.risk_level.value, approval.requested_by_agent, payload, approval.status.value, approval.created_at, approval.decided_at, approval.decided_by, approval.decision_reason),
            )

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return self._approval_from_row(row) if row else None

    def list_approvals(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        query = "SELECT * FROM approvals"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status.value,)
        query += " ORDER BY created_at DESC"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def save_audit_event(self, event: AuditEvent) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO audit_events (id, event_type, actor, action, resource_type, resource_id, details_json, severity, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event.id, event.event_type, event.actor, event.action, event.resource_type, event.resource_id, json.dumps(redact_data(event.details), sort_keys=True), event.severity.value, event.created_at),
            )

    def list_audit_events(self, resource_id: str | None = None) -> list[AuditEvent]:
        query = "SELECT * FROM audit_events"
        params: tuple[Any, ...] = ()
        if resource_id is not None:
            query += " WHERE resource_id = ?"
            params = (resource_id,)
        query += " ORDER BY created_at DESC"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [AuditEvent(id=row["id"], event_type=row["event_type"], actor=row["actor"], action=row["action"], resource_type=row["resource_type"], resource_id=row["resource_id"], details=json.loads(row["details_json"]), severity=Severity(row["severity"]), created_at=row["created_at"]) for row in rows]

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(id=row["id"], action_type=row["action_type"], proposed_action=row["proposed_action"], risk_level=Severity(row["risk_level"]), requested_by_agent=row["requested_by_agent"], payload=json.loads(row["payload_json"]), status=ApprovalStatus(row["status"]), created_at=row["created_at"], decided_at=row["decided_at"], decided_by=row["decided_by"], decision_reason=row["decision_reason"])


class ApprovalError(ValueError):
    pass


class ApprovalService:
    """Human approval gate for external, financial, and legal actions."""

    def __init__(self, store: SQLiteStore):
        self.store = store

    def _audit(self, event: AuditEvent) -> None:
        self.store.save_audit_event(event)

    def request(self, *, action_type: str, proposed_action: str, risk_level: Severity, requested_by_agent: str, payload: dict[str, Any] | None = None) -> ApprovalRequest:
        approval = ApprovalRequest(action_type=action_type, proposed_action=proposed_action, risk_level=risk_level, requested_by_agent=requested_by_agent, payload=payload or {})
        self.store.save_approval(approval)
        self._audit(AuditEvent(event_type="approval_requested", actor=requested_by_agent, action=action_type, resource_type="approval", resource_id=approval.id, details={"risk_level": risk_level.value}, severity=risk_level))
        return approval

    def decide(self, approval_id: str, *, approve: bool, decided_by: str, reason: str | None = None) -> ApprovalRequest:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(f"Approval not found: {approval_id}")
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"Approval {approval_id} is already {approval.status.value}")
        approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        approval.decided_at = utc_now_iso()
        approval.decided_by = decided_by
        approval.decision_reason = reason
        self.store.save_approval(approval)
        self._audit(AuditEvent(event_type="approval_decided", actor=decided_by, action=approval.status.value, resource_type="approval", resource_id=approval.id, details={"reason": reason}, severity=approval.risk_level))
        return approval

    def mark_executed(self, approval_id: str, *, actor: str) -> ApprovalRequest:
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalError(f"Approval not found: {approval_id}")
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalError("Only approved actions may be marked executed")
        approval.status = ApprovalStatus.EXECUTED
        self.store.save_approval(approval)
        self._audit(AuditEvent(event_type="approved_action_executed", actor=actor, action=approval.action_type, resource_type="approval", resource_id=approval.id, severity=approval.risk_level))
        return approval
