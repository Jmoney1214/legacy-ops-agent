# Legacy Ops Advisory Board Architecture

## Status

This document defines the target architecture and the current implementation boundary.

As of Phase 0, the repository contains deterministic operational services, a governed agent build platform, a minimal OpenAI Agents SDK HTTP endpoint, Docker deployment files, and Python 3.11/3.13 CI. The advisory board, Microsoft Graph mailbox integration, production worker, dashboard, durable Postgres runtime, and three leader profiles are not yet implemented.

## Existing system

### Runtime entrypoint

The current container starts Uvicorn against `http_server:app`. The current HTTP service exposes a health endpoint and a single `/run` endpoint backed by one module-level Agents SDK agent. It may register a read-only Lightspeed item tool when the legacy adapter imports successfully.

This endpoint is a prototype compatibility surface. It is not the final advisory-board runtime.

### Deterministic operational services

The repository already contains deterministic capabilities for:

- approval requests and human decisions;
- redacted audit events;
- weekly Uber Eats and DoorDash reconciliation against Lightspeed;
- chargeback intake, deterministic transaction matching, evidence planning, response preparation, MerchantOS preview, and approval-gated submission controls;
- browser automation restricted by host, selectors, evidence requirements, and approval state.

These services remain authorities for their business logic. They may later be registered as tools; their arithmetic, matching, authorization, and side-effect rules must not be reimplemented in prompts.

### Governed agent platform

The merged platform provides:

- versioned manifests;
- tool contracts and least-privilege permissions;
- loop and cost limits;
- artifact hashing;
- lifecycle registration;
- trace storage;
- ordered validation, test, eval, review, staging, and release gates;
- approval-bound production promotion.

Every advisory-board leader, router, drafting agent, and execution agent must pass through this platform.

### Persistence

Existing operational services use SQLite with WAL durability. This is suitable for one process and deterministic local or single-instance operation. It is not the target persistence layer for horizontally scaled web and worker services.

`DATABASE_URL` exists as a reserved configuration boundary. A later phase will add a Postgres/Supabase adapter without removing SQLite test support.

## Target system

```text
Owner dashboard request or Outlook notification
                    |
                    v
          Authenticated task intake
                    |
                    v
       Deterministic routing and policy
                    |
                    v
             Evidence compiler
                    |
                    v
       Immutable, source-linked evidence packet
                    |
          +---------+---------+
          |         |         |
          v         v         v
      Leader 1  Leader 2  Leader 3
      independent, parallel, structured
          |         |         |
          +---------+---------+
                    |
                    v
        Raw opinions stored unchanged
                    |
                    v
         Board synthesis and dissent report
                    |
                    v
             Deterministic policy gate
                    |
           +--------+--------+
           |                 |
           v                 v
     Read-only result   Approval request
                             |
                             v
                    Approved execution tool
                             |
                             v
                Audit, trace, and outcome record
```

## Main components

### 1. API service

Responsibilities:

- owner authentication and authorization;
- task and board-request APIs;
- approval decisions;
- dashboard assets;
- Microsoft Graph webhook acknowledgement;
- health and readiness endpoints;
- retrieval of stored runs, opinions, drafts, audit events, and outcomes.

The API service must not perform long model runs or slow external workflows inside webhook or interactive request handlers. It writes durable jobs and returns accepted/status responses.

### 2. Background worker

Responsibilities:

- consume durable jobs;
- compile evidence;
- run the three leaders in parallel;
- synthesize board decisions;
- classify mailbox messages;
- generate drafts;
- resume approved tool executions;
- retry transient failures;
- write trace, cost, and outcome records.

Every handler must be idempotent and bounded by attempts, runtime, tool calls, and cost.

### 3. Evidence compiler

The compiler turns a request into an immutable `EvidencePacket` containing only verified records needed for that decision.

Each evidence item includes:

- stable evidence ID;
- source type and source name;
- source record ID when available;
- observation time;
- normalized claims supported;
- sensitivity classification;
- retrieval or adapter version;
- optional content digest.

The evidence packet is frozen before leader deliberation. All three leaders receive the exact same packet.

### 4. Three leader-advisor agents

The leaders are advisory specialists, not autonomous executives.

Hard requirements:

- role-based internal IDs;
- versioned profile documents;
- identical evidence input;
- independent execution;
- structured `LeaderOpinion` output;
- no access to another leader's output before submission;
- explicit `insufficient_evidence` state;
- no side-effecting tools;
- no claims of being or representing a real person.

The exact names and source-backed profiles are a later clarification gate. Until then the implementation uses neutral role IDs.

### 5. Board synthesizer

The synthesizer receives the immutable evidence packet and three valid stored opinions. It produces:

- recommendation;
- consensus level;
- supporting and dissenting leaders;
- unresolved disagreements;
- evidence references;
- risks;
- proposed next actions;
- whether approval is required.

It may summarize but may not edit the stored opinions. It cannot declare a complete board when fewer than three valid opinions exist.

### 6. Deterministic policy and approval service

The policy service decides whether a proposed action is:

- read-only and reportable;
- an internal audited write;
- an external communication;
- a financial mutation;
- legal/compliance work;
- a production release.

The approval service binds a human decision to exact normalized tool arguments, requester, artifact/profile versions, expiration, and idempotency key. A changed payload requires a new approval.

### 7. Tool layer

Tools are narrow adapters around deterministic services and external systems.

Initial categories:

- Microsoft Graph mailbox reads;
- Outlook draft creation;
- Outlook sending after approval;
- Lightspeed reads;
- reconciliation reporting;
- chargeback case retrieval and preparation;
- approval creation and status lookup;
- verified business-memory reads;
- outcome recording.

Function tools remain the default. MCP is deferred until the same approved tool surface must be consumed by multiple independent clients.

### 8. Microsoft 365 integration

Runtime mailbox access will use Microsoft Graph directly.

Mailbox roles:

- `info@legacywineandliquor.com`: `owner_private`;
- `support@legacywineandliquor.com`: `customer_support`.

Addresses are supplied through environment configuration.

The initial integration is read-only and uses:

- Graph authentication;
- webhook subscriptions for near-real-time notifications;
- per-mailbox delta cursors for recovery and reconciliation;
- duplicate-notification protection;
- durable queue handoff.

The existing Gmail chargeback adapter remains unchanged until a reviewed Outlook migration phase.

### 9. Memory and sessions

Two concepts remain separate:

- conversation/session state for continuity;
- verified business memory for reusable facts, preferences, procedures, and outcomes.

A session message is not automatically a verified fact. Agents may propose memory; verification and procedural changes require explicit governance.

### 10. Observability

Every run is linked to:

- request ID;
- task or board request ID;
- agent ID and version;
- profile and artifact versions;
- model actually used;
- evidence packet ID;
- tool calls;
- approvals;
- latency, token, and estimated-cost fields;
- final status;
- outcome records.

Secrets and restricted mailbox/customer content are excluded or redacted before tracing.

## Trust boundaries

```text
Browser / owner device
        |
        v
Authenticated API and CSRF boundary
        |
        +---- Postgres/Supabase persistence boundary
        |
        +---- Worker queue boundary
        |
        +---- OpenAI API boundary
        |
        +---- Microsoft Graph boundary
        |
        +---- Lightspeed / MerchantOS boundary
```

External messages and attachments are untrusted. Model output is untrusted until schema validation and deterministic policy checks pass. Tool execution is untrusted until the external response is validated and recorded.

## Deployment shape

Target deployment uses separate processes:

- `legacy-ops-web`: HTTP API and dashboard;
- `legacy-ops-worker`: continuous job consumer;
- `legacy-ops-maintenance`: scheduled subscription renewal, delta sync, expiration, cleanup, and outcome tasks;
- managed Postgres/Supabase database.

The current single Docker web process remains until the worker and durable queue phases are implemented and staged.

## Phase boundaries

Phase 0 changes documentation and repository controls only.

No phase may claim the following before it exists and is verified:

- a live three-leader advisory board;
- production Outlook access;
- automatic customer email sending;
- durable cross-worker sessions;
- production Postgres migration;
- a deployed dashboard;
- real-person profile fidelity;
- autonomous financial or legal execution.
