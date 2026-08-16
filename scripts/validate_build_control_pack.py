#!/usr/bin/env python3
"""Validate the version-controlled Legacy build-control artifacts."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "docs" / "build"
CATALOG_PATH = BUILD / "PHASE_CATALOG.yaml"
STATUS_PATH = BUILD / "PHASE_STATUS.yaml"

REQUIRED_FILES = [
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "CODEX_MASTER_PROMPT.md",
    ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
    ROOT / ".github" / "ISSUE_TEMPLATE" / "phase-implementation.md",
    BUILD / "README.md",
    BUILD / "MASTER_BUILD_SCHEDULE.md",
    BUILD / "CODEX_EXECUTION_PROTOCOL.md",
    BUILD / "LIVE_BUILD_BOARD.md",
    CATALOG_PATH,
    STATUS_PATH,
    BUILD / "reports" / "P00_REPOSITORY_BASELINE.md",
    BUILD / "reports" / "P00_START_REPORT.md",
]

EXPECTED_PHASE_IDS = [f"P{i:02d}" for i in range(16)]
VALID_RISKS = {"STANDARD", "HIGH", "CRITICAL"}
MIN_REVIEWS = {"STANDARD": 1, "HIGH": 2, "CRITICAL": 2}
TERMINAL_PHASE_STATUSES = {"planned", "complete", "cancelled"}
SATISFIED_GATE_STATES = {"passed", "not_applicable"}


class ValidationError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(f"Unable to load {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{path.relative_to(ROOT)} must contain a YAML mapping")
    return data


def parse_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{label} is not a valid ISO date: {value}") from exc


def require_unique_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValidationError(f"{label} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValidationError(f"{label} must contain only non-empty strings")
    if len(value) != len(set(value)):
        raise ValidationError(f"{label} contains duplicates")
    return value


def validate_required_files() -> None:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_FILES if not path.is_file()]
    if missing:
        raise ValidationError("Missing required files: " + ", ".join(missing))


def validate_catalog(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    phases = catalog.get("phases")
    if not isinstance(phases, dict):
        raise ValidationError("PHASE_CATALOG.yaml must define a phases mapping")
    if list(phases) != EXPECTED_PHASE_IDS:
        raise ValidationError(
            f"Phase IDs must be ordered exactly as {EXPECTED_PHASE_IDS}; found {list(phases)}"
        )

    branches: set[str] = set()
    previous_end: date | None = None
    for phase_id, phase in phases.items():
        if not isinstance(phase, dict):
            raise ValidationError(f"{phase_id} must be a mapping")

        required = {
            "title",
            "target_start",
            "target_end",
            "workdays",
            "branch",
            "risk",
            "required_independent_reviews",
            "dependencies",
            "objective",
            "deliverables",
            "non_goals",
            "required_verification",
            "exit_criteria",
        }
        missing = sorted(required - phase.keys())
        if missing:
            raise ValidationError(f"{phase_id} is missing fields: {', '.join(missing)}")

        start = parse_date(phase["target_start"], f"{phase_id}.target_start")
        end = parse_date(phase["target_end"], f"{phase_id}.target_end")
        if start > end:
            raise ValidationError(f"{phase_id} starts after it ends")
        if previous_end is not None and start <= previous_end:
            raise ValidationError(f"{phase_id} overlaps the prior phase schedule")
        previous_end = end

        if not isinstance(phase["workdays"], int) or phase["workdays"] < 1:
            raise ValidationError(f"{phase_id}.workdays must be a positive integer")

        branch = phase["branch"]
        if not isinstance(branch, str) or not branch.startswith("phase/"):
            raise ValidationError(f"{phase_id}.branch must start with phase/")
        if branch in branches:
            raise ValidationError(f"Duplicate branch in phase catalog: {branch}")
        branches.add(branch)

        risk = phase["risk"]
        if risk not in VALID_RISKS:
            raise ValidationError(f"{phase_id}.risk must be one of {sorted(VALID_RISKS)}")
        reviews = phase["required_independent_reviews"]
        if not isinstance(reviews, int) or reviews < MIN_REVIEWS[risk]:
            raise ValidationError(
                f"{phase_id} requires at least {MIN_REVIEWS[risk]} independent review(s) for {risk}"
            )

        dependencies = phase["dependencies"]
        if not isinstance(dependencies, list):
            raise ValidationError(f"{phase_id}.dependencies must be a list")
        for dependency in dependencies:
            if dependency not in phases:
                raise ValidationError(f"{phase_id} references unknown dependency {dependency}")
            if EXPECTED_PHASE_IDS.index(dependency) >= EXPECTED_PHASE_IDS.index(phase_id):
                raise ValidationError(f"{phase_id} dependency {dependency} is not an earlier phase")

        for list_field in (
            "deliverables",
            "non_goals",
            "required_verification",
            "exit_criteria",
        ):
            require_unique_string_list(phase[list_field], f"{phase_id}.{list_field}")

    return phases


def validate_status(status: dict[str, Any], catalog_phases: dict[str, dict[str, Any]]) -> None:
    program = status.get("program")
    if not isinstance(program, dict):
        raise ValidationError("PHASE_STATUS.yaml must define program")

    current_phase = program.get("current_phase")
    if current_phase not in catalog_phases:
        raise ValidationError(f"Unknown current phase: {current_phase}")

    allowed_statuses = set(require_unique_string_list(status.get("allowed_statuses"), "allowed_statuses"))
    gate_states = set(require_unique_string_list(status.get("gate_states"), "gate_states"))
    required_gates = require_unique_string_list(status.get("required_gates"), "required_gates")
    pre_merge_gates = require_unique_string_list(status.get("pre_merge_gates"), "pre_merge_gates")
    post_merge_gates = require_unique_string_list(status.get("post_merge_gates"), "post_merge_gates")

    if set(pre_merge_gates) & set(post_merge_gates):
        raise ValidationError("pre_merge_gates and post_merge_gates must not overlap")
    if set(pre_merge_gates) | set(post_merge_gates) != set(required_gates):
        raise ValidationError("Pre/post merge gate groups must cover required_gates exactly")

    program_status = program.get("status")
    if program_status not in allowed_statuses:
        raise ValidationError(f"Invalid program status: {program_status}")
    if not isinstance(program.get("merge_authorized"), bool):
        raise ValidationError("program.merge_authorized must be boolean")

    phase_statuses = status.get("phases")
    if not isinstance(phase_statuses, dict):
        raise ValidationError("PHASE_STATUS.yaml must define phases")
    if list(phase_statuses) != EXPECTED_PHASE_IDS:
        raise ValidationError("PHASE_STATUS.yaml must list P00 through P15 in order")

    active: list[str] = []
    for phase_id, phase_status in phase_statuses.items():
        if not isinstance(phase_status, dict):
            raise ValidationError(f"Status for {phase_id} must be a mapping")
        value = phase_status.get("status")
        if value not in allowed_statuses:
            raise ValidationError(f"Invalid status for {phase_id}: {value}")
        if value not in TERMINAL_PHASE_STATUSES:
            active.append(phase_id)

    limit = program.get("default_active_phase_limit")
    if not isinstance(limit, int) or limit < 1:
        raise ValidationError("default_active_phase_limit must be a positive integer")
    if len(active) > limit:
        raise ValidationError(f"Active phase limit exceeded: {active}")
    if current_phase not in active:
        raise ValidationError("current_phase must be the active phase")

    current = phase_statuses[current_phase]
    if current.get("status") != program_status:
        raise ValidationError("Program status must match the current phase status")
    if current.get("branch") != catalog_phases[current_phase]["branch"]:
        raise ValidationError("Current phase branch does not match the phase catalog")
    if current.get("risk") != catalog_phases[current_phase]["risk"]:
        raise ValidationError("Current phase risk does not match the phase catalog")
    if current.get("required_independent_reviews") != catalog_phases[current_phase][
        "required_independent_reviews"
    ]:
        raise ValidationError("Current phase review count does not match the phase catalog")
    if not isinstance(current.get("merge_authorized"), bool):
        raise ValidationError("Current phase merge_authorized must be boolean")
    if bool(program["merge_authorized"]) != bool(current["merge_authorized"]):
        raise ValidationError("Program and current-phase merge_authorized values disagree")
    if not isinstance(current.get("owner_release_approved"), bool):
        raise ValidationError("Current phase owner_release_approved must be boolean")

    gates = current.get("gates")
    if not isinstance(gates, dict):
        raise ValidationError("Current phase must define a gates mapping")
    missing_gates = [gate for gate in required_gates if gate not in gates]
    extra_gates = [gate for gate in gates if gate not in required_gates]
    if missing_gates:
        raise ValidationError("Current phase is missing gates: " + ", ".join(missing_gates))
    if extra_gates:
        raise ValidationError("Current phase has unknown gates: " + ", ".join(extra_gates))
    invalid_gate_states = {
        gate: value for gate, value in gates.items() if value not in gate_states
    }
    if invalid_gate_states:
        raise ValidationError(f"Invalid gate states: {invalid_gate_states}")

    exceptions = current.get("gate_exceptions", {})
    if not isinstance(exceptions, dict):
        raise ValidationError("gate_exceptions must be a mapping")
    for gate, state in gates.items():
        if state == "not_applicable":
            reason = exceptions.get(gate)
            if not isinstance(reason, str) or not reason.strip():
                raise ValidationError(f"Gate {gate} is not_applicable without a reason")
    for gate in exceptions:
        if gate not in gates:
            raise ValidationError(f"gate_exceptions references unknown gate {gate}")

    if current_phase == "P00":
        bootstrap = current.get("bootstrap_exception")
        if not isinstance(bootstrap, dict) or bootstrap.get("active") is not True:
            raise ValidationError("P00 must document its one-time bootstrap exception")
        if bootstrap.get("future_phases_permitted") is not False:
            raise ValidationError("The P00 bootstrap exception must be prohibited for future phases")
    elif current.get("bootstrap_exception"):
        raise ValidationError("Only P00 may use a bootstrap exception")

    if current["status"] == "ready_to_merge":
        unsatisfied = [
            gate for gate in pre_merge_gates if gates[gate] not in SATISFIED_GATE_STATES
        ]
        if unsatisfied:
            raise ValidationError(f"ready_to_merge has unsatisfied gates: {unsatisfied}")
        if not current["merge_authorized"]:
            raise ValidationError("ready_to_merge requires merge authorization")
        if current["risk"] == "CRITICAL" and not current["owner_release_approved"]:
            raise ValidationError("CRITICAL ready_to_merge requires owner release approval")

    if current["status"] == "post_merge_verify" and gates["squash_merged"] != "passed":
        raise ValidationError("post_merge_verify requires squash_merged=passed")

    if current["status"] == "complete":
        unsatisfied = [
            gate for gate in required_gates if gates[gate] not in SATISFIED_GATE_STATES
        ]
        if unsatisfied:
            raise ValidationError(f"complete phase has unsatisfied gates: {unsatisfied}")
        if not current["merge_authorized"]:
            raise ValidationError("complete phase requires merge authorization")

    current_index = EXPECTED_PHASE_IDS.index(current_phase)
    for dependency in catalog_phases[current_phase]["dependencies"]:
        if phase_statuses[dependency]["status"] != "complete":
            raise ValidationError(f"Current phase dependency {dependency} is not complete")
    for future in EXPECTED_PHASE_IDS[current_index + 1 :]:
        if phase_statuses[future]["status"] != "planned":
            raise ValidationError(f"Future phase {future} must remain planned")


def main() -> int:
    try:
        validate_required_files()
        catalog = load_yaml(CATALOG_PATH)
        status = load_yaml(STATUS_PATH)
        phases = validate_catalog(catalog)
        validate_status(status, phases)
    except ValidationError as exc:
        print(f"FAIL: {exc}")
        return 1

    print("PASS: build-control artifacts are internally consistent")
    print(f"Validated phases: {len(EXPECTED_PHASE_IDS)}")
    print(f"Current phase: {status['program']['current_phase']}")
    print(f"Program status: {status['program']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
