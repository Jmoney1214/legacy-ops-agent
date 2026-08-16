# Master Codex Build Prompt

You are working in the existing repository:

`Jmoney1214/legacy-ops-agent`

Execute exactly one current phase of the Legacy Wine & Liquor multi-agent business operating system.

## Read first

1. Read every applicable `AGENTS.md`.
2. Read:
   - `docs/build/PHASE_STATUS.yaml`;
   - `docs/build/PHASE_CATALOG.yaml`;
   - `docs/build/CODEX_EXECUTION_PROTOCOL.md`;
   - `docs/build/MASTER_BUILD_SCHEDULE.md`.
3. Locate the current phase from `program.current_phase` in `PHASE_STATUS.yaml` and read only that phase's contract in `PHASE_CATALOG.yaml`.
4. Inspect the actual repository, tests, CI, migrations, deployment files, current branch, open phase issue, and pull request.
5. Do not rely on an old summary when the repository can be inspected.

## Phase selection

Work only the current phase. Do not:

- start another phase;
- add a future integration;
- enable a side effect outside the current phase;
- rewrite deterministic business logic with prompts;
- introduce a second agent/orchestration framework;
- guess unknown credentials, permissions, mailbox types, legal rules, or business thresholds;
- modify production data or permissions without the recorded gate.

## Required sequence

```text
BASELINE
-> PHASE CONTRACT
-> IMPLEMENT SMALL UNIT
-> TARGETED TEST
-> DEBUG
-> NEXT UNIT
-> FULL LOCAL VERIFICATION
-> PRE-PUSH DIFF/SECURITY REVIEW
-> COMMIT
-> CLEAN WORKTREE
-> PUSH PHASE BRANCH
-> DRAFT PR
-> CI
-> COMPLETE PR DIFF REVIEW
-> INDEPENDENT REVIEW
-> FIX + REGRESSION TEST + FULL GATE
-> RESOLVE THREADS
-> STAGING
-> MERGE AUTHORIZATION
-> SQUASH MERGE
-> MAIN CI
-> POST-MERGE DEPLOYED VERIFICATION
-> COMPLETION REPORT
```

## Pre-push hard rule

Do not push until every applicable local gate in `CODEX_EXECUTION_PROTOCOL.md` passes and the complete diff has been reviewed. A branch checkpoint may be incomplete; it may not be knowingly broken. Never use `--no-verify` to bypass a gate.

## Review hard rule

- Request automated Codex review when configured.
- STANDARD requires one independent human approval.
- HIGH and CRITICAL require two independent human approvals.
- HIGH and CRITICAL also require a security-focused review.
- The PR author cannot count as the independent reviewer.
- Resolve every review thread and rerun the applicable full gate after fixes.

Do not merge around failed, pending, skipped, or neutral required CI; unresolved review threads; unknown migration impact; untested rollback; security findings; missing staging evidence; or missing authorization.

## Merge hard rule

Use squash merge only. You may merge only when both the program/current-phase status records:

```yaml
merge_authorized: true
```

and every required gate is green. HIGH production releases and every CRITICAL phase also require explicit owner release approval. Otherwise report `READY_TO_MERGE` or `BLOCKED` and stop.

## Stop and ask the owner

Stop before any destructive or irreversible command, paid action, new permission, production credential use outside approved secret storage, external send or mutation not enabled in the current phase, unresolved legal/compliance assumption, ambiguous business record, unsupported mailbox/API/account configuration, missing reviewer, or merge missing approval.

## Completion report

Return:

```text
PHASE
STATUS
BRANCH
BASE COMMIT
HEAD COMMIT
PULL REQUEST
SCOPE COMPLETED
FILES CHANGED
MIGRATIONS
TESTS AND EVALS
LOCAL PRE-PUSH REVIEW
CI STATUS
CODE REVIEW STATUS
STAGING STATUS
SECURITY STATUS
OPEN BLOCKERS
ROLLBACK
NEXT ACTION
```

Never report `COMPLETE` until the PR is merged and post-merge `main` and deployed verification are green.
