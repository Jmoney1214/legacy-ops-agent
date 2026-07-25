from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from legacy_ops.agent_build_pipeline import (
    AgentBuildError,
    AgentBuildPipeline,
    BuildGate,
    BuildStatus,
    GateStatus,
)
from legacy_ops.agent_manifest import AgentManifest, LoopPolicy, ManifestError, ToolPermission
from legacy_ops.agent_workspace import scaffold_agent
from legacy_ops.agent_registry import (
    AgentLifecycleStage,
    AgentRegistry,
    AgentRegistryError,
    TraceEvent,
)
from legacy_ops.control_plane import ApprovalService, SQLiteStore
from legacy_ops.domain import ApprovalStatus, Severity
from legacy_ops.loop_controller import LoopController, LoopError, StopReason
ARTIFACT_DIGEST = "a" * 64


from legacy_ops.tool_registry import (
    SideEffectLevel,
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
)


def manifest_payload(**overrides):
    payload = {
        "agent_id": "chargeback_agent",
        "version": "1.0.0",
        "display_name": "Chargeback Agent",
        "purpose": "Prepare verified chargeback cases and approval packages.",
        "owner": "risk_operations",
        "risk_level": "high",
        "instructions_path": "agents/chargeback_agent/instructions.md",
        "tools": ["read_sales", "submit_dispute"],
        "permissions": {
            "read_sales": ["read"],
            "submit_dispute": ["execute"],
        },
        "approval_actions": ["file_chargeback_dispute"],
        "execution_mode": "human_in_the_loop",
        "loop_policy": {
            "max_iterations": 6,
            "max_tool_calls": 12,
            "max_runtime_seconds": 120,
            "max_cost_usd": "1.50",
            "max_consecutive_failures": 2,
        },
        "context_policy": {
            "max_items": 25,
            "max_characters": 40000,
            "allowed_sources": ["gmail", "lightspeed"],
            "require_source_attribution": True,
        },
        "memory_policy": {
            "read_scopes": ["working", "episodic"],
            "write_scopes": ["episodic"],
            "retention_days": 365,
        },
        "tags": ["risk", "chargeback"],
    }
    payload.update(overrides)
    return payload


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="read_sales",
            description="Read candidate sales from the point-of-sale system.",
            input_schema={
                "type": "object",
                "properties": {"transaction_id": {"type": "string"}},
                "required": ["transaction_id"],
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        ToolSpec(
            name="submit_dispute",
            description="Submit an approved chargeback dispute to MerchantOS.",
            input_schema={
                "type": "object",
                "properties": {"case_id": {"type": "string"}},
                "required": ["case_id"],
                "additionalProperties": False,
            },
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            minimum_risk_level=Severity.HIGH,
            required_approval_action="file_chargeback_dispute",
        )
    )
    return registry


class ManifestTests(unittest.TestCase):
    def test_high_risk_agent_cannot_be_human_out_of_loop(self):
        with self.assertRaises(ManifestError):
            AgentManifest.from_mapping(manifest_payload(execution_mode="human_out_of_the_loop"))

    def test_side_effect_permissions_require_approval_actions(self):
        with self.assertRaises(ManifestError):
            AgentManifest.from_mapping(manifest_payload(approval_actions=[]))

    def test_procedural_memory_cannot_be_written(self):
        with self.assertRaises(ManifestError):
            AgentManifest.from_mapping(manifest_payload(memory_policy={
                "read_scopes": ["procedural"],
                "write_scopes": ["procedural"],
                "retention_days": 365,
            }))

    def test_manifest_fingerprint_stable_and_round_trips(self):
        first = AgentManifest.from_mapping(manifest_payload())
        second = AgentManifest.from_mapping(first.to_dict())
        self.assertEqual(first.fingerprint, second.fingerprint)


class ToolRegistryTests(unittest.TestCase):
    def test_irreversible_tool_requires_approval(self):
        manifest = AgentManifest.from_mapping(manifest_payload())
        tools = build_tool_registry()
        denied = tools.authorize(
            manifest=manifest,
            tool_name="submit_dispute",
            permission=ToolPermission.EXECUTE,
            approval_action="file_chargeback_dispute",
            approval_granted=False,
        )
        self.assertFalse(denied.allowed)
        self.assertTrue(denied.requires_approval)
        allowed = tools.authorize(
            manifest=manifest,
            tool_name="submit_dispute",
            permission=ToolPermission.EXECUTE,
            approval_action="file_chargeback_dispute",
            approval_granted=True,
        )
        self.assertTrue(allowed.allowed)

    def test_tool_input_rejects_unknown_fields(self):
        with self.assertRaises(ToolRegistryError):
            build_tool_registry().validate_input(
                "read_sales",
                {"transaction_id": "500", "access_token": "do-not-accept"},
            )

    def test_read_only_tool_cannot_have_execute_permission(self):
        payload = manifest_payload()
        payload["permissions"] = {
            **payload["permissions"],
            "read_sales": ["read", "execute"],
        }
        manifest = AgentManifest.from_mapping(payload)
        with self.assertRaises(ToolRegistryError):
            build_tool_registry().validate_manifest(manifest)


    def test_wrong_approval_action_cannot_authorize_tool(self):
        manifest = AgentManifest.from_mapping(manifest_payload())
        decision = build_tool_registry().authorize(
            manifest=manifest,
            tool_name="submit_dispute",
            permission=ToolPermission.EXECUTE,
            approval_action="send_email",
            approval_granted=True,
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_nested_schema_validation_and_output_validation(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="nested_tool",
                description="Validate nested structured tool data safely.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "order": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "quantity": {
                                                "type": "integer",
                                                "minimum": 1,
                                            }
                                        },
                                        "required": ["quantity"],
                                        "additionalProperties": False,
                                    },
                                    "minItems": 1,
                                },
                            },
                            "required": ["id", "items"],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["order"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["ok", "error"],
                        }
                    },
                    "required": ["status"],
                    "additionalProperties": False,
                },
            )
        )
        registry.validate_input(
            "nested_tool",
            {"order": {"id": "A1", "items": [{"quantity": 2}]}},
        )
        registry.validate_output("nested_tool", {"status": "ok"})
        with self.assertRaises(ToolRegistryError):
            registry.validate_input(
                "nested_tool",
                {"order": {"id": "A1", "items": [{"quantity": 0}]}},
            )
        with self.assertRaises(ToolRegistryError):
            registry.validate_output("nested_tool", {"status": "unknown"})

    def test_agent_risk_must_cover_tool_risk(self):
        payload = manifest_payload(risk_level="medium")
        manifest = AgentManifest.from_mapping(payload)
        with self.assertRaises(ToolRegistryError):
            build_tool_registry().validate_manifest(manifest)


class LoopControllerTests(unittest.TestCase):
    def test_repeated_failure_stops_loop(self):
        controller = LoopController(LoopPolicy(
            max_iterations=8,
            max_tool_calls=20,
            max_runtime_seconds=60,
            max_cost_usd=Decimal("2.00"),
            max_consecutive_failures=2,
        ))
        controller.before_iteration()
        controller.record_failure("timeout")
        self.assertFalse(controller.should_stop)
        controller.record_failure("timeout")
        self.assertTrue(controller.should_stop)
        self.assertEqual(controller.state.stop_reason, StopReason.REPEATED_FAILURE)

    def test_max_iterations_are_enforced(self):
        controller = LoopController(LoopPolicy(
            max_iterations=1,
            max_tool_calls=20,
            max_runtime_seconds=60,
            max_cost_usd=Decimal("2.00"),
            max_consecutive_failures=2,
        ))
        controller.before_iteration()
        self.assertTrue(controller.should_stop)
        self.assertEqual(controller.state.stop_reason, StopReason.MAX_ITERATIONS)

    def test_zero_tool_budget_allows_reasoning_but_blocks_tool(self):
        controller = LoopController(LoopPolicy(
            max_iterations=2,
            max_tool_calls=0,
            max_runtime_seconds=60,
            max_cost_usd=Decimal("2.00"),
            max_consecutive_failures=2,
        ))
        controller.before_iteration()
        with self.assertRaises(LoopError):
            controller.before_tool_call()
        self.assertEqual(controller.state.stop_reason, StopReason.MAX_TOOL_CALLS)


class AgentRegistryTests(unittest.TestCase):
    def test_versions_are_immutable_and_traces_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            registry = AgentRegistry(store)
            manifest = AgentManifest.from_mapping(manifest_payload())
            registry.register(manifest, actor="test")
            changed = AgentManifest.from_mapping(
                manifest_payload(purpose="A materially changed purpose for the same version.")
            )
            with self.assertRaises(AgentRegistryError):
                registry.register(changed, actor="test")
            registry.record_trace(TraceEvent(
                run_id="run-1",
                agent_id=manifest.agent_id,
                version=manifest.version,
                event_type="tool_call",
                payload={"api_key": "secret-value", "case_id": "CB-1"},
                duration_ms=15,
                cost_usd=Decimal("0.01"),
            ))
            event = registry.list_trace_events("run-1")[0]
            self.assertEqual(event.payload["api_key"], "[REDACTED]")
            self.assertEqual(event.payload["case_id"], "CB-1")

    def test_invalid_stage_jump_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            registry = AgentRegistry(store)
            manifest = AgentManifest.from_mapping(manifest_payload())
            registry.register(manifest, actor="test")
            with self.assertRaises(AgentRegistryError):
                registry.transition_stage(
                    agent_id=manifest.agent_id,
                    version=manifest.version,
                    expected_stage=AgentLifecycleStage.DRAFT,
                    new_stage=AgentLifecycleStage.PRODUCTION,
                    actor="test",
                )

    def test_registry_cannot_bypass_governed_production_release(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            registry = AgentRegistry(store)
            manifest = AgentManifest.from_mapping(manifest_payload())
            registry.register(manifest, actor="test")
            registry.transition_stage(
                agent_id=manifest.agent_id,
                version=manifest.version,
                expected_stage=AgentLifecycleStage.DRAFT,
                new_stage=AgentLifecycleStage.VALIDATED,
                actor="test",
            )
            registry.transition_stage(
                agent_id=manifest.agent_id,
                version=manifest.version,
                expected_stage=AgentLifecycleStage.VALIDATED,
                new_stage=AgentLifecycleStage.REVIEWED,
                actor="test",
            )
            registry.transition_stage(
                agent_id=manifest.agent_id,
                version=manifest.version,
                expected_stage=AgentLifecycleStage.REVIEWED,
                new_stage=AgentLifecycleStage.STAGING,
                actor="test",
            )
            with self.assertRaises(AgentRegistryError):
                registry.transition_stage(
                    agent_id=manifest.agent_id,
                    version=manifest.version,
                    expected_stage=AgentLifecycleStage.STAGING,
                    new_stage=AgentLifecycleStage.PRODUCTION,
                    actor="test",
                )


    def test_trace_sequence_and_parent_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            registry = AgentRegistry(store)
            manifest = AgentManifest.from_mapping(manifest_payload())
            registry.register(manifest, actor="test")
            parent = registry.record_trace(
                TraceEvent(
                    run_id="run-1",
                    agent_id=manifest.agent_id,
                    version=manifest.version,
                    event_type="run.started",
                    payload={"status": "started"},
                    input_tokens=10,
                )
            )
            child = registry.record_trace(
                TraceEvent(
                    run_id="run-1",
                    parent_event_id=parent.id,
                    agent_id=manifest.agent_id,
                    version=manifest.version,
                    event_type="tool.called",
                    payload={"tool": "read_sales"},
                    output_tokens=5,
                )
            )
            self.assertEqual(parent.sequence_number, 1)
            self.assertEqual(child.sequence_number, 2)
            self.assertEqual(
                [event.id for event in registry.list_trace_events("run-1")],
                [parent.id, child.id],
            )
            with self.assertRaises(AgentRegistryError):
                registry.record_trace(
                    TraceEvent(
                        run_id="run-2",
                        parent_event_id=parent.id,
                        agent_id=manifest.agent_id,
                        version=manifest.version,
                        event_type="tool.called",
                        payload={},
                    )
                )

    def test_trace_rejects_invalid_metrics_and_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            registry = AgentRegistry(store)
            manifest = AgentManifest.from_mapping(manifest_payload())
            registry.register(manifest, actor="test")
            with self.assertRaises(AgentRegistryError):
                registry.record_trace(
                    TraceEvent(
                        run_id="run-1",
                        agent_id=manifest.agent_id,
                        version=manifest.version,
                        event_type="bad event",
                        payload={},
                    )
                )
            with self.assertRaises(AgentRegistryError):
                registry.record_trace(
                    TraceEvent(
                        run_id="run-1",
                        agent_id=manifest.agent_id,
                        version=manifest.version,
                        event_type="run.metric",
                        payload={"not_json": object()},
                    )
                )
            with self.assertRaises(AgentRegistryError):
                registry.record_trace(
                    TraceEvent(
                        run_id="run-1",
                        agent_id=manifest.agent_id,
                        version=manifest.version,
                        event_type="run.metric",
                        payload={},
                        input_tokens=-1,
                    )
                )


    def test_trace_schema_migrates_from_initial_phase_one_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            with store.connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE agent_trace_events (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        version TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        duration_ms INTEGER,
                        cost_usd TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            registry = AgentRegistry(store)
            with store.connection() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(agent_trace_events)"
                    ).fetchall()
                }
            self.assertTrue(
                {
                    "sequence_number",
                    "parent_event_id",
                    "input_tokens",
                    "output_tokens",
                }.issubset(columns)
            )


class AgentBuildPipelineTests(unittest.TestCase):
    def _make(self, directory):
        store = SQLiteStore(Path(directory) / "ops.db")
        approvals = ApprovalService(store)
        registry = AgentRegistry(store)
        tools = build_tool_registry()
        pipeline = AgentBuildPipeline(
            store=store,
            registry=registry,
            tool_registry=tools,
            approvals=approvals,
        )
        return store, approvals, registry, pipeline

    def test_build_schema_migrates_initial_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "ops.db")
            registry = AgentRegistry(store)
            with store.connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE agent_builds (
                        build_id TEXT PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        next_gate TEXT,
                        gate_results_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            AgentBuildPipeline(
                store=store,
                registry=registry,
                tool_registry=build_tool_registry(),
                approvals=ApprovalService(store),
            )
            with store.connection() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(agent_builds)"
                    ).fetchall()
                }
            self.assertTrue(
                {"artifact_digest", "production_approval_id"}.issubset(columns)
            )

    def test_gate_order_and_approved_production_release(self):
        with tempfile.TemporaryDirectory() as directory:
            store, approvals, registry, pipeline = self._make(directory)
            manifest = AgentManifest.from_mapping(manifest_payload())
            build = pipeline.start(manifest, artifact_digest=ARTIFACT_DIGEST, actor="agent_factory")
            with self.assertRaises(AgentBuildError):
                pipeline.record_gate(
                    build_id=build.build_id,
                    gate=BuildGate.UNIT_TESTS,
                    status=GateStatus.PASSED,
                    evidence={"command": "tests", "result": "passed", "artifact_digest": ARTIFACT_DIGEST},
                    actor="ci",
                )
            gates = (
                (BuildGate.CONTRACT_VALIDATION, {"schema": "passed", "manifest_fingerprint": manifest.fingerprint}),
                (BuildGate.TOOL_VALIDATION, {"registry": "passed"}),
                (BuildGate.GUARDRAIL_VALIDATION, {
                    "permission_review": "passed",
                    "approval_review": "passed",
                    "data_review": "passed",
                }),
                (BuildGate.UNIT_TESTS, {
                    "command": "python -m unittest",
                    "result": "passed",
                    "artifact_digest": ARTIFACT_DIGEST,
                }),
                (BuildGate.AGENT_EVALS, {
                    "cases_total": 12,
                    "cases_failed": 0,
                    "artifact_digest": ARTIFACT_DIGEST,
                }),
                (BuildGate.CODE_REVIEW, {
                    "reviewer": "codex",
                    "findings_resolved": True,
                    "artifact_digest": ARTIFACT_DIGEST,
                }),
                (BuildGate.STAGING_VALIDATION, {
                    "environment": "staging",
                    "smoke_test": "passed",
                    "artifact_digest": ARTIFACT_DIGEST,
                }),
            )
            for gate, evidence in gates:
                build = pipeline.record_gate(
                    build_id=build.build_id,
                    gate=gate,
                    status=GateStatus.PASSED,
                    evidence=evidence,
                    actor="test",
                )
            self.assertEqual(build.status, BuildStatus.STAGING_READY)
            self.assertEqual(
                registry.get(manifest.agent_id, manifest.version).stage,
                AgentLifecycleStage.STAGING,
            )
            approval_id = pipeline.request_production_release(
                build_id=build.build_id,
                requested_by="release_agent",
            )
            approvals.decide(
                approval_id,
                approve=True,
                decided_by="owner",
                reason="staging verified",
            )
            released = pipeline.release_to_production(
                build_id=build.build_id,
                approval_id=approval_id,
                actor="deployment_controller",
                deployment_evidence={
                    "deployment_id": "deploy-123",
                    "health_check": "passed",
                    "artifact_digest": ARTIFACT_DIGEST,
                },
            )
            self.assertEqual(released.status, BuildStatus.RELEASED)
            self.assertIsNone(released.next_gate)
            self.assertEqual(
                registry.get(manifest.agent_id, manifest.version).stage,
                AgentLifecycleStage.PRODUCTION,
            )
            approval_record = store.get_approval(approval_id)
            self.assertEqual(approval_record.status, ApprovalStatus.EXECUTED)
            self.assertEqual(approval_record.decided_by, "owner")

    def test_wrong_approval_cannot_release_build(self):
        with tempfile.TemporaryDirectory() as directory:
            store, approvals, registry, pipeline = self._make(directory)
            manifest = AgentManifest.from_mapping(manifest_payload())
            build = pipeline.start(manifest, artifact_digest=ARTIFACT_DIGEST, actor="agent_factory")
            gates = (
                (BuildGate.CONTRACT_VALIDATION, {"schema": "passed", "manifest_fingerprint": manifest.fingerprint}),
                (BuildGate.TOOL_VALIDATION, {"registry": "passed"}),
                (BuildGate.GUARDRAIL_VALIDATION, {
                    "permission_review": "passed",
                    "approval_review": "passed",
                    "data_review": "passed",
                }),
                (BuildGate.UNIT_TESTS, {"command": "tests", "result": "passed", "artifact_digest": ARTIFACT_DIGEST}),
                (BuildGate.AGENT_EVALS, {"cases_total": 1, "cases_failed": 0, "artifact_digest": ARTIFACT_DIGEST}),
                (BuildGate.CODE_REVIEW, {"reviewer": "codex", "findings_resolved": True, "artifact_digest": ARTIFACT_DIGEST}),
                (BuildGate.STAGING_VALIDATION, {"environment": "staging", "smoke_test": "passed", "artifact_digest": ARTIFACT_DIGEST}),
            )
            for gate, evidence in gates:
                build = pipeline.record_gate(
                    build_id=build.build_id,
                    gate=gate,
                    status=GateStatus.PASSED,
                    evidence=evidence,
                    actor="test",
                )
            wrong = approvals.request(
                action_type="deploy_agent_production",
                proposed_action="Wrong build",
                risk_level=Severity.HIGH,
                requested_by_agent="release_agent",
                payload={
                    "build_id": "wrong",
                    "agent_id": manifest.agent_id,
                    "version": manifest.version,
                    "artifact_digest": ARTIFACT_DIGEST,
                },
            )
            approvals.decide(
                wrong.id, approve=True, decided_by="owner", reason="test"
            )
            with self.assertRaises(AgentBuildError):
                pipeline.release_to_production(
                    build_id=build.build_id,
                    approval_id=wrong.id,
                    actor="deployment_controller",
                    deployment_evidence={
                        "deployment_id": "deploy-123",
                        "health_check": "passed",
                    },
                )


    def test_release_approval_request_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store, approvals, registry, pipeline = self._make(directory)
            manifest = AgentManifest.from_mapping(manifest_payload())
            build = pipeline.start(manifest, artifact_digest=ARTIFACT_DIGEST, actor="agent_factory")
            gates = (
                (BuildGate.CONTRACT_VALIDATION, {"schema": "passed", "manifest_fingerprint": manifest.fingerprint}),
                (BuildGate.TOOL_VALIDATION, {"registry": "passed"}),
                (BuildGate.GUARDRAIL_VALIDATION, {
                    "permission_review": "passed",
                    "approval_review": "passed",
                    "data_review": "passed",
                }),
                (BuildGate.UNIT_TESTS, {"command": "tests", "result": "passed", "artifact_digest": ARTIFACT_DIGEST}),
                (BuildGate.AGENT_EVALS, {"cases_total": 1, "cases_failed": 0, "artifact_digest": ARTIFACT_DIGEST}),
                (BuildGate.CODE_REVIEW, {"reviewer": "codex", "findings_resolved": True, "artifact_digest": ARTIFACT_DIGEST}),
                (BuildGate.STAGING_VALIDATION, {"environment": "staging", "smoke_test": "passed", "artifact_digest": ARTIFACT_DIGEST}),
            )
            for gate, evidence in gates:
                build = pipeline.record_gate(
                    build_id=build.build_id,
                    gate=gate,
                    status=GateStatus.PASSED,
                    evidence=evidence,
                    actor="test",
                )
            first = pipeline.request_production_release(
                build_id=build.build_id, requested_by="release_agent"
            )
            second = pipeline.request_production_release(
                build_id=build.build_id, requested_by="release_agent"
            )
            self.assertEqual(first, second)
            self.assertEqual(len(store.list_approvals()), 1)

    def test_duplicate_live_build_for_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store, approvals, registry, pipeline = self._make(directory)
            manifest = AgentManifest.from_mapping(manifest_payload())
            pipeline.start(manifest, artifact_digest=ARTIFACT_DIGEST, actor="agent_factory")
            with self.assertRaises(AgentBuildError):
                pipeline.start(manifest, artifact_digest=ARTIFACT_DIGEST, actor="agent_factory")


    def test_failed_build_can_retry_same_artifact_but_not_different_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            store, approvals, registry, pipeline = self._make(directory)
            manifest = AgentManifest.from_mapping(manifest_payload())
            build = pipeline.start(
                manifest,
                artifact_digest=ARTIFACT_DIGEST,
                actor="agent_factory",
            )
            build = pipeline.record_gate(
                build_id=build.build_id,
                gate=BuildGate.CONTRACT_VALIDATION,
                status=GateStatus.PASSED,
                evidence={
                    "schema": "passed",
                    "manifest_fingerprint": manifest.fingerprint,
                },
                actor="test",
            )
            build = pipeline.record_gate(
                build_id=build.build_id,
                gate=BuildGate.TOOL_VALIDATION,
                status=GateStatus.PASSED,
                evidence={"registry": "passed"},
                actor="test",
            )
            build = pipeline.record_gate(
                build_id=build.build_id,
                gate=BuildGate.GUARDRAIL_VALIDATION,
                status=GateStatus.PASSED,
                evidence={
                    "permission_review": "passed",
                    "approval_review": "passed",
                    "data_review": "passed",
                },
                actor="test",
            )
            failed = pipeline.record_gate(
                build_id=build.build_id,
                gate=BuildGate.UNIT_TESTS,
                status=GateStatus.FAILED,
                evidence={"failure_reason": "regression"},
                actor="ci",
            )
            self.assertEqual(failed.status, BuildStatus.FAILED)
            retried = pipeline.start(
                manifest,
                artifact_digest=ARTIFACT_DIGEST,
                actor="agent_factory",
            )
            self.assertEqual(retried.status, BuildStatus.ACTIVE)
            retried = pipeline.record_gate(
                build_id=retried.build_id,
                gate=BuildGate.CONTRACT_VALIDATION,
                status=GateStatus.PASSED,
                evidence={"manifest_fingerprint": manifest.fingerprint},
                actor="test",
            )
            retried = pipeline.record_gate(
                build_id=retried.build_id,
                gate=BuildGate.TOOL_VALIDATION,
                status=GateStatus.PASSED,
                evidence={"registry": "passed"},
                actor="test",
            )
            retried = pipeline.record_gate(
                build_id=retried.build_id,
                gate=BuildGate.GUARDRAIL_VALIDATION,
                status=GateStatus.PASSED,
                evidence={
                    "permission_review": "passed",
                    "approval_review": "passed",
                    "data_review": "passed",
                },
                actor="test",
            )
            self.assertEqual(
                registry.get(manifest.agent_id, manifest.version).stage,
                AgentLifecycleStage.VALIDATED,
            )
            with self.assertRaises(AgentBuildError):
                pipeline.start(
                    manifest,
                    artifact_digest="b" * 64,
                    actor="agent_factory",
                )

    def test_gate_evidence_must_match_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            store, approvals, registry, pipeline = self._make(directory)
            manifest = AgentManifest.from_mapping(manifest_payload())
            build = pipeline.start(
                manifest,
                artifact_digest=ARTIFACT_DIGEST,
                actor="agent_factory",
            )
            build = pipeline.record_gate(
                build_id=build.build_id,
                gate=BuildGate.CONTRACT_VALIDATION,
                status=GateStatus.PASSED,
                evidence={
                    "manifest_fingerprint": manifest.fingerprint,
                },
                actor="test",
            )
            build = pipeline.record_gate(
                build_id=build.build_id,
                gate=BuildGate.TOOL_VALIDATION,
                status=GateStatus.PASSED,
                evidence={"registry": "passed"},
                actor="test",
            )
            build = pipeline.record_gate(
                build_id=build.build_id,
                gate=BuildGate.GUARDRAIL_VALIDATION,
                status=GateStatus.PASSED,
                evidence={
                    "permission_review": "passed",
                    "approval_review": "passed",
                    "data_review": "passed",
                },
                actor="test",
            )
            with self.assertRaises(AgentBuildError):
                pipeline.record_gate(
                    build_id=build.build_id,
                    gate=BuildGate.UNIT_TESTS,
                    status=GateStatus.PASSED,
                    evidence={
                        "command": "tests",
                        "result": "passed",
                        "artifact_digest": "b" * 64,
                    },
                    actor="ci",
                )


    def test_requester_cannot_approve_own_production_release(self):
        with tempfile.TemporaryDirectory() as directory:
            store, approvals, registry, pipeline = self._make(directory)
            manifest = AgentManifest.from_mapping(manifest_payload())
            build = pipeline.start(
                manifest,
                artifact_digest=ARTIFACT_DIGEST,
                actor="agent_factory",
            )
            gates = (
                (
                    BuildGate.CONTRACT_VALIDATION,
                    {"manifest_fingerprint": manifest.fingerprint},
                ),
                (BuildGate.TOOL_VALIDATION, {"registry": "passed"}),
                (
                    BuildGate.GUARDRAIL_VALIDATION,
                    {
                        "permission_review": "passed",
                        "approval_review": "passed",
                        "data_review": "passed",
                    },
                ),
                (
                    BuildGate.UNIT_TESTS,
                    {
                        "command": "tests",
                        "result": "passed",
                        "artifact_digest": ARTIFACT_DIGEST,
                    },
                ),
                (
                    BuildGate.AGENT_EVALS,
                    {
                        "cases_total": 1,
                        "cases_failed": 0,
                        "artifact_digest": ARTIFACT_DIGEST,
                    },
                ),
                (
                    BuildGate.CODE_REVIEW,
                    {
                        "reviewer": "codex",
                        "findings_resolved": True,
                        "artifact_digest": ARTIFACT_DIGEST,
                    },
                ),
                (
                    BuildGate.STAGING_VALIDATION,
                    {
                        "environment": "staging",
                        "smoke_test": "passed",
                        "artifact_digest": ARTIFACT_DIGEST,
                    },
                ),
            )
            for gate, evidence in gates:
                build = pipeline.record_gate(
                    build_id=build.build_id,
                    gate=gate,
                    status=GateStatus.PASSED,
                    evidence=evidence,
                    actor="test",
                )
            approval_id = pipeline.request_production_release(
                build_id=build.build_id,
                requested_by="release_agent",
            )
            approvals.decide(
                approval_id,
                approve=True,
                decided_by="release_agent",
                reason="self approval",
            )
            with self.assertRaises(AgentBuildError):
                pipeline.release_to_production(
                    build_id=build.build_id,
                    approval_id=approval_id,
                    actor="deployment_controller",
                    deployment_evidence={
                        "deployment_id": "deploy-123",
                        "health_check": "passed",
                        "artifact_digest": ARTIFACT_DIGEST,
                    },
                )


    def test_workspace_digest_is_used_by_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = scaffold_agent(
                root,
                agent_id="operations_agent",
                display_name="Operations Agent",
                purpose="Review operational records and surface verified execution exceptions.",
                owner="operations",
            )
            store = SQLiteStore(root / "ops.db")
            approvals = ApprovalService(store)
            registry = AgentRegistry(store)
            pipeline = AgentBuildPipeline(
                store=store,
                registry=registry,
                tool_registry=ToolRegistry(),
                approvals=approvals,
            )
            build = pipeline.start_workspace(workspace, actor="agent_factory")
            self.assertEqual(build.artifact_digest, workspace.artifact_digest)


if __name__ == "__main__":
    unittest.main()
