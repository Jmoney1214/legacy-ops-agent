from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping

from .agent_manifest import AgentManifest, ToolPermission
from .domain import Severity


class ToolRegistryError(ValueError):
    pass


class SideEffectLevel(StrEnum):
    NONE = "none"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    side_effect_level: SideEffectLevel = SideEffectLevel.NONE
    minimum_risk_level: Severity = Severity.INFO
    handler: Callable[..., Any] | None = None

    def validate(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ToolRegistryError(
                "tool names must contain only letters, numbers, and underscores"
            )
        if len(self.description.strip()) < 12:
            raise ToolRegistryError("tool descriptions must be at least 12 characters")
        if self.input_schema.get("type") != "object":
            raise ToolRegistryError("tool input_schema root type must be object")
        properties = self.input_schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ToolRegistryError("tool input_schema properties must be a mapping")
        required = self.input_schema.get("required", [])
        if not isinstance(required, list):
            raise ToolRegistryError("tool input_schema required must be a list")
        missing = set(required) - set(properties)
        if missing:
            raise ToolRegistryError(
                f"required fields missing schemas: {', '.join(sorted(missing))}"
            )
        if self.input_schema.get("additionalProperties", False) is not False:
            raise ToolRegistryError(
                "tool input schemas must set additionalProperties to false"
            )


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    requires_approval: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        spec.validate()
        if spec.name in self._tools:
            raise ToolRegistryError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolRegistryError(f"unknown tool: {name}") from exc

    def list_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def validate_manifest(self, manifest: AgentManifest) -> None:
        missing = [name for name in manifest.tools if name not in self._tools]
        if missing:
            raise ToolRegistryError(
                f"manifest references unregistered tools: {', '.join(sorted(missing))}"
            )
        for tool_name in manifest.tools:
            spec = self.get(tool_name)
            permissions = manifest.permissions[tool_name]
            if (
                spec.side_effect_level is SideEffectLevel.NONE
                and ToolPermission.EXECUTE in permissions
            ):
                raise ToolRegistryError(
                    f"read-only tool {tool_name} cannot grant execute permission"
                )
            if (
                spec.side_effect_level is SideEffectLevel.IRREVERSIBLE
                and ToolPermission.EXECUTE not in permissions
            ):
                raise ToolRegistryError(
                    f"irreversible tool {tool_name} requires execute permission"
                )

    def authorize(
        self,
        *,
        manifest: AgentManifest,
        tool_name: str,
        permission: ToolPermission,
        approval_action: str | None = None,
        approval_granted: bool = False,
    ) -> AuthorizationDecision:
        if tool_name not in manifest.tools:
            return AuthorizationDecision(False, "tool is not declared by the agent")
        spec = self.get(tool_name)
        allowed_permissions = manifest.permissions.get(tool_name, frozenset())
        if permission not in allowed_permissions:
            return AuthorizationDecision(
                False,
                f"agent lacks {permission.value} permission for {tool_name}",
            )

        needs_approval = (
            permission in {ToolPermission.WRITE, ToolPermission.EXECUTE}
            or spec.side_effect_level is not SideEffectLevel.NONE
        )
        if needs_approval:
            if not approval_action:
                return AuthorizationDecision(
                    False,
                    "side-effecting tool call is missing an approval action",
                    requires_approval=True,
                )
            if approval_action not in manifest.approval_actions:
                return AuthorizationDecision(
                    False,
                    "approval action is not authorized by the agent manifest",
                    requires_approval=True,
                )
            if not approval_granted:
                return AuthorizationDecision(
                    False,
                    "human approval is required before this tool call",
                    requires_approval=True,
                )

        return AuthorizationDecision(True, "authorized", requires_approval=False)

    def validate_input(self, tool_name: str, payload: Mapping[str, Any]) -> None:
        spec = self.get(tool_name)
        schema = spec.input_schema
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = required - set(payload)
        if missing:
            raise ToolRegistryError(
                f"missing required tool fields: {', '.join(sorted(missing))}"
            )
        unknown = set(payload) - set(properties)
        if unknown:
            raise ToolRegistryError(
                f"unknown tool fields: {', '.join(sorted(unknown))}"
            )
        for field_name, value in payload.items():
            expected = properties[field_name].get("type")
            if expected and not _matches_type(value, expected):
                raise ToolRegistryError(
                    f"tool field {field_name!r} must be {expected}"
                )


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "null":
        return value is None
    return False
