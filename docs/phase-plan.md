# Legacy Ops Advisory Board Phase Plan

## Purpose

This plan converts the current single-agent prototype and deterministic operational services into a governed, production-usable advisory board, Outlook workflow, approval system, worker, memory layer, and dashboard.

Each phase is delivered through its own branch and pull request. No phase is complete until CI passes on Python 3.11 and 3.13, the complete diff is reviewed, all findings are resolved, and the pull request is merged.

## Existing baseline

The repository already contains:

- approval and audit control-plane services;
- deterministic marketplace reconciliation;
- deterministic chargeback preparation and approval-gated execution controls;
- a governed agent build and release platform;
- a minimal OpenAI Agents SDK HTTP endpoint;
- Docker deployment files;
- Python 3.11 and 3.13 GitHub Actions CI.

The existing Gmail chargeback intake remains unchanged until the Microsoft Graph migration phase explicitly replaces or adapts it.

---

## Phase 0 — Repository contract and architecture baseline

### Goal

Establish non-negotiable engineering, safety, architecture, and sequencing rules before runtime implementation.

### Deliverables

- expanded `AGENTS.md`;
- `docs/architecture.md`;
- `docs/threat-model.md`;
- `docs/phase-plan.md`.

### Non-goals

- no runtime code changes;
- no OpenAI API calls;
- no Microsoft Graph configuration;
- no database migration;
- no deployment change.

### Validation

- documentation reflects actual repository entrypoints and current capabilities;
- existing CI remains green;
- complete documentation diff reviewed.

### Exit criteria

- Python 3.11 CI passes;
- Python 3.13 CI passes;
- no unresolved review findings;
- merged to `main`.

---

## Phase 1 — Runtime kernel and configuration

### Goal

Replace the module-level prototype agent with a controlled runtime service while keeping the existing HTTP entrypoint compatible.

### Deliverables

```text
legacy_ops/config.py
legacy_ops/runtime/agent_factory.py
legacy_ops/runtime/model_router.py
legacy_ops/runtime/run_context.py
legacy_ops/runtime/session_factory.py
legacy_ops/runtime/run_service.py
legacy_ops/api/app.py
legacy_ops/api/routes_health.py
http_server.py                         # compatibility import only
```

### Required behavior

- typed environment configuration;
- startup validation;
- request and run IDs;
- explicit model configuration;
- actual model used stored with each run;
- timeout, max-turn, tool-call, repeated-failure, and cost limits;
- `/health/live` and `/health/ready`;
- no global mutable business agent or session state;
- no mailbox or side-effect tools.

### Credential gate

Before editing, running, testing, or configuring OpenAI-calling code, confirm a usable `OPENAI_API_KEY` is available through an approved secure environment path.

### Tests

- configuration parsing and missing-secret behavior;
- model routing;
- request-context isolation;
- timeout and limit behavior;
- compatibility entrypoint;
- no live API call in normal unit tests.

### Exit criteria

- local/unit tests pass;
- compileall passes;
- CI passes on both Python versions;
- code review complete;
- no production deployment change unless staging smoke validation is included and approved.

---

## Phase 2 — Advisory-board contracts and storage

### Goal

Define immutable schemas and durable records before implementing model behavior.

### Deliverables

```text
legacy_ops/advisory_board/schemas.py
legacy_ops/advisory_board/evidence.py
legacy_ops/advisory_board/policy.py
legacy_ops/advisory_board/repository.py
migrations/002_advisory_board.sql
```

### Core records

- board request;
- evidence packet;
- evidence item;
- leader profile and version;
- leader opinion;
- board decision;
- proposed action;
- board outcome.

### Required states

```text
requested
collecting_evidence
evidence_ready
deliberating
incomplete
synthesizing
decided
approval_pending
executing
completed
failed
cancelled
```

### Hard rules

- evidence packet immutable after deliberation begins;
- accepted leader opinions immutable;
- decision binds to exactly three opinion IDs for complete status;
- no tool execution from a decision row;
- schema and migration remain compatible with SQLite tests and planned Postgres adapter.

### Tests

- Pydantic/contract validation;
- state transitions;
- evidence and opinion immutability;
- duplicate/idempotent creation;
- migration compatibility;
- invalid complete-board construction rejected.

### Exit criteria

Same branch, CI, review, and merge gates as Phase 1.

---

## Phase 3 — Three-leader orchestration and synthesis

### Goal

Run three independent leader agents from one immutable evidence packet and synthesize only after all required opinions are valid.

### Deliverables

```text
legacy_ops/advisory_board/leaders/base.py
legacy_ops/advisory_board/orchestrator.py
legacy_ops/advisory_board/synthesizer.py
legacy_ops/advisory_board/prompts.py
```

### Required behavior

- three leader runs launched independently and concurrently;
- identical evidence packet supplied to all three;
- no leader sees another opinion before its own is stored;
- structured `LeaderOpinion` output;
- raw opinion persisted before synthesis;
- leader timeout, invalid output, or failure creates `incomplete` board status;
- synthesizer surfaces dissent and cannot modify raw opinions;
- no side-effect tools;
- neutral role IDs until approved profiles exist.

### Tests and evals

- complete consensus;
- two-to-one disagreement;
- three-way disagreement;
- one timeout;
- invalid structured output;
- insufficient evidence;
- attempted fabricated quote;
- attempted false consensus;
- cost and runtime limits.

### Exit criteria

- all deterministic tests pass;
- focused local eval harness passes configured threshold;
- CI and review gates pass;
- no production external actions.

---

## Phase 4 — Source-backed leader profiles

### Goal

Create versioned advisory profiles from authoritative public material without impersonating or inventing private beliefs.

### Required clarification

The owner must provide the exact three leader names before this phase starts.

### Deliverables

```text
legacy_ops/advisory_board/leaders/profiles/<leader_id>.md
legacy_ops/advisory_board/source_registry.py
docs/leader-profile-standard.md
docs/reviews/leader-profile-review.md
```

### Source policy

Use only reviewed public sources such as official writings, books, shareholder letters, verified speeches, published interviews, company/foundation material, and directly attributable statements.

### Required profile content

- display label;
- internal role ID;
- semantic profile version;
- approved source registry;
- cited decision principles;
- known domains;
- explicit limitations;
- mandatory label that output is an AI advisory simulation based on public principles.

### Prohibitions

- no claim to be or represent the real person;
- no fabricated quote;
- no unsupported private belief;
- no personality-cloning instruction;
- no use of an image to identify the person.

### Tests and evals

- every profile principle maps to a source entry;
- unsupported questions produce `insufficient_evidence`;
- identity and representation claims rejected;
- quote requires exact approved source evidence;
- profile version stored with every opinion.

### Exit criteria

- owner approves the three profiles;
- code and content review complete;
- CI/evals pass;
- merged before profiles may be used in staging.

---

## Phase 5 — Evidence compiler and read-only business tools

### Goal

Build a deterministic evidence layer so leaders reason from verified records rather than unrestricted prompts or direct system browsing.

### Deliverables

```text
legacy_ops/evidence/compiler.py
legacy_ops/evidence/provenance.py
legacy_ops/tools/outlook_read.py       # adapter contract or mock until Graph phase
legacy_ops/tools/lightspeed_read.py
legacy_ops/tools/reconciliation.py
legacy_ops/tools/chargebacks.py
legacy_ops/tools/approvals.py
legacy_ops/tools/business_memory.py
```

### Initial read-only tools

- get Outlook message by approved identifier;
- search Outlook messages within bounded criteria;
- get Lightspeed sale;
- get inventory record;
- get reconciliation summary;
- get chargeback case;
- get approval history;
- get verified business metric;
- get verified memory record.

### Required behavior

- strict input and output schemas;
- explicit not-found, ambiguous, stale, and unauthorized states;
- source IDs and observation timestamps;
- sensitive-data minimization;
- no leader receives credentials or unrestricted connector access;
- no financial arithmetic or record matching performed by the model.

### Tests and evals

- ambiguous transaction match;
- stale record;
- unauthorized mailbox/task access;
- prompt-injection content in a record;
- secret and PII redaction;
- evidence packet size and source limits;
- provenance completeness.

### Exit criteria

Read-only tools only; no sending, refunds, credits, submissions, or system mutation.

---

## Phase 6 — Microsoft Graph read-only mailbox integration

### Goal

Connect the owner and support mailboxes using Microsoft Graph, webhooks, and delta synchronization.

### Required clarification

Before assigning permissions, confirm:

- whether the addresses are separate user mailboxes, a shared mailbox, or aliases;
- whether both are in the same Microsoft 365 tenant;
- who can grant Entra application permissions and admin consent.

The user has confirmed the business roles:

- owner/private: `info@legacywineandliquor.com`;
- customer-facing support: `support@legacywineandliquor.com`.

### Deliverables

```text
legacy_ops/outlook/auth.py
legacy_ops/outlook/graph_client.py
legacy_ops/outlook/subscriptions.py
legacy_ops/outlook/webhook.py
legacy_ops/outlook/delta_sync.py
legacy_ops/outlook/repository.py
legacy_ops/api/routes_outlook.py
migrations/003_outlook.sql
docs/outlook-setup.md
```

### Configuration

```text
OUTLOOK_OWNER_MAILBOX
OUTLOOK_SUPPORT_MAILBOX
MS_TENANT_ID
MS_CLIENT_ID
MS_CLIENT_SECRET              # pilot only when approved
MS_CERTIFICATE_PATH           # production target
MS_CERTIFICATE_THUMBPRINT     # production target
GRAPH_WEBHOOK_CLIENT_STATE
APP_BASE_URL
```

### Required behavior

- read-only permission first;
- mailbox access restricted to approved addresses;
- webhook validation and client-state checks;
- immediate acknowledgement and durable enqueue;
- duplicate-notification protection;
- per-mailbox/folder delta cursor;
- subscription renewal and lifecycle handling;
- no OpenAI call or external action inside webhook handler;
- no send permission.

### Tests

- validation-token response;
- invalid client state;
- duplicate webhook;
- missed-event recovery through delta sync;
- cursor persistence;
- subscription renewal;
- cross-mailbox isolation;
- transient Graph failure and retry.

### Exit criteria

- approved test messages can be ingested from both configured roles;
- no send scope or send tool exists;
- staging review complete.

---

## Phase 7 — Inbox routing, support tasks, and drafts

### Goal

Convert ingested messages into classified, reviewable tasks and drafts without sending externally.

### Deliverables

```text
legacy_ops/inbox/router.py
legacy_ops/inbox/schemas.py
legacy_ops/inbox/task_service.py
legacy_ops/inbox/drafting.py
legacy_ops/inbox/policies.py
migrations/004_inbox.sql
evals/outlook_cases.jsonl
```

### Initial classes

```text
customer_support
sales_opportunity
order_status
delivery_issue
shipping_issue
return_or_damage
website_issue
vendor
invoice
chargeback
marketplace
legal_or_regulatory
spam
unknown
```

### Required behavior

- `support@` routes to customer-support workflows;
- `info@` routes to restricted owner workflows;
- classification and extraction use structured outputs;
- drafts stored in the database;
- no send tool;
- unknown, ambiguous, legal, financial, alcohol-shipping, refund, credit, or threat cases escalate;
- advisory board invoked only by policy or explicit owner request, not for every message.

### Tests and evals

- each routing category;
- mailbox-role separation;
- malicious HTML/instructions;
- missing order identifier;
- multiple possible orders;
- private-owner message not exposed to support workflows;
- draft grounded in source records;
- no external send.

### Exit criteria

- staging dashboard/API can display tasks and drafts;
- send remains disabled.

---

## Phase 8 — Human approval and resumable execution

### Goal

Allow high-impact tools to pause, obtain exact approval, survive process restarts, and resume safely.

### Deliverables

```text
legacy_ops/approvals/runtime.py
legacy_ops/approvals/policies.py
legacy_ops/approvals/resume.py
legacy_ops/approvals/signatures.py
legacy_ops/tools/outlook_send.py
migrations/005_runtime_approvals.sql
evals/approval_cases.jsonl
```

### Sensitive action examples

- send Outlook email;
- issue customer credit or refund;
- submit chargeback;
- change inventory;
- create vendor order;
- change campaign budget;
- publish public content;
- deploy an agent version.

### Required behavior

- run interruption saved durably;
- approval binds to exact normalized arguments and digest;
- requester and approver separation;
- decision reason and expiration;
- changed payload invalidates approval;
- rejection and cancellation paths;
- idempotent resume;
- duplicate external execution blocked;
- external confirmation recorded before completion.

### Tests and evals

- approve, reject, expire, cancel;
- self-approval attempt;
- changed recipients/body/amount;
- replayed approval;
- worker restart before resume;
- external timeout with uncertain execution;
- duplicate send prevention.

### Exit criteria

- all sensitive tools remain approval-gated;
- no broad standing approval.

---

## Phase 9 — Production sessions and verified business memory

### Goal

Add durable session continuity and governed reusable business knowledge without treating model output as fact.

### Deliverables

```text
legacy_ops/memory/sessions.py
legacy_ops/memory/facts.py
legacy_ops/memory/preferences.py
legacy_ops/memory/outcomes.py
legacy_ops/memory/retrieval.py
migrations/006_memory.sql
```

### Memory separation

- session/conversation history;
- proposed business fact;
- verified business fact;
- owner preference;
- reviewed operating procedure;
- measured outcome.

### States

```text
proposed
verified
rejected
superseded
```

### Required behavior

- stable scoped session IDs;
- provenance and verifier identity;
- agents may propose but not verify facts;
- no autonomous procedural-memory write;
- retention and deletion policies;
- owner-editable preferences;
- cross-task and cross-mailbox isolation.

### Tests

- session continuity across processes;
- memory poisoning attempt;
- unverified fact exclusion;
- superseding record;
- retention cleanup;
- authorization isolation.

### Exit criteria

- memory review and privacy review complete;
- no raw credentials or restricted data stored as memory.

---

## Phase 10 — Owner dashboard and API

### Goal

Make the system usable from an authenticated mobile and desktop interface.

### Screens

- dashboard;
- inbox;
- tasks;
- advisory board;
- board request detail;
- approvals;
- draft emails;
- agent runs;
- audit log;
- settings.

### Board detail requirements

Display:

- original request;
- evidence packet;
- raw Leader 1 opinion;
- raw Leader 2 opinion;
- raw Leader 3 opinion;
- synthesis;
- dissent and unresolved disagreement;
- proposed actions;
- approval state;
- execution and outcome state.

### Security requirements

- owner authentication;
- server-side role checks;
- secure session cookie;
- CSRF protection;
- output escaping and content-security policy;
- reauthentication for sensitive approvals;
- no secrets or raw tokens returned to the browser;
- audit every approval action.

### Tests

- unauthorized route access;
- CSRF;
- cross-record access;
- mobile smoke tests;
- approval reauthentication;
- safe rendering of malicious email HTML.

### Exit criteria

- staging dashboard works on mobile and desktop;
- security review complete.

---

## Phase 11 — Durable worker, queue, and scheduling

### Goal

Separate interactive HTTP traffic from asynchronous model and connector work.

### Deliverables

```text
legacy_ops/worker/main.py
legacy_ops/worker/queue.py
legacy_ops/worker/handlers.py
legacy_ops/jobs/maintenance.py
migrations/007_queue.sql
render.yaml
```

### Target services

- web/API service;
- continuous background worker;
- scheduled maintenance job;
- managed Postgres/Supabase.

### Initial queue design

Use a Postgres-backed durable queue with:

- job status;
- attempt count;
- available time;
- idempotency key;
- lease/lock owner and expiration;
- last error;
- dead-letter status.

Redis is deferred until measured concurrency or latency requires it.

### Maintenance responsibilities

- renew Graph subscriptions;
- run delta reconciliation;
- expire approvals;
- retry eligible jobs;
- clean temporary data;
- enforce retention;
- measure pending outcomes.

### Tests

- concurrent job claim;
- worker crash and lease recovery;
- bounded retry;
- dead-letter behavior;
- idempotent handler;
- maintenance overlap prevention.

### Exit criteria

- web requests do not run long board or mailbox workflows inline;
- restart and concurrency tests pass.

---

## Phase 12 — Observability, tracing, cost, and outcomes

### Goal

Provide enough traceability to diagnose decisions, tool calls, failures, cost, and business results without leaking sensitive content.

### Deliverables

```text
legacy_ops/observability/tracing.py
legacy_ops/observability/metrics.py
legacy_ops/observability/redaction.py
legacy_ops/advisory_board/outcome_tracker.py
migrations/008_observability.sql
```

### Required metrics

- board run count and latency;
- leader failure count;
- incomplete-board count;
- tool-call count and failures;
- approval wait time;
- email classification accuracy;
- draft acceptance/edit/send rates;
- token use and estimated cost;
- duplicate/replay blocks;
- measured outcome success rate.

### Required behavior

- trace ID linked to every run;
- actual model and profile version stored;
- secrets and restricted message content excluded or redacted;
- alert on repeated leader failures, missed subscriptions, send failures, abnormal cost, and queue backlog;
- approved action linked to outcome review.

### Tests

- redaction;
- trace-parent ordering;
- usage/cost accounting;
- alert threshold behavior;
- outcome linkage.

### Exit criteria

- privacy review complete;
- staging metrics available.

---

## Phase 13 — Full eval and security gate

### Goal

Establish the release-blocking behavioral and security suite for the full system.

### Eval groups

#### Board

- consensus;
- two-to-one dissent;
- three-way disagreement;
- missing evidence;
- one leader timeout;
- invalid opinion;
- fabricated quote attempt;
- malicious evidence injection;
- approval-required action.

#### Outlook

- normal support request;
- sales inquiry;
- order issue;
- vendor invoice;
- chargeback;
- legal threat;
- spam;
- ambiguous request;
- malicious HTML;
- repeated webhook;
- delta recovery;
- oversized attachment.

#### Approval

- approve;
- reject;
- expire;
- self-approval;
- changed arguments;
- duplicate execution;
- unauthorized user;
- process restart and resume.

### Release threshold

A pull request cannot merge unless:

```text
unit tests pass
integration tests pass
security tests pass
contract tests pass
configured eval threshold passes
compileall passes
Python 3.11 CI passes
Python 3.13 CI passes
complete code review passes
no unresolved review threads remain
```

---

## Phase 14 — Read-only staging pilot

### Goal

Validate real workflows without allowing automatic external actions.

### Staging policy

```text
Microsoft Graph read-only
no automatic sends
no automatic financial actions
manual board requests enabled
support drafts require owner review
side-effect tools disabled or dry-run
full tracing and outcome feedback enabled
```

### Acceptance measures

- message-routing accuracy;
- false urgency and false escalation;
- evidence completeness;
- leader failure and disagreement behavior;
- draft acceptance and edit rate;
- owner approval usability;
- latency;
- token and dollar cost;
- privacy and retention compliance.

### Exit criteria

- owner accepts pilot results;
- unresolved safety issues closed;
- production-send proposal reviewed separately.

---

## Phase 15 — Controlled production sending

### Goal

Enable Outlook sending only after successful read-only staging and explicit owner approval.

### Required changes

- add approved Microsoft Graph send permission;
- verify mailbox send-as or send-on-behalf configuration;
- restrict runtime mailbox access;
- enable `send_outlook_email` with exact approval;
- bind approval to sender, recipients, subject/body digest, attachments, and conversation;
- enforce duplicate-send prevention;
- record Graph confirmation identifiers;
- add send failure and uncertain-result recovery.

### Non-goals

- no general autonomous email sending;
- no refunds, credits, purchase orders, legal correspondence, or other financial/legal actions without separate approved phases and policies.

### Exit criteria

- staging send tests pass;
- security and permission review pass;
- owner approves production release;
- governed agent release records the exact artifact and deployment digest.

---

## Decisions intentionally deferred

The following are not selected until the relevant phase has evidence and approval:

- exact three leader identities and public source profiles;
- final Microsoft mailbox topology and Entra permission model;
- final Postgres/Supabase project and migration timing;
- final deployment service split and paid resource sizes;
- Redis or another dedicated broker;
- automatic-send categories;
- retention periods for mailbox bodies and attachments;
- expanded financial, vendor, marketing, or legal execution tools.

Deferring these decisions prevents the documentation from presenting assumptions as completed architecture.
