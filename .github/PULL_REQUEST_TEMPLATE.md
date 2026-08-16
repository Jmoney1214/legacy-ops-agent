## Phase control

- Phase:
- Phase issue:
- Status entry:
- Branch:
- Base commit:
- Head commit:
- Risk: `STANDARD` / `HIGH` / `CRITICAL`
- Required independent reviews:
- Merge authorized: `false`

## Objective

What single phase outcome does this PR deliver?

## Scope delivered

-

## Explicit non-goals

-

## Changed areas

- [ ] Application code
- [ ] Supabase migration/RLS
- [ ] Authentication/permissions
- [ ] Tool contract
- [ ] Agent manifest/instructions/schema/routing
- [ ] Dashboard
- [ ] Integration
- [ ] CI/deployment
- [ ] Documentation only

## Data and migration safety

- Migrations:
- Backup/restore evidence:
- RLS/grant impact:
- Data-count reconciliation:
- Rollback:

## Pre-push gate

- [ ] Targeted tests pass.
- [ ] Full required local verification passes, or the P00-only bootstrap exception is documented in `PHASE_STATUS.yaml` and prohibited from future phases.
- [ ] Debugging is complete.
- [ ] Complete diff reviewed line by line.
- [ ] `git diff --check` clean.
- [ ] Secret/credential/PII scan clean.
- [ ] No unrelated files or debug artifacts.
- [ ] Worktree was clean after commit.
- [ ] No known required check was red when pushed.

## Verification

Use `PASS`, `FAIL`, `PENDING`, `BLOCKED`, or `N/A — <reason>`.

| Gate | Result | Evidence |
|---|---|---|
| Format/lint | | |
| Static type | | |
| Python 3.11 | | |
| Python 3.13 | | |
| Compile | | |
| Supabase reset/migration/RLS | | |
| Integration/contract | | |
| Security/dependency/secret | | |
| Agent evals | | |
| Docker | | |
| Local smoke | | |
| Staging smoke | | |

## Review

- [ ] Automated Codex review requested when configured.
- [ ] Security-focused review requested for HIGH/CRITICAL.
- [ ] Required independent human approvals received.
- [ ] The PR author is not counted as the independent reviewer.
- [ ] All conversations resolved.
- [ ] Full required verification rerun after review fixes.

## Side effects and permissions

- [ ] No new side effect.
- [ ] No permission expansion.
- [ ] New side effect/permission is explicitly in this phase and approval-gated.
- Feature flags:
- Emergency disable:
- Idempotency:
- External confirmation:

## Staging

- Deployment, or `N/A` with rationale:
- Smoke evidence, or `N/A` with rationale:
- Monitoring:
- Rollback point:

## Open limitations / follow-up issues

-

## Merge checklist

- [ ] PR is no longer draft.
- [ ] Branch is current and mergeable.
- [ ] All applicable required checks are green; every `N/A` has a recorded rationale.
- [ ] Required approvals are present.
- [ ] All threads are resolved.
- [ ] Staging verified, or explicitly `N/A` with rationale for a non-runtime phase.
- [ ] Rollback verified.
- [ ] Merge authorization recorded.
- [ ] Owner release approval recorded when required.
- [ ] Squash merge selected.
