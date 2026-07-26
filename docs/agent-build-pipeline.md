# Legacy Agent Build Pipeline

## Purpose

This pipeline is the mandatory control path for every Legacy business agent. It
separates agent creation from agent execution and prevents a prompt, code change,
tool permission, or unreviewed artifact from being promoted directly to production.

Operational services built before this platform—approvals, reconciliation, and
chargeback preparation—are deterministic capabilities. They become agent tools only
after registration, permission design, validation, eval coverage, staging, and owner
approval.

## Standard lifecycle

```text
Business task definition
  -> standard scaffold
  -> manifest and instructions
  -> tool and permission registration
  -> context and memory policy
  -> loop and cost budgets
  -> artifact digest
  -> contract validation
  -> tool validation
  -> guardrail validation
  -> unit tests
  -> agent evals
  -> code review
  -> staging validation
  -> owner production approval
  -> production release
  -> traces and failure feedback
```

An agent version progresses through:

```text
DRAFT -> VALIDATED -> REVIEWED -> STAGING -> PRODUCTION -> RETIRED
```

Invalid jumps are blocked. Production and retired versions cannot be silently rebuilt.
A changed artifact must use a new semantic version.

## Agent artifact

Every active agent uses this minimum layout:

```text
agents/<agent_id>/
  agent_manifest.yaml
  instructions.md
  tools.py
  guardrails.py
  schemas.py
  evals/
    cases.jsonl
  README.md
```

Use the CLI to create a new scaffold:

```bash
python -m legacy_ops.agent_cli scaffold inventory_agent \
  --display-name "Inventory Agent" \
  --purpose "Review inventory and surface replenishment exceptions." \
  --owner inventory_operations
```

Validate it and calculate the immutable artifact digest:

```bash
python -m legacy_ops.agent_cli validate agents/inventory_agent
```

The digest covers every approved source, policy, instruction, schema, and eval file in
the agent directory. Code-review, test, eval, staging, approval, and deployment
evidence must all reference that exact digest.

## Manifest contract

The manifest defines:

- stable `agent_id` and semantic `version`;
- narrow purpose and accountable business owner;
- risk level;
- instructions path;
- declared tools;
- minimal per-tool `read`, `write`, or `execute` permissions;
- exact approval actions;
- human-in/on/out-of-loop mode;
- loop, tool-call, runtime, repeated-failure, and cost budgets;
- context source and sensitive-data restrictions;
- memory read/write scopes and retention;
- searchable tags.

High- or critical-risk agents cannot be human-out-of-the-loop. Agents cannot directly
write procedural memory. Agents with tools must have a positive tool-call budget.

## Tool contract

Every tool is registered centrally with:

- a clear name and description;
- strict object input schema with unknown fields rejected;
- output schema;
- side-effect level;
- minimum agent risk declaration;
- exact required approval action for side effects;
- deterministic implementation.

A read-only tool cannot grant write or execute permission. Irreversible tools require
execute permission and an approval action that exactly matches the tool specification.
An unrelated approval cannot authorize a different tool.

## Loop engineering

The loop controller enforces limits before work begins:

- maximum iterations;
- maximum tool calls;
- maximum runtime;
- maximum estimated cost;
- maximum repeated identical failures;
- explicit completion, guardrail, and human-escalation stops.

A zero tool-call budget allows reasoning but blocks all tool calls. Budget failures
fail closed instead of continuing indefinitely.

## Build gates

### 1. Contract validation

Validates the manifest and binds evidence to its manifest fingerprint.

### 2. Tool validation

Confirms all declared tools exist and that permissions, side effects, risk, and exact
approval actions agree.

### 3. Guardrail validation

Requires passed permission, approval, and sensitive-data reviews. Passing this gate
moves the version from draft to validated.

### 4. Unit tests

Runs deterministic code tests against the exact artifact digest.

### 5. Agent evals

Requires at least one executed eval and zero failed cases for the gate. Each real agent
should include happy-path, missing-data, permission, approval, tool-failure, loop,
escalation, and observed-regression cases.

### 6. Code review

Requires a named reviewer and all findings resolved against the exact artifact digest.
Passing moves the version to reviewed.

### 7. Staging validation

Requires a named staging environment and passed smoke test against the exact artifact.
Passing moves the version to staging.

### 8. Production release

Creates one idempotent owner approval bound to the build, agent, version, and artifact
digest. Release requires the bound approval, matching deployment digest, deployment
identifier, and passed health check. The database atomically updates the build,
agent lifecycle, approval execution status, and audit records.

## Memory and context

Phase 1 defines policy contracts. Later platform phases implement the stores and
retrieval engine.

- Working memory: current run only.
- Episodic memory: prior run outcomes and failures.
- Semantic memory: verified business facts and records.
- Procedural memory: reviewed policies and operating procedures.

Only declared scopes may be read or written. Procedural memory changes require a
reviewed platform change rather than an autonomous agent write.

## Human control

Irreversible actions and production releases are human-in-the-loop. Lower-risk,
reversible activity may later use human-on-the-loop operation when policy and eval
evidence justify it. The approval payload is bound to the exact action and artifact;
approval for one build cannot release another.

## Observability

The registry records redacted run events with agent identity, version, event type,
duration, estimated cost, and payload. Secrets are removed before persistence.
Later phases add full run/span traces, token accounting, latency dashboards, alerts,
and production-failure replay into evals.

## Mapping to the ten engineering concepts

1. Harness engineering: build gates, fail-closed controls, budgets, deployment health.
2. Loop engineering: bounded iterations, tool calls, runtime, cost, and failures.
3. Context engineering: source allowlists, size limits, attribution, sensitive classes.
4. Tool design: strict registry, schemas, risk, side effects, exact approvals.
5. Memory architecture: explicit working, episodic, semantic, procedural scopes.
6. Orchestration patterns: lifecycle and contracts prepared for later routing.
7. Guardrails and permissions: least privilege and approval-bound execution.
8. Agent evals: required gate and standard case format.
9. Human in the loop: production and side effects require bound approval.
10. Observability and tracing: immutable registration, audits, redacted trace events.

## Phase boundary

Phase 1 does not create an LLM runtime or claim that business agents are live. It
creates the governed factory and promotion path that later agent implementations must
use.
