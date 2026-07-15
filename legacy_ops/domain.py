from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ApprovalRequest:
    action_type: str
    proposed_action: str
    risk_level: Severity
    requested_by_agent: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = field(default_factory=utc_now_iso)
    decided_at: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["risk_level"] = self.risk_level.value
        result["status"] = self.status.value
        return result


@dataclass(slots=True)
class AuditEvent:
    event_type: str
    actor: str
    action: str
    resource_type: str
    resource_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    severity: Severity = Severity.INFO
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["severity"] = self.severity.value
        return result
