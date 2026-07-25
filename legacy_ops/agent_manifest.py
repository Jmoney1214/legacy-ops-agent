from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .domain import Severity


_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
_MEMORY_SCOPES = {"working", "episodic", "semantic", "procedural"}


class ManifestError(ValueError):
    pass


class ToolPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class ExecutionMode(StrEnum):
    HUMAN_IN_THE_LOOP = "human_in_the_loop"
    HUMAN_ON_THE_LOOP = "human_on_the_loop"
    HUMAN_OUT_OF_THE_LOOP = "human_out_of_the_loop"


@dataclass(frozen=True, slots=True)
class LoopPolicy:
    max_iterations: int = 8
    max_tool_calls: int = 20
    max_runtime_seconds: int = 300
    max_cost_usd: Decimal = Decimal("2.00")
    max_consecutive_failures: int = 3

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "LoopPolicy":
        data = dict(value or {})
        try:
            return cls(
                max_iterations=int(data.get("max_iterations", 8)),
                max_tool_calls=int(data.get("max_tool_calls", 20)),
                max_runtime_seconds=int(data.get("max_runtime_seconds", 300)),
                max_cost_usd=Decimal(str(data.get("max_cost_usd", "2.00"))),
                max_consecutive_failures=int(
                    data.get("max_consecutive_failures", 3)
                ),
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ManifestError("loop_policy contains invalid numeric values") from exc

    def validate(self) -> None:
        if not 1 <= self.max_iterations <= 25:
            raise ManifestError("max_iterations must be between 1 and 25")
        if not 0 <= self.max_tool_calls <= 100:
            raise ManifestError("max_tool_calls must be between 0 and 100")
        if not 1 <= self.max_runtime_seconds <= 3600:
            raise ManifestError("max_runtime_seconds must be between 1 and 3600")
        if not Decimal("0") <= self.max_cost_usd <= Decimal("100"):
            raise ManifestError("max_cost_usd must be between 0 and 100")
        if not 1 <= self.max_consecutive_failures <= 10:
            raise ManifestError(
                "max_consecutive_failures must be between 1 and 10"
            )


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    max_items: int = 40
    max_characters: int = 80_000
    allowed_sources: tuple[str, ...] = ()
    forbidden_data_classes: tuple[str, ...] = (
        "full_card_number",
        "password",
        "oauth_token",
        "api_key",
    )
    require_source_attribution: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ContextPolicy":
        data = dict(value or {})
        return cls(
            max_items=int(data.get("max_items", 40)),
            max_characters=int(data.get("max_characters", 80_000)),
            allowed_sources=_string_tuple(data.get("allowed_sources", ())),
            forbidden_data_classes=_string_tuple(
                data.get(
                    "forbidden_data_classes",
                    ("full_card_number", "password", "oauth_token", "api_key"),
                )
            ),
            require_source_attribution=bool(
                data.get("require_source_attribution", True)
            ),
        )

    def validate(self) -> None:
        if not 1 <= self.max_items <= 500:
            raise ManifestError("context max_items must be between 1 and 500")
        if not 1_000 <= self.max_characters <= 1_000_000:
            raise ManifestError(
                "context max_characters must be between 1,000 and 1,000,000"
            )


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    read_scopes: tuple[str, ...] = ("working",)
    write_scopes: tuple[str, ...] = ()
    retention_days: int = 30
    require_write_attribution: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "MemoryPolicy":
        data = dict(value or {})
        return cls(
            read_scopes=_string_tuple(data.get("read_scopes", ("working",))),
            write_scopes=_string_tuple(data.get("write_scopes", ())),
            retention_days=int(data.get("retention_days", 30)),
            require_write_attribution=bool(
                data.get("require_write_attribution", True)
            ),
        )

    def validate(self) -> None:
        unknown = (set(self.read_scopes) | set(self.write_scopes)) - _MEMORY_SCOPES
        if unknown:
            raise ManifestError(
                f"unknown memory scopes: {', '.join(sorted(unknown))}"
            )
        if not 1 <= self.retention_days <= 3650:
            raise ManifestError("memory retention_days must be between 1 and 3650")
        if "procedural" in self.write_scopes:
            raise ManifestError(
                "agents may read procedural memory but may not directly write it"
            )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, Sequence):
        raise ManifestError("expected a string or sequence of strings")
    output: list[str] = []
    for item in value:
        normalized = str(item).strip()
        if normalized and normalized not in output:
            output.append(normalized)
    return tuple(output)


@dataclass(frozen=True, slots=True)
class AgentManifest:
    agent_id: str
    version: str
    display_name: str
    purpose: str
    owner: str
    risk_level: Severity
    instructions_path: str
    tools: tuple[str, ...] = ()
    permissions: Mapping[str, frozenset[ToolPermission]] = field(default_factory=dict)
    approval_actions: tuple[str, ...] = ()
    execution_mode: ExecutionMode = ExecutionMode.HUMAN_IN_THE_LOOP
    loop_policy: LoopPolicy = field(default_factory=LoopPolicy)
    context_policy: ContextPolicy = field(default_factory=ContextPolicy)
    memory_policy: MemoryPolicy = field(default_factory=MemoryPolicy)
    tags: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentManifest":
        allowed = {
            "agent_id",
            "version",
            "display_name",
            "purpose",
            "owner",
            "risk_level",
            "instructions_path",
            "tools",
            "permissions",
            "approval_actions",
            "execution_mode",
            "loop_policy",
            "context_policy",
            "memory_policy",
            "tags",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ManifestError(
                f"unknown manifest fields: {', '.join(sorted(unknown))}"
            )

        raw_permissions = value.get("permissions") or {}
        if not isinstance(raw_permissions, Mapping):
            raise ManifestError("permissions must be a mapping")
        permissions: dict[str, frozenset[ToolPermission]] = {}
        for tool_name, raw_values in raw_permissions.items():
            try:
                permissions[str(tool_name)] = frozenset(
                    ToolPermission(item) for item in _string_tuple(raw_values)
                )
            except ValueError as exc:
                raise ManifestError(
                    f"invalid permission for tool {tool_name!r}"
                ) from exc

        try:
            manifest = cls(
                agent_id=str(value.get("agent_id") or "").strip(),
                version=str(value.get("version") or "").strip(),
                display_name=str(value.get("display_name") or "").strip(),
                purpose=str(value.get("purpose") or "").strip(),
                owner=str(value.get("owner") or "").strip(),
                risk_level=Severity(str(value.get("risk_level") or "")),
                instructions_path=str(
                    value.get("instructions_path") or ""
                ).strip(),
                tools=_string_tuple(value.get("tools", ())),
                permissions=permissions,
                approval_actions=_string_tuple(value.get("approval_actions", ())),
                execution_mode=ExecutionMode(
                    str(
                        value.get(
                            "execution_mode",
                            ExecutionMode.HUMAN_IN_THE_LOOP.value,
                        )
                    )
                ),
                loop_policy=LoopPolicy.from_mapping(value.get("loop_policy")),
                context_policy=ContextPolicy.from_mapping(
                    value.get("context_policy")
                ),
                memory_policy=MemoryPolicy.from_mapping(value.get("memory_policy")),
                tags=_string_tuple(value.get("tags", ())),
            )
        except ValueError as exc:
            raise ManifestError("manifest contains an invalid enum value") from exc
        manifest.validate()
        return manifest

    @classmethod
    def load(cls, path: str | Path) -> "AgentManifest":
        source = Path(path)
        try:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ManifestError(f"unable to load manifest: {source}") from exc
        if not isinstance(payload, Mapping):
            raise ManifestError("manifest root must be a mapping")
        return cls.from_mapping(payload)

    def validate(self) -> None:
        if not _AGENT_ID.fullmatch(self.agent_id):
            raise ManifestError(
                "agent_id must be 3-64 lowercase letters, numbers, or underscores"
            )
        if not _SEMVER.fullmatch(self.version):
            raise ManifestError("version must use semantic versioning")
        for name, value in (
            ("display_name", self.display_name),
            ("purpose", self.purpose),
            ("owner", self.owner),
            ("instructions_path", self.instructions_path),
        ):
            if not value:
                raise ManifestError(f"{name} is required")
        if len(self.purpose) < 20:
            raise ManifestError("purpose must be at least 20 characters")
        if len(set(self.tools)) != len(self.tools):
            raise ManifestError("tools must be unique")
        undeclared = set(self.permissions) - set(self.tools)
        if undeclared:
            raise ManifestError(
                f"permissions reference undeclared tools: {', '.join(sorted(undeclared))}"
            )
        missing_permissions = set(self.tools) - set(self.permissions)
        if missing_permissions:
            raise ManifestError(
                f"declared tools require permission entries: {', '.join(sorted(missing_permissions))}"
            )
        has_side_effect_permission = any(
            ToolPermission.WRITE in permissions
            or ToolPermission.EXECUTE in permissions
            for permissions in self.permissions.values()
        )
        if has_side_effect_permission and not self.approval_actions:
            raise ManifestError(
                "write or execute permissions require explicit approval_actions"
            )
        if (
            self.execution_mode is ExecutionMode.HUMAN_OUT_OF_THE_LOOP
            and self.risk_level in {Severity.HIGH, Severity.CRITICAL}
        ):
            raise ManifestError(
                "high or critical risk agents cannot be human-out-of-the-loop"
            )
        self.loop_policy.validate()
        self.context_policy.validate()
        self.memory_policy.validate()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["risk_level"] = self.risk_level.value
        result["execution_mode"] = self.execution_mode.value
        result["loop_policy"]["max_cost_usd"] = str(
            self.loop_policy.max_cost_usd
        )
        result["permissions"] = {
            tool: sorted(permission.value for permission in permissions)
            for tool, permissions in sorted(self.permissions.items())
        }
        return result

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
