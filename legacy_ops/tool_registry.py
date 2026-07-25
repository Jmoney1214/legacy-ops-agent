from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping, Sequence

from .agent_manifest import AgentManifest, ToolPermission
from .domain import Severity


class ToolRegistryError(ValueError):
    pass


class SideEffectLevel(StrEnum):
    NONE = "none"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
_SUPPORTED_SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "description",
}
_SUPPORTED_TYPES = {
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "object",
    "null",
}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    side_effect_level: SideEffectLevel = SideEffectLevel.NONE
    minimum_risk_level: Severity = Severity.INFO
    required_approval_action: str | None = None
    handler: Callable[..., Any] | None = None

    def validate(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ToolRegistryError(
                "tool names must contain only letters, numbers, and underscores"
            )
        if len(self.description.strip()) < 12:
            raise ToolRegistryError("tool descriptions must be at least 12 characters")
        _validate_schema_definition(
            self.input_schema,
            path=f"{self.name}.input",
            require_strict_object=True,
        )
        if self.output_schema:
            _validate_schema_definition(
                self.output_schema,
                path=f"{self.name}.output",
                require_strict_object=False,
            )
        if self.side_effect_level is SideEffectLevel.NONE:
            if self.required_approval_action:
                raise ToolRegistryError(
                    "read-only tools cannot require an approval action"
                )
        elif not str(self.required_approval_action or "").strip():
            raise ToolRegistryError(
                "side-effecting tools require a specific approval action"
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
            if _SEVERITY_RANK[manifest.risk_level] < _SEVERITY_RANK[spec.minimum_risk_level]:
                raise ToolRegistryError(
                    f"agent risk_level is too low for tool {tool_name}"
                )
            if spec.side_effect_level is SideEffectLevel.NONE:
                if ToolPermission.READ not in permissions:
                    raise ToolRegistryError(
                        f"read-only tool {tool_name} requires read permission"
                    )
                forbidden = permissions & {
                    ToolPermission.WRITE,
                    ToolPermission.EXECUTE,
                }
                if forbidden:
                    raise ToolRegistryError(
                        f"read-only tool {tool_name} cannot grant write or execute permission"
                    )
            elif spec.side_effect_level is SideEffectLevel.REVERSIBLE:
                if not (
                    ToolPermission.WRITE in permissions
                    or ToolPermission.EXECUTE in permissions
                ):
                    raise ToolRegistryError(
                        f"reversible tool {tool_name} requires write or execute permission"
                    )
            elif ToolPermission.EXECUTE not in permissions:
                raise ToolRegistryError(
                    f"irreversible tool {tool_name} requires execute permission"
                )

            required_action = spec.required_approval_action
            if required_action and required_action not in manifest.approval_actions:
                raise ToolRegistryError(
                    f"tool {tool_name} requires approval action {required_action}"
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

        required_action = spec.required_approval_action
        if required_action:
            if approval_action != required_action:
                return AuthorizationDecision(
                    False,
                    f"tool requires approval action {required_action}",
                    requires_approval=True,
                )
            if required_action not in manifest.approval_actions:
                return AuthorizationDecision(
                    False,
                    "required approval action is not authorized by the agent manifest",
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
        _validate_value(
            payload,
            self.get(tool_name).input_schema,
            path=f"{tool_name}.input",
        )

    def validate_output(self, tool_name: str, output: Any) -> None:
        schema = self.get(tool_name).output_schema
        if schema:
            _validate_value(output, schema, path=f"{tool_name}.output")


def _validate_schema_definition(
    schema: Mapping[str, Any],
    *,
    path: str,
    require_strict_object: bool,
) -> None:
    if not isinstance(schema, Mapping):
        raise ToolRegistryError(f"{path} schema must be a mapping")
    unknown = set(schema) - _SUPPORTED_SCHEMA_KEYS
    if unknown:
        raise ToolRegistryError(
            f"{path} schema uses unsupported keywords: {', '.join(sorted(unknown))}"
        )
    schema_type = schema.get("type")
    if schema_type not in _SUPPORTED_TYPES:
        raise ToolRegistryError(f"{path} schema has an unsupported type")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise ToolRegistryError(f"{path} enum must be a non-empty list")

    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ToolRegistryError(f"{path} properties must be a mapping")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            raise ToolRegistryError(f"{path} required must be a string list")
        missing = set(required) - set(properties)
        if missing:
            raise ToolRegistryError(
                f"{path} required fields missing schemas: "
                f"{', '.join(sorted(missing))}"
            )
        if (
            require_strict_object or properties
        ) and schema.get("additionalProperties") is not False:
            raise ToolRegistryError(
                f"{path} object schema must set additionalProperties to false"
            )
        for name, child in properties.items():
            _validate_schema_definition(
                child,
                path=f"{path}.{name}",
                require_strict_object=False,
            )
    elif schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ToolRegistryError(f"{path} array schema requires items")
        _validate_schema_definition(
            items,
            path=f"{path}[]",
            require_strict_object=False,
        )
    elif "properties" in schema or "required" in schema or "items" in schema:
        raise ToolRegistryError(
            f"{path} schema contains keywords incompatible with {schema_type}"
        )

    for minimum_key, maximum_key in (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
        ("minimum", "maximum"),
    ):
        minimum = schema.get(minimum_key)
        maximum = schema.get(maximum_key)
        if minimum is not None and (
            not isinstance(minimum, (int, float)) or isinstance(minimum, bool)
        ):
            raise ToolRegistryError(f"{path} {minimum_key} must be numeric")
        if maximum is not None and (
            not isinstance(maximum, (int, float)) or isinstance(maximum, bool)
        ):
            raise ToolRegistryError(f"{path} {maximum_key} must be numeric")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ToolRegistryError(
                f"{path} {minimum_key} cannot exceed {maximum_key}"
            )


def _validate_value(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    expected = str(schema["type"])
    if not _matches_type(value, expected):
        raise ToolRegistryError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolRegistryError(f"{path} is not an allowed enum value")

    if expected == "object":
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = required - set(value)
        if missing:
            raise ToolRegistryError(
                f"{path} is missing fields: {', '.join(sorted(missing))}"
            )
        unknown = set(value) - set(properties)
        if unknown and schema.get("additionalProperties") is False:
            raise ToolRegistryError(
                f"{path} has unknown fields: {', '.join(sorted(unknown))}"
            )
        for name, item in value.items():
            child_schema = properties.get(name)
            if child_schema is not None:
                _validate_value(item, child_schema, path=f"{path}.{name}")
    elif expected == "array":
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise ToolRegistryError(f"{path} has too few items")
        if maximum is not None and len(value) > maximum:
            raise ToolRegistryError(f"{path} has too many items")
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], path=f"{path}[{index}]")
    elif expected == "string":
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise ToolRegistryError(f"{path} is shorter than minLength")
        if maximum is not None and len(value) > maximum:
            raise ToolRegistryError(f"{path} is longer than maxLength")
    elif expected in {"integer", "number"}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            raise ToolRegistryError(f"{path} is below minimum")
        if maximum is not None and value > maximum:
            raise ToolRegistryError(f"{path} exceeds maximum")


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
