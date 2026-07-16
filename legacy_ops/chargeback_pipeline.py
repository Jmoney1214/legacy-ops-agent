from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Sequence

from .chargebacks import (
    ChargebackError,
    ChargebackWorkflow,
    DisputePackage,
    EmailMessage,
    EvidenceDocument,
)
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
    """Orchestrates Gmail intake, Lightspeed lookup, and approval preparation.

    The pipeline does not submit a dispute form. Browser submission remains a
    separate approval-gated executor so the read/prepare path cannot acquire an
    accidental financial side effect.
    """

    def __init__(
        self,
        *,
        gmail: GmailDisputeClient,
        lightspeed: LightspeedChargebackSaleClient,
        workflow: ChargebackWorkflow,
        evidence_resolver: EvidenceResolver,
    ):
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
        if hasattr(value, "__await__"):
            return await value  # type: ignore[misc]
        return value  # type: ignore[return-value]

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
            except ChargebackError as exc:
                result.failures.append(
                    IntakeFailure(
                        message_id=email.message_id,
                        subject=email.subject,
                        error_type=type(exc).__name__,
                        explanation=str(exc),
                    )
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
                            "explanation": str(exc),
                        },
                        severity=Severity.MEDIUM,
                    )
                )
        return result
