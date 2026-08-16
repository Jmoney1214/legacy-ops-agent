# Master Codex Build Prompt

You are working in the existing repository:

`Jmoney1214/legacy-ops-agent`

Your job is to execute exactly one current phase of the Legacy Wine & Liquor multi-agent business operating system.

## Read first

1. Read every applicable `AGENTS.md`.
2. Read:
   - `docs/build/MASTER_BUILD_SCHEDULE.md`
   - `docs/build/CODEX_EXECUTION_PROTOCOL.md`
   - `docs/build/GIT_RELEASE_GOVERNANCE.md`
   - `docs/build/PHASE_STATUS.yaml`
   - the current `docs/build/phases/phase-NN.md`
3. Inspect the actual repository, tests, CI, migrations, deployment files, and current branch.
4. Do not rely on an old summary when the repository can be inspected.

## Phase selection

Work only the phase marked `current_phase` in `PHASE_STATUS.yaml`.

Do not:
- start another phase;
- add a future integration;
- enable a side effect outside the current phase;
- rewrite existing deterministic business logic with prompts;
- introduce a second agent/orchestration framework;
- make assumptions about unknown credentials, permissions, mailbox types, legal rules, or business thresholds.

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
-> CODE REVIEW
-> FIX + REGRESSION TEST + FULL GATE
-> STAGING
-> MERGE AUTHORIZATION
-> SQUASH MERGE
-> MAIN CI
-> POST-MERGE STAGING
-> COMPLETION REPORT
```

## Pre-push hard rule

Do not push until all required local checks for the changed areas pass and the complete diff has been reviewed.

A branch checkpoint may be incomplete. It may not be knowingly broken.

## Review hard rule

Always request automated Codex review when configured. HIGH and CRITICAL phases require a security-focused review and the human review count listed in the phase file.

Do not merge around:
- failing/pending/skipped required CI;
- unresolved review threads;
- unknown migration impact;
- an untested rollback;
- a security finding;
- missing staging evidence;
- missing merge authorization.

## Merge hard rule

Use squash merge only.

You may merge only when the phase issue contains:

```yaml
merge_authorized: true
```

and every required gate is green.

HIGH production releases and every CRITICAL phase also require:

```yaml
owner_release_approved: true
```

Otherwise report `READY TO MERGE` and stop.

## Stop and ask the owner

Stop before any:
- destructive or irreversible command;
- paid action;
- new permission;
- production credential use outside approved secret storage;
- external send or mutation not explicitly enabled;
- unresolved legal/compliance assumption;
- ambiguous business record;
- unsupported mailbox/API/account configuration;
- merge missing required approval.

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

Never report `COMPLETE` until the PR is merged and post-merge main/staging verification is green.
