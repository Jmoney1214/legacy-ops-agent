from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4

from .agent_manifest import AgentManifest
from .agent_registry import AgentLifecycleStage, AgentRegistry
from .control_plane import ApprovalService, SQLiteStore, redact_data
from .domain import ApprovalStatus, AuditEvent, Severity, utc_now_iso
from .tool_registry import ToolRegistry


class AgentBuildError(ValueError):
    pass


class BuildGate(StrEnum):
    CONTRACT_VALIDATION = "contract_validation"
    TOOL_VALIDATION = "tool_validation"
    GUARDRAIL_VALIDATION = "guardrail_validation"
    UNIT_TESTS = "unit_tests"
    AGENT_EVALS = "agent_evals"
    CODE_REVIEW = "code_review"
    STAGING_VALIDATION = "staging_validation"
    PRODUCTION_RELEASE = "production_release"


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class BuildStatus(StrEnum):
    ACTIVE = "active"
    FAILED = "failed"
    STAGING_READY = "staging_ready"
    RELEASED = "released"


_GATE_ORDER = (
    BuildGate.CONTRACT_VALIDATION,
    BuildGate.TOOL_VALIDATION,
    BuildGate.GUARDRAIL_VALIDATION,
    BuildGate.UNIT_TESTS,
    BuildGate.AGENT_EVALS,
    BuildGate.CODE_REVIEW,
    BuildGate.STAGING_VALIDATION,
    BuildGate.PRODUCTION_RELEASE,
)


@dataclass(frozen=True, slots=True)
class BuildRecord:
    build_id: str
    agent_id: str
    version: str
    status: BuildStatus
    next_gate: BuildGate | None
    gate_results: Mapping[str, Any]
    created_at: str
    updated_at: str


class AgentBuildPipeline:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        registry: AgentRegistry,
        tool_registry: ToolRegistry,
        approvals: ApprovalService,
    ):
        self.store = store
        self.registry = registry
        self.tool_registry = tool_registry
        self.approvals = approvals
        self._initialize()

    def _initialize(self) -> None:
        with self.store.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_builds (
                    build_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_gate TEXT,
                    gate_results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (agent_id, version)
                        REFERENCES agent_manifests(agent_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_build_status
                    ON agent_builds(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_agent_build_agent
                    ON agent_builds(agent_id, version, created_at);
                """
            )

    def start(self, manifest: AgentManifest, *, actor: str) -> BuildRecord:
        self.registry.register(manifest, actor=actor)
        build_id = str(uuid4())
        now = utc_now_iso()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO agent_builds (
                    build_id, agent_id, version, status, next_gate,
                    gate_results_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    build_id,
                    manifest.agent_id,
                    manifest.version,
                    BuildStatus.ACTIVE.value,
                    BuildGate.CONTRACT_VALIDATION.value,
                    "{}",
                    now,
                    now,
                ),
            )
            SQLiteStore._insert_audit(
                connection,
                AuditEvent(
                    event_type="agent_build_started",
                    actor=actor,
                    action="start",
                    resource_type="agent_build",
                    resource_id=build_id,
                    details={
                        "agent_id": manifest.agent_id,
                        "version": manifest.version,
                        "fingerprint": manifest.fingerprint,
                    },
                    severity=manifest.risk_level,
                ),
            )
        return self.get(build_id)

    def get(self, build_id: str) -> BuildRecord:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_builds WHERE build_id = ?", (build_id,)
            ).fetchone()
        if row is None:
            raise AgentBuildError(f"agent build not found: {build_id}")
        return self._from_row(row)

    def record_gate(
        self,
        *,
        build_id: str,
        gate: BuildGate,
        status: GateStatus,
        evidence: Mapping[str, Any],
        actor: str,
    ) -> BuildRecord:
        if gate is BuildGate.PRODUCTION_RELEASE:
            raise AgentBuildError(
                "production release must use release_to_production"
            )
        build = self.get(build_id)
        if build.status is not BuildStatus.ACTIVE:
            raise AgentBuildError(
                f"cannot record a gate for build status {build.status.value}"
            )
        if build.next_gate is not gate:
            expected = build.next_gate.value if build.next_gate else "none"
            raise AgentBuildError(
                f"gate order violation: expected {expected}, received {gate.value}"
            )
        agent = self.registry.get(build.agent_id, build.version)
        self._run_gate_checks(gate, status, evidence, agent.manifest)

        results = dict(build.gate_results)
        results[gate.value] = {
            "status": status.value,
            "evidence": redact_data(dict(evidence)),
            "recorded_by": actor,
            "recorded_at": utc_now_iso(),
        }
        if status is GateStatus.FAILED:
            next_gate = None
            build_status = BuildStatus.FAILED
        else:
            next_gate = _GATE_ORDER[_GATE_ORDER.index(gate) + 1]
            build_status = (
                BuildStatus.STAGING_READY
                if gate is BuildGate.STAGING_VALIDATION
                else BuildStatus.ACTIVE
            )

        now = utc_now_iso()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM agent_builds WHERE build_id = ?", (build_id,)
            ).fetchone()
            if current is None:
                raise AgentBuildError(f"agent build not found: {build_id}")
            if current["next_gate"] != gate.value:
                raise AgentBuildError("build gate changed concurrently")

            connection.execute(
                """
                UPDATE agent_builds
                SET status = ?, next_gate = ?, gate_results_json = ?, updated_at = ?
                WHERE build_id = ?
                """,
                (
                    build_status.value,
                    next_gate.value if next_gate else None,
                    json.dumps(results, sort_keys=True),
                    now,
                    build_id,
                ),
            )
            self._apply_lifecycle_for_gate(
                connection=connection,
                gate=gate,
                status=status,
                agent_id=build.agent_id,
                version=build.version,
                actor=actor,
                evidence=evidence,
            )
            SQLiteStore._insert_audit(
                connection,
                AuditEvent(
                    event_type="agent_build_gate_recorded",
                    actor=actor,
                    action=status.value,
                    resource_type="agent_build",
                    resource_id=build_id,
                    details={
                        "gate": gate.value,
                        "agent_id": build.agent_id,
                        "version": build.version,
                    },
                    severity=(
                        Severity.HIGH
                        if gate in {
                            BuildGate.CODE_REVIEW,
                            BuildGate.STAGING_VALIDATION,
                        }
                        else Severity.MEDIUM
                    ),
                ),
            )
        return self.get(build_id)

    def request_production_release(
        self, *, build_id: str, requested_by: str
    ) -> str:
        build = self.get(build_id)
        if build.status is not BuildStatus.STAGING_READY:
            raise AgentBuildError("build must pass staging validation first")
        approval = self.approvals.request(
            action_type="deploy_agent_production",
            proposed_action=(
                f"Deploy agent {build.agent_id} version {build.version} "
                f"from build {build.build_id} to production"
            ),
            risk_level=Severity.HIGH,
            requested_by_agent=requested_by,
            payload={
                "build_id": build.build_id,
                "agent_id": build.agent_id,
                "version": build.version,
            },
        )
        return approval.id

    def release_to_production(
        self,
        *,
        build_id: str,
        approval_id: str,
        actor: str,
        deployment_evidence: Mapping[str, Any],
    ) -> BuildRecord:
        build = self.get(build_id)
        if build.status is not BuildStatus.STAGING_READY:
            raise AgentBuildError("build is not ready for production release")
        if build.next_gate is not BuildGate.PRODUCTION_RELEASE:
            raise AgentBuildError("production release is not the next build gate")
        if not deployment_evidence.get("deployment_id"):
            raise AgentBuildError("deployment evidence requires deployment_id")
        if deployment_evidence.get("health_check") != "passed":
            raise AgentBuildError("production health_check must be passed")

        now = utc_now_iso()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approval_row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if approval_row is None:
                raise AgentBuildError(f"approval not found: {approval_id}")
            if approval_row["status"] != ApprovalStatus.APPROVED.value:
                raise AgentBuildError("production release requires approved status")
            if approval_row["action_type"] != "deploy_agent_production":
                raise AgentBuildError("approval does not authorize agent deployment")
            approval_payload = json.loads(approval_row["payload_json"])
            expected = {
                "build_id": build.build_id,
                "agent_id": build.agent_id,
                "version": build.version,
            }
            if any(str(approval_payload.get(key)) != value for key, value in expected.items()):
                raise AgentBuildError("approval payload does not match this build")

            build_row = connection.execute(
                "SELECT * FROM agent_builds WHERE build_id = ?", (build_id,)
            ).fetchone()
            if (
                build_row is None
                or build_row["status"] != BuildStatus.STAGING_READY.value
                or build_row["next_gate"] != BuildGate.PRODUCTION_RELEASE.value
            ):
                raise AgentBuildError("build changed before production release")

            manifest_cursor = connection.execute(
                """
                UPDATE agent_manifests
                SET lifecycle_stage = ?, updated_at = ?
                WHERE agent_id = ? AND version = ? AND lifecycle_stage = ?
                """,
                (
                    AgentLifecycleStage.PRODUCTION.value,
                    now,
                    build.agent_id,
                    build.version,
                    AgentLifecycleStage.STAGING.value,
                ),
            )
            if manifest_cursor.rowcount != 1:
                raise AgentBuildError("agent is not currently in staging")

            results = json.loads(build_row["gate_results_json"])
            results[BuildGate.PRODUCTION_RELEASE.value] = {
                "status": GateStatus.PASSED.value,
                "evidence": redact_data(dict(deployment_evidence)),
                "approval_id": approval_id,
                "recorded_by": actor,
                "recorded_at": now,
            }
            connection.execute(
                """
                UPDATE agent_builds
                SET status = ?, next_gate = NULL, gate_results_json = ?, updated_at = ?
                WHERE build_id = ?
                """,
                (
                    BuildStatus.RELEASED.value,
                    json.dumps(results, sort_keys=True),
                    now,
                    build_id,
                ),
            )
            approval_cursor = connection.execute(
                """
                UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?
                WHERE id = ? AND status = ?
                """,
                (
                    ApprovalStatus.EXECUTED.value,
                    now,
                    actor,
                    approval_id,
                    ApprovalStatus.APPROVED.value,
                ),
            )
            if approval_cursor.rowcount != 1:
                raise AgentBuildError("approval execution lost a concurrent update")

            for event in (
                AuditEvent(
                    event_type="agent_lifecycle_transition",
                    actor=actor,
                    action=AgentLifecycleStage.PRODUCTION.value,
                    resource_type="agent_manifest",
                    resource_id=f"{build.agent_id}:{build.version}",
                    details={
                        "previous_stage": AgentLifecycleStage.STAGING.value,
                        "build_id": build_id,
                    },
                    severity=Severity.HIGH,
                ),
                AuditEvent(
                    event_type="agent_production_release",
                    actor=actor,
                    action="released",
                    resource_type="agent_build",
                    resource_id=build_id,
                    details={
                        "approval_id": approval_id,
                        "deployment": redact_data(dict(deployment_evidence)),
                    },
                    severity=Severity.HIGH,
                ),
                AuditEvent(
                    event_type="approved_action_executed",
                    actor=actor,
                    action="deploy_agent_production",
                    resource_type="approval",
                    resource_id=approval_id,
                    details={"build_id": build_id},
                    severity=Severity.HIGH,
                ),
            ):
                SQLiteStore._insert_audit(connection, event)
        return self.get(build_id)

    def _run_gate_checks(
        self,
        gate: BuildGate,
        status: GateStatus,
        evidence: Mapping[str, Any],
        manifest: AgentManifest,
    ) -> None:
        if status is GateStatus.FAILED:
            if not str(evidence.get("failure_reason") or "").strip():
                raise AgentBuildError("failed gates require failure_reason evidence")
            return
        if gate is BuildGate.CONTRACT_VALIDATION:
            manifest.validate()
        elif gate is BuildGate.TOOL_VALIDATION:
            self.tool_registry.validate_manifest(manifest)
        elif gate is BuildGate.GUARDRAIL_VALIDATION:
            required = {"permission_review", "approval_review", "data_review"}
            if any(evidence.get(key) != "passed" for key in required):
                raise AgentBuildError(
                    "guardrail validation requires passed permission, approval, and data reviews"
                )
        elif gate is BuildGate.UNIT_TESTS:
            if evidence.get("result") != "passed" or not evidence.get("command"):
                raise AgentBuildError("unit tests require command and passed result")
        elif gate is BuildGate.AGENT_EVALS:
            total = int(evidence.get("cases_total", 0))
            failed = int(evidence.get("cases_failed", -1))
            if total < 1 or failed != 0:
                raise AgentBuildError("agent evals require at least one case and zero failures")
        elif gate is BuildGate.CODE_REVIEW:
            if not evidence.get("reviewer") or evidence.get("findings_resolved") is not True:
                raise AgentBuildError(
                    "code review requires reviewer and findings_resolved=true"
                )
        elif gate is BuildGate.STAGING_VALIDATION:
            if evidence.get("smoke_test") != "passed" or not evidence.get("environment"):
                raise AgentBuildError(
                    "staging validation requires environment and passed smoke_test"
                )

    def _apply_lifecycle_for_gate(
        self,
        *,
        connection: Any,
        gate: BuildGate,
        status: GateStatus,
        agent_id: str,
        version: str,
        actor: str,
        evidence: Mapping[str, Any],
    ) -> None:
        if status is not GateStatus.PASSED:
            return
        transition = None
        if gate is BuildGate.GUARDRAIL_VALIDATION:
            transition = (AgentLifecycleStage.DRAFT, AgentLifecycleStage.VALIDATED)
        elif gate is BuildGate.CODE_REVIEW:
            transition = (AgentLifecycleStage.VALIDATED, AgentLifecycleStage.REVIEWED)
        elif gate is BuildGate.STAGING_VALIDATION:
            transition = (AgentLifecycleStage.REVIEWED, AgentLifecycleStage.STAGING)
        if transition is None:
            return
        expected, new = transition
        cursor = connection.execute(
            """
            UPDATE agent_manifests
            SET lifecycle_stage = ?, updated_at = ?
            WHERE agent_id = ? AND version = ? AND lifecycle_stage = ?
            """,
            (new.value, utc_now_iso(), agent_id, version, expected.value),
        )
        if cursor.rowcount != 1:
            raise AgentBuildError(
                f"agent lifecycle is not ready for {new.value} transition"
            )
        SQLiteStore._insert_audit(
            connection,
            AuditEvent(
                event_type="agent_lifecycle_transition",
                actor=actor,
                action=new.value,
                resource_type="agent_manifest",
                resource_id=f"{agent_id}:{version}",
                details={
                    "previous_stage": expected.value,
                    "gate": gate.value,
                    "evidence": redact_data(dict(evidence)),
                },
                severity=Severity.MEDIUM,
            ),
        )

    @staticmethod
    def _from_row(row: Any) -> BuildRecord:
        return BuildRecord(
            build_id=row["build_id"],
            agent_id=row["agent_id"],
            version=row["version"],
            status=BuildStatus(row["status"]),
            next_gate=BuildGate(row["next_gate"]) if row["next_gate"] else None,
            gate_results=json.loads(row["gate_results_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
