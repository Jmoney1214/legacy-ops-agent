from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from time import monotonic

from .agent_manifest import LoopPolicy


class LoopError(RuntimeError):
    pass


class StopReason(StrEnum):
    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_RUNTIME = "max_runtime"
    MAX_COST = "max_cost"
    REPEATED_FAILURE = "repeated_failure"
    GUARDRAIL_BLOCK = "guardrail_block"
    HUMAN_ESCALATION = "human_escalation"


@dataclass(slots=True)
class LoopState:
    policy: LoopPolicy
    started_at: float = field(default_factory=monotonic)
    iterations: int = 0
    tool_calls: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    consecutive_failures: int = 0
    last_failure_signature: str | None = None
    stop_reason: StopReason | None = None

    @property
    def elapsed_seconds(self) -> float:
        return monotonic() - self.started_at


class LoopController:
    def __init__(self, policy: LoopPolicy):
        policy.validate()
        self.state = LoopState(policy=policy)

    def _ensure_running(self) -> None:
        if self.state.stop_reason is not None:
            raise LoopError(f"loop already stopped: {self.state.stop_reason.value}")

    def before_iteration(self) -> None:
        self._ensure_running()
        reason = self.evaluate_limits()
        if reason is not None:
            self.state.stop_reason = reason
            raise LoopError(f"loop budget reached: {reason.value}")
        self.state.iterations += 1

    def record_tool_call(self, estimated_cost_usd: Decimal | str = Decimal("0")) -> None:
        self._ensure_running()
        try:
            cost = Decimal(str(estimated_cost_usd))
        except Exception as exc:
            raise LoopError("estimated tool-call cost must be numeric") from exc
        if cost < 0:
            raise LoopError("estimated tool-call cost cannot be negative")
        self.state.tool_calls += 1
        self.state.estimated_cost_usd += cost
        reason = self.evaluate_limits()
        if reason is not None:
            self.state.stop_reason = reason

    def record_success(self) -> None:
        self._ensure_running()
        self.state.consecutive_failures = 0
        self.state.last_failure_signature = None

    def record_failure(self, signature: str) -> None:
        self._ensure_running()
        normalized = signature.strip() or "unknown_failure"
        if normalized == self.state.last_failure_signature:
            self.state.consecutive_failures += 1
        else:
            self.state.last_failure_signature = normalized
            self.state.consecutive_failures = 1
        reason = self.evaluate_limits()
        if reason is not None:
            self.state.stop_reason = reason

    def stop(self, reason: StopReason) -> StopReason:
        if self.state.stop_reason is None:
            self.state.stop_reason = reason
        return self.state.stop_reason

    def evaluate_limits(self) -> StopReason | None:
        policy = self.state.policy
        if self.state.iterations >= policy.max_iterations:
            return StopReason.MAX_ITERATIONS
        if self.state.tool_calls >= policy.max_tool_calls:
            return StopReason.MAX_TOOL_CALLS
        if self.state.elapsed_seconds >= policy.max_runtime_seconds:
            return StopReason.MAX_RUNTIME
        if self.state.estimated_cost_usd >= policy.max_cost_usd:
            return StopReason.MAX_COST
        if self.state.consecutive_failures >= policy.max_consecutive_failures:
            return StopReason.REPEATED_FAILURE
        return None

    @property
    def should_stop(self) -> bool:
        if self.state.stop_reason is not None:
            return True
        reason = self.evaluate_limits()
        if reason is not None:
            self.state.stop_reason = reason
            return True
        return False
