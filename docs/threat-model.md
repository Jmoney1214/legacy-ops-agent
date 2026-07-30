# Legacy Ops Advisory Board Threat Model

## Purpose

This threat model covers the planned advisory board, Outlook integration, approval-gated execution, dashboard, worker, memory, and tracing layers. It also documents the security assumptions inherited from the current deterministic services and agent platform.

Phase 0 adds documentation only. Controls described as planned are not yet production claims.

## Protected assets

### Credentials and cryptographic material

- OpenAI API keys;
- Microsoft Graph client credentials, certificates, refresh tokens, access tokens, webhook client state, and subscription identifiers;
- Lightspeed credentials and tokens;
- MerchantOS browser state and session cookies;
- database credentials;
- application session secrets;
- encryption and approval-signing keys;
- GitHub and deployment credentials.

### Business data

- owner mailbox contents;
- customer-support messages and attachments;
- customer names, addresses, phones, emails, order identifiers, and complaint details;
- sales, inventory, reconciliation, payout, refund, and chargeback records;
- vendor, legal, compliance, and shipping communications;
- approval decisions and audit history;
- advisory evidence packets, leader opinions, board decisions, and outcomes;
- memory records and owner preferences.

### Integrity-critical records

- agent manifests and artifact digests;
- leader profile versions and approved source registries;
- evidence-item provenance;
- tool specifications and approval policies;
- normalized tool arguments and idempotency keys;
- lifecycle state;
- trace sequence and parent relationships;
- external execution confirmations.

## Actors and failure sources

- unauthenticated internet attacker;
- authenticated user without owner or approver authority;
- malicious customer, vendor, or sender;
- malicious or compromised attachment, link, or HTML body;
- compromised Microsoft, OpenAI, Lightspeed, MerchantOS, GitHub, or deployment credential;
- prompt-injection content in email, documents, websites, or tool output;
- buggy or misconfigured model, agent, tool, router, webhook, worker, or migration;
- insider with excessive mailbox, database, deployment, or source-control access;
- replayed webhook, approval, queue job, or external request;
- dependency or supply-chain compromise;
- accidental disclosure through logs, traces, tests, screenshots, support tickets, or pull requests.

## Trust boundaries

```text
Owner browser / mobile device
        |
        v
Dashboard authentication, authorization, CSRF, and session boundary
        |
        +---- API validation boundary
        |
        +---- Durable database and queue boundary
        |
        +---- Background worker boundary
        |
        +---- OpenAI API and model-output boundary
        |
        +---- Microsoft Graph and webhook boundary
        |
        +---- Lightspeed / marketplace / MerchantOS boundary
        |
        +---- GitHub Actions / deployment boundary
```

Data from outside the trusted database is untrusted until validated. Model output remains untrusted until schema validation, evidence checks, policy evaluation, and approval checks complete.

## Threats and required controls

### 1. Prompt injection through email or evidence

**Threat**

An email, attachment, webpage, tool response, or quoted conversation tells an agent to ignore policy, reveal secrets, change recipients, call tools, or treat unverified text as instructions.

**Controls**

- separate system/developer instructions from untrusted content;
- label external content as data;
- compile evidence deterministically before deliberation;
- allowlist evidence fields supplied to each agent;
- use structured outputs;
- reject tool calls not declared by the manifest;
- run tool-input guardrails;
- require exact approval for side effects;
- test adversarial email and attachment cases;
- never place credentials or privileged instructions in agent-visible content.

### 2. Secret exfiltration

**Threat**

Secrets enter prompts, traces, audit records, exceptions, test fixtures, screenshots, model output, or source control.

**Controls**

- use environment variables or secret files managed by the deployment platform;
- never expose raw tokens to model context;
- redact recursively before persistence and tracing;
- block known secret filenames and high-confidence secret patterns from governed artifacts;
- keep OAuth tokens in a dedicated encrypted credential store;
- disable request/response body logging for credential endpoints;
- scan pull requests and CI output for secret patterns;
- rotate any credential exposed in chat, logs, or Git history.

### 3. Unauthorized mailbox access

**Threat**

The service receives broader Microsoft Graph access than required or reads unrelated mailboxes and folders.

**Controls**

- begin with read-only permission;
- restrict runtime access to the configured owner and support mailboxes;
- separate `owner_private` and `customer_support` policies;
- retrieve only required messages and fields;
- record mailbox, folder, message ID, and reason for access;
- prevent board leaders from browsing a mailbox directly;
- provide leaders only evidence items selected for the request;
- review tenant-wide permissions before production approval.

### 4. Unauthorized or duplicate email sending

**Threat**

The system sends an unapproved message, sends from the wrong mailbox, changes recipients after approval, or sends the same message twice.

**Controls**

- do not grant send permission during the read-only phase;
- separate draft and send tools;
- require owner approval bound to sender, recipients, normalized subject/body digest, attachments, and conversation ID;
- expire approvals;
- invalidate approval after any material edit;
- use a stable idempotency key;
- record Microsoft Graph message and request identifiers;
- prevent self-approval;
- require reauthentication for dashboard approval actions.

### 5. Financial or legal mutation without authority

**Threat**

An advisory recommendation causes a refund, credit, purchase order, payment promise, dispute, policy change, or legal communication without authorization.

**Controls**

- board agents have no side-effecting tools;
- route proposed actions through deterministic policy classification;
- require an exact approval action for every high-impact tool;
- keep arithmetic and record matching deterministic;
- validate amounts, currency, records, recipients, deadlines, and evidence;
- make approval and execution state transitions atomic;
- record external confirmation before marking execution complete.

### 6. Fabricated leader opinion or false consensus

**Threat**

The chair invents a leader's answer, hides dissent, claims a failed leader agreed, or presents an unsupported position as belonging to a real person.

**Controls**

- run all three leaders independently from the same immutable evidence packet;
- save raw structured opinions before synthesis;
- require exactly three valid opinions for a complete board;
- store leader and profile version with every opinion;
- surface dissent and unresolved disagreement;
- prohibit identity or representation claims;
- require source-backed profile principles;
- return `insufficient_evidence` for unsupported profile conclusions;
- display that outputs are AI-generated advisory simulations.

### 7. Evidence tampering or provenance loss

**Threat**

A record is changed after deliberation, an evidence reference points to a different record, or the board uses unverifiable data.

**Controls**

- freeze the evidence packet before leader execution;
- assign stable evidence IDs;
- store source identifiers, observation time, adapter version, and content digest when appropriate;
- bind opinions and decisions to the packet ID and digest;
- prohibit in-place edits to accepted evidence and opinions;
- use superseding records rather than destructive mutation;
- reject missing or ambiguous source records.

### 8. Webhook spoofing, delay, or replay

**Threat**

An attacker submits fake Microsoft Graph notifications, legitimate notifications are replayed, or slow processing causes delivery failure.

**Controls**

- implement Graph validation correctly;
- validate the configured client-state value;
- acknowledge quickly and enqueue processing;
- never run model or external side-effect workflows in the webhook request;
- deduplicate notifications;
- persist subscription and resource identifiers;
- use delta sync to recover missed changes;
- renew subscriptions before expiration;
- process lifecycle notifications;
- rate-limit invalid webhook requests.

### 9. Job replay and concurrent execution

**Threat**

Workers process the same task simultaneously, resume an old approval, or repeat an external mutation after a timeout.

**Controls**

- durable queue states;
- database row locks or equivalent atomic claims;
- idempotency keys for each handler and external mutation;
- bounded retries and dead-letter state;
- compare-and-swap lifecycle transitions;
- approval expiration and exact payload hash;
- external confirmation lookup before retrying an uncertain mutation.

### 10. Cross-tenant or cross-customer data leakage

**Threat**

One mailbox, customer, task, session, or board request receives context belonging to another.

**Controls**

- scope every database query by mailbox, task, request, and authorization context;
- use stable session namespaces;
- avoid global mutable agent/session state;
- test cross-record access attempts;
- minimize retrieved context;
- treat cached data as scoped and expiring;
- enforce server-side authorization on every API route.

### 11. Memory poisoning

**Threat**

A malicious email or model output becomes a trusted business fact or operating procedure.

**Controls**

- separate session history from verified memory;
- use proposed, verified, rejected, and superseded states;
- require provenance for semantic facts;
- require human or deterministic verification before promotion;
- prohibit autonomous procedural-memory writes;
- retain who proposed and who verified each record;
- allow rollback through superseding versions.

### 12. Excessive model cost or runaway loops

**Threat**

An input triggers repeated model calls, tool loops, leader retries, or oversized context.

**Controls**

- enforce maximum iterations, runtime, tool calls, repeated failures, and estimated cost;
- limit evidence size and attachment processing;
- run the three leaders once per board attempt unless a governed retry is created;
- use queue-level retry budgets;
- record actual model and usage;
- alert on repeated failures or abnormal spend;
- require approval before increasing production budgets.

### 13. Dependency and CI compromise

**Threat**

A dependency, workflow, action, or pull request extracts secrets or changes production behavior.

**Controls**

- pin or constrain dependencies intentionally;
- review dependency changes separately;
- use protected GitHub environments for production secrets;
- do not provide production secrets to untrusted pull-request runs;
- require CI, complete diff review, and no unresolved threads;
- preserve artifact digests and governed releases;
- keep deployment and runtime credentials separate from normal CI where possible.

### 14. Sensitive data in observability systems

**Threat**

Email bodies, attachments, addresses, customer information, or tokens are copied into traces and logs.

**Controls**

- store identifiers and normalized metadata instead of raw content when possible;
- redact before trace creation;
- prohibit raw credentials and payment-card data;
- apply retention and deletion schedules;
- restrict access to traces and audit records;
- test representative redaction cases;
- document any external trace processor and its data handling before enabling it.

### 15. Dashboard compromise

**Threat**

An attacker steals a session, forges an approval request, reads private owner data, or invokes an API route directly.

**Controls**

- authenticated owner access;
- secure, HTTP-only, same-site cookies;
- CSRF protection;
- server-side role checks;
- rate limiting and lockout controls;
- reauthentication for sensitive approvals;
- content-security policy and output escaping;
- no secrets in browser responses or local storage;
- audit all approval, rejection, and execution actions.

## Data classification

| Class | Examples | Default handling |
|---|---|---|
| Public | store hours, public policies, public product information | usable with provenance |
| Internal | task status, operational metrics, drafts | authenticated access |
| Confidential | mailbox content, customer details, vendor records, payouts | minimum access, encrypted storage, redacted traces |
| Restricted | credentials, OAuth tokens, full card data, legal secrets | never model-visible; dedicated secret storage or prohibited |

## Security test requirements

Each applicable phase adds tests for:

- unauthorized API access;
- cross-mailbox and cross-task isolation;
- prompt injection;
- schema rejection;
- secret redaction;
- self-approval;
- changed-payload approval replay;
- duplicate webhook and duplicate external execution;
- leader timeout and false-consensus prevention;
- evidence immutability;
- memory-verification boundaries;
- retry and dead-letter behavior.

## Current limitations

The following target controls are not yet implemented as of Phase 0:

- authenticated dashboard;
- Microsoft Graph credentials and mailbox restrictions;
- webhook and delta-sync processing;
- durable Postgres queue;
- three-leader orchestration;
- profile source registry;
- resumable runtime approvals;
- production memory;
- cross-service trace and outcome dashboards.

These limitations must remain visible in documentation and release notes until the corresponding phase passes CI, review, staging, and production approval.
