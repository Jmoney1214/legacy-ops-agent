# Agent Platform Phase 1 — Code Review

## Scope reviewed

- agent manifest and policy validation;
- tool registry and input/output schema validation;
- loop and budget controller;
- agent registry, lifecycle, and trace storage;
- workspace scaffold, artifact validation, and digesting;
- build gates and production approval flow;
- database migrations;
- regression tests and standard template.

## Findings corrected

### Critical / high

1. **Build approvals were not bound to the exact source artifact.**
   Added a SHA-256 artifact digest covering the agent's manifest, instructions, code,
   schemas, guardrails, documentation, and eval cases. Unit, eval, review, staging,
   approval, and deployment evidence must match the digest.

2. **The generic registry could bypass the governed production release.**
   Direct transitions into or out of production/retired states are blocked. Production
   is entered only through the approval-gated release transaction.

3. **Any agent approval action could authorize any side-effecting tool.**
   Each side-effecting tool now declares one exact required approval action. Tool
   authorization rejects unrelated approval actions.

4. **Repeated production-approval requests could create duplicate approvals.**
   The build stores one bound production approval and returns the existing pending or
   approved request idempotently.

5. **The production requester could approve the same release.**
   Release now requires a named approver different from the requester and a recorded
   decision reason.

6. **An agent version could be rebuilt from different source code.**
   A semantic version is permanently bound to one artifact digest. Source changes
   require a new version.

### Medium

7. **Tool schemas were validated only at the top level.**
   Added strict recursive object, array, enum, string-length, item-count, and numeric
   validation for both inputs and outputs. Unsupported schema keywords fail closed.

8. **Read-only tools could receive insufficient or excessive permissions.**
   Read permission is required; write and execute are rejected. Tool risk must not
   exceed the risk declared by the agent.

9. **Zero tool budgets stopped unrelated reasoning.**
   Iteration and tool reservations are now separate. A zero tool budget permits
   reasoning but blocks tool execution.

10. **Failed builds could not safely retry after lifecycle progress.**
    A retry of the same artifact can re-run earlier gates idempotently. A different
    artifact under the same version remains blocked.

11. **Trace events lacked deterministic ordering and parent validation.**
    Added atomic per-run sequence numbers, same-run parent enforcement, token metrics,
    JSON/size validation, finite cost checks, and schema migration.

12. **Agent artifacts could contain secret files, pasted credentials, symlinks, hidden
    files, unsupported files, or oversized content.**
    Added fail-closed workspace scanning and limits.

13. **Initial database shapes could fail during an upgrade.**
    Added additive migrations and regression coverage for the initial Phase 1 table
    layouts.

## Verification

```text
python -m compileall -q legacy_ops
python -m unittest discover -s tests -v
```

Result at review completion:

- 35 tests passed;
- source compilation passed;
- standard template validation passed;
- schema migration tests passed;
- production approval and artifact-binding tests passed.

Cloud Python 3.11 and 3.13 CI remains required after synchronization to GitHub.
