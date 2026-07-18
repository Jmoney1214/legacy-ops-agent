from __future__ import annotations

from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Awaitable, Callable, Sequence

from .chargebacks import (
    ChargebackError,
    DisputePackage,
    EmailMessage,
    EvidenceDocument,
)
from .control_plane import redact_text
from .domain import AuditEvent, Severity
from .gmail_disputes import GmailDisputeClient
from .lightspeed_chargeback_sales import LightspeedChargebackSaleClient


EvidenceResolver = Callable[
    [EmailMessage, Sequence],
    Awaitable[Sequence[EvidenceDocument]] | Sequence[EvidenceDocument],
]


@dataclass(frozen=True, slots=True)
class IntakeFailure:
    message_id: str
    subject: str
    error_type: str
    explanation: str


@dataclass(slots=True)
class ChargebackScanResult:
    packages: list[DisputePackage] = field(default_factory=list)
    failures: list[IntakeFailure] = field(default_factory=list)

    @property
    def approval_pending_count(self) -> int:
        return sum(item.approval_id is not None for item in self.packages)


class ChargebackPipeline:
    """Gmail intake, deterministic Lightspeed lookup, and approval preparation.

    Production execution requires adapters that explicitly advertise the strict
    and production-hardened contracts. A single transport or decoding failure is
    recorded against that message and cannot terminate the rest of the scan.
    """

    def __init__(
        self,
        *,
        gmail: GmailDisputeClient,
        lightspeed: LightspeedChargebackSaleClient,
        workflow,
        evidence_resolver: EvidenceResolver,
    ):
        if not getattr(workflow, "atomic_submission", False):
            raise ChargebackError(
                "ChargebackPipeline requires an atomic chargeback workflow"
            )
        if not getattr(workflow, "production_hardened", False):
            raise ChargebackError(
                "ChargebackPipeline requires ProductionChargebackWorkflow"
            )
        if not getattr(lightspeed, "strict_matching", False):
            raise ChargebackError(
                "ChargebackPipeline requires a strict Lightspeed adapter"
            )
        if not getattr(lightspeed, "production_hardened", False):
            raise ChargebackError(
                "ChargebackPipeline requires ProductionLightspeedChargebackSaleClient"
            )
        self.gmail = gmail
        self.lightspeed = lightspeed
        self.workflow = workflow
        self.evidence_resolver = evidence_resolver

    async def _resolve_evidence(
        self,
        email: EmailMessage,
        sales: Sequence,
    ) -> Sequence[EvidenceDocument]:
        value = self.evidence_resolver(email, sales)
        if isawaitable(value):
            return await value
        return value

    def _record_failure(self, email: EmailMessage, exc: Exception) -> IntakeFailure:
        explanation = redact_text(str(exc) or type(exc).__name__)
        failure = IntakeFailure(
            message_id=email.message_id,
            subject=email.subject,
            error_type=type(exc).__name__,
            explanation=explanation,
        )
        self.workflow.store.save_audit_event(
            AuditEvent(
                event_type="chargeback_intake_failed",
                actor="chargeback_dispute_agent",
                action="manual_review_required",
                resource_type="gmail_message",
                resource_id=email.message_id,
                details={
                    "subject": email.subject,
                    "error_type": type(exc).__name__,
                    "explanation": explanation,
                },
                severity=Severity.MEDIUM,
            )
        )
        return failure

    async def scan_and_prepare(
        self,
        *,
        lookback_days: int = 60,
        senders: Sequence[str] = (),
        max_results: int = 100,
        default_timezone: str = "America/New_York",
    ) -> ChargebackScanResult:
        emails = await self.gmail.search_disputes(
            lookback_days=lookback_days,
            senders=senders,
            max_results=max_results,
        )
        result = ChargebackScanResult()
        for email in emails:
            try:
                notice = self.workflow.parser.parse(
                    email,
                    default_timezone=default_timezone,
                    allowed_hosts=self.workflow.policy.allowed_portal_hosts,
                )
                sales = await self.lightspeed.find_candidates(
                    notice,
                    date_window_days=self.workflow.policy.date_window_days,
                )
                evidence = await self._resolve_evidence(email, sales)
                package = self.workflow.prepare(
                    email=email,
                    sales=sales,
                    evidence=evidence,
                    default_timezone=default_timezone,
                )
                result.packages.append(package)
            except Exception as exc:
                result.failures.append(self._record_failure(email, exc))
        return result
