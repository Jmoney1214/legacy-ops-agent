from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4

from .agent_manifest import AgentManifest
from .control_plane import SQLiteStore, redact_data
from .domain import AuditEvent, Severity, utc_now_iso


class AgentRegistryError(ValueError):
    pass


class AgentLifecycleStage(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REVIEWED = "reviewed"
    STAGING = "staging"
    PRODUCTION = "production"
    RETIRED = "retired"


_ALLOWED_STAGE_TRANSITIONS = {
    AgentLifecycleStage.DRAFT: {AgentLifecycleStage.VALIDATED},
    AgentLifecycleStage.VALIDATED: {AgentLifecycleStage.REVIEWED},
    AgentLifecycleStage.REVIEWED: {AgentLifecycleStage.STAGING},
    AgentLifecycleStage.STAGING: {
        AgentLifecycleStage.PRODUCTION,
        AgentLifecycleStage.REVIEWED,
    },
    AgentLifecycleStage.PRODUCTION: {
        AgentLifecycleStage.STAGING,
        AgentLifecycleStage.RETIRED,
    },
    AgentLifecycleStage.RETIRED: set(),
}
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_MAX_TRACE_PAYLOAD_BYTES = 256_000


@dataclass(frozen=True, slots=True)
class RegisteredAgent:
    manifest: AgentManifest
    stage: AgentLifecycleStage
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TraceEvent:
    run_id: str
    agent_id: str
    version: str
    event_type: str
    payload: Mapping[str, Any]
    parent_event_id: str | None = None
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    sequence_number: int | None = None
    id: str = ""
    created_at: str = ""

    def normalized(self) -> "TraceEvent":
        return TraceEvent(
            run_id=self.run_id.strip(),
            agent_id=self.agent_id.strip(),
            version=self.version.strip(),
            event_type=self.event_type.strip(),
            payload=redact_data(dict(self.payload)),
            parent_event_id=(
                self.parent_event_id.strip() if self.parent_event_id else None
            ),
            duration_ms=self.duration_ms,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
            sequence_number=self.sequence_number,
            id=self.id or str(uuid4()),
            created_at=self.created_at or utc_now_iso(),
        )


class AgentRegistry:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self._initialize()

    def _initialize(self) -> None:
        with self.store.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_manifests (
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    lifecycle_stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agent_id, version)
                );
                CREATE TABLE IF NOT EXISTS agent_trace_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence_number INTEGER,
                    parent_event_id TEXT,
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    duration_ms INTEGER,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cost_usd TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (agent_id, version)
                        REFERENCES agent_manifests(agent_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_manifest_stage
                    ON agent_manifests(lifecycle_stage, updated_at);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(agent_trace_events)"
                ).fetchall()
            }
            for column, definition in {
                "sequence_number": "INTEGER",
                "parent_event_id": "TEXT",
                "input_tokens": "INTEGER",
                "output_tokens": "INTEGER",
            }.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE agent_trace_events "
                        f"ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_trace_run_created
                ON agent_trace_events(run_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_trace_order
                ON agent_trace_events(run_id, sequence_number, created_at)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_trace_sequence
                ON agent_trace_events(run_id, sequence_number)
                WHERE sequence_number IS NOT NULL
                """
            )

    def register(
        self,
        manifest: AgentManifest,
        *,
        actor: str,
    ) -> RegisteredAgent:
        manifest.validate()
        if not actor.strip():
            raise AgentRegistryError("registration actor is required")
        now = utc_now_iso()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM agent_manifests
                WHERE agent_id = ? AND version = ?
                """,
                (manifest.agent_id, manifest.version),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != manifest.fingerprint:
                    raise AgentRegistryError(
                        "agent versions are immutable; publish a new version"
                    )
                return self._registered_from_row(existing)

            connection.execute(
                """
                INSERT INTO agent_manifests (
                    agent_id, version, fingerprint, manifest_json,
                    lifecycle_stage, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest.agent_id,
                    manifest.version,
                    manifest.fingerprint,
                    manifest.canonical_json(),
                    AgentLifecycleStage.DRAFT.value,
                    now,
                    now,
                ),
            )
            SQLiteStore._insert_audit(
                connection,
                AuditEvent(
                    event_type="agent_manifest_registered",
                    actor=actor,
                    action="register",
                    resource_type="agent_manifest",
                    resource_id=f"{manifest.agent_id}:{manifest.version}",
                    details={"fingerprint": manifest.fingerprint},
                    severity=manifest.risk_level,
                ),
            )
        return self.get(manifest.agent_id, manifest.version)

    def get(self, agent_id: str, version: str) -> RegisteredAgent:
        with self.store.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_manifests
                WHERE agent_id = ? AND version = ?
                """,
                (agent_id, version),
            ).fetchone()
        if row is None:
            raise AgentRegistryError(f"agent not found: {agent_id}:{version}")
        return self._registered_from_row(row)

    def list_agents(
        self,
        *,
        stage: AgentLifecycleStage | None = None,
    ) -> tuple[RegisteredAgent, ...]:
        query = "SELECT * FROM agent_manifests"
        params: tuple[Any, ...] = ()
        if stage is not None:
            query += " WHERE lifecycle_stage = ?"
            params = (stage.value,)
        query += " ORDER BY agent_id, version"
        with self.store.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._registered_from_row(row) for row in rows)

    def transition_stage(
        self,
        *,
        agent_id: str,
        version: str,
        expected_stage: AgentLifecycleStage,
        new_stage: AgentLifecycleStage,
        actor: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> RegisteredAgent:
        if not actor.strip():
            raise AgentRegistryError("lifecycle actor is required")
        if (
            expected_stage
            in {AgentLifecycleStage.PRODUCTION, AgentLifecycleStage.RETIRED}
            or new_stage
            in {AgentLifecycleStage.PRODUCTION, AgentLifecycleStage.RETIRED}
        ):
            raise AgentRegistryError(
                "production and retirement transitions require a governed release workflow"
            )
        allowed = _ALLOWED_STAGE_TRANSITIONS[expected_stage]
        if new_stage not in allowed:
            raise AgentRegistryError(
                f"invalid lifecycle transition: {expected_stage.value} -> "
                f"{new_stage.value}"
            )
        now = utc_now_iso()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE agent_manifests
                SET lifecycle_stage = ?, updated_at = ?
                WHERE agent_id = ? AND version = ? AND lifecycle_stage = ?
                """,
                (
                    new_stage.value,
                    now,
                    agent_id,
                    version,
                    expected_stage.value,
                ),
            )
            if cursor.rowcount != 1:
                current = connection.execute(
                    """
                    SELECT lifecycle_stage FROM agent_manifests
                    WHERE agent_id = ? AND version = ?
                    """,
                    (agent_id, version),
                ).fetchone()
                if current is None:
                    raise AgentRegistryError(
                        f"agent not found: {agent_id}:{version}"
                    )
                raise AgentRegistryError(
                    f"agent is currently {current['lifecycle_stage']}, not "
                    f"{expected_stage.value}"
                )
            SQLiteStore._insert_audit(
                connection,
                AuditEvent(
                    event_type="agent_lifecycle_transition",
                    actor=actor,
                    action=new_stage.value,
                    resource_type="agent_manifest",
                    resource_id=f"{agent_id}:{version}",
                    details={
                        "previous_stage": expected_stage.value,
                        "evidence": redact_data(dict(evidence or {})),
                    },
                    severity=Severity.HIGH
                    if new_stage is AgentLifecycleStage.PRODUCTION
                    else Severity.MEDIUM,
                ),
            )
        return self.get(agent_id, version)

    def record_trace(self, event: TraceEvent) -> TraceEvent:
        normalized = event.normalized()
        if not normalized.run_id or len(normalized.run_id) > 128:
            raise AgentRegistryError("trace run_id must be 1-128 characters")
        if not normalized.agent_id or not normalized.version:
            raise AgentRegistryError("trace agent_id and version are required")
        if not _EVENT_TYPE.fullmatch(normalized.event_type):
            raise AgentRegistryError("trace event_type has an invalid format")
        for name, value in (
            ("duration_ms", normalized.duration_ms),
            ("input_tokens", normalized.input_tokens),
            ("output_tokens", normalized.output_tokens),
        ):
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise AgentRegistryError(f"trace {name} must be a non-negative integer")
        if normalized.cost_usd is not None:
            try:
                cost = Decimal(normalized.cost_usd)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise AgentRegistryError("trace cost_usd must be numeric") from exc
            if not cost.is_finite() or cost < 0:
                raise AgentRegistryError(
                    "trace cost_usd must be finite and non-negative"
                )
        try:
            payload_json = json.dumps(normalized.payload, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise AgentRegistryError(
                "trace payload must be JSON serializable"
            ) from exc
        if len(payload_json.encode("utf-8")) > _MAX_TRACE_PAYLOAD_BYTES:
            raise AgentRegistryError("trace payload exceeds the size limit")

        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            manifest = connection.execute(
                """
                SELECT 1 FROM agent_manifests
                WHERE agent_id = ? AND version = ?
                """,
                (normalized.agent_id, normalized.version),
            ).fetchone()
            if manifest is None:
                raise AgentRegistryError(
                    f"agent not found: {normalized.agent_id}:{normalized.version}"
                )
            if normalized.parent_event_id:
                parent = connection.execute(
                    """
                    SELECT run_id FROM agent_trace_events WHERE id = ?
                    """,
                    (normalized.parent_event_id,),
                ).fetchone()
                if parent is None:
                    raise AgentRegistryError("trace parent_event_id was not found")
                if parent["run_id"] != normalized.run_id:
                    raise AgentRegistryError(
                        "trace parent_event_id belongs to a different run"
                    )
            next_sequence = connection.execute(
                """
                SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence
                FROM agent_trace_events WHERE run_id = ?
                """,
                (normalized.run_id,),
            ).fetchone()["next_sequence"]
            stored = replace(
                normalized,
                sequence_number=int(next_sequence),
            )
            connection.execute(
                """
                INSERT INTO agent_trace_events (
                    id, run_id, sequence_number, parent_event_id,
                    agent_id, version, event_type, payload_json, duration_ms,
                    input_tokens, output_tokens, cost_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.id,
                    stored.run_id,
                    stored.sequence_number,
                    stored.parent_event_id,
                    stored.agent_id,
                    stored.version,
                    stored.event_type,
                    payload_json,
                    stored.duration_ms,
                    stored.input_tokens,
                    stored.output_tokens,
                    str(stored.cost_usd)
                    if stored.cost_usd is not None
                    else None,
                    stored.created_at,
                ),
            )
        return stored

    def list_trace_events(self, run_id: str) -> tuple[TraceEvent, ...]:
        with self.store.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_trace_events
                WHERE run_id = ?
                ORDER BY sequence_number, created_at, id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            TraceEvent(
                id=row["id"],
                run_id=row["run_id"],
                sequence_number=row["sequence_number"],
                parent_event_id=row["parent_event_id"],
                agent_id=row["agent_id"],
                version=row["version"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                duration_ms=row["duration_ms"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cost_usd=Decimal(row["cost_usd"])
                if row["cost_usd"] is not None
                else None,
                created_at=row["created_at"],
            )
            for row in rows
        )

    @staticmethod
    def _registered_from_row(row: Any) -> RegisteredAgent:
        return RegisteredAgent(
            manifest=AgentManifest.from_mapping(json.loads(row["manifest_json"])),
            stage=AgentLifecycleStage(row["lifecycle_stage"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
