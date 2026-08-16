# Legacy Agent Build Control

This directory is the durable program record for the phased Legacy Wine & Liquor multi-agent business operating system.

## Canonical files

- `MASTER_BUILD_SCHEDULE.md` — live reforecast, milestones, risk classes, and phase timing.
- `PHASE_CATALOG.yaml` — machine-readable P00–P15 contracts, dependencies, deliverables, non-goals, tests, and exit criteria.
- `PHASE_STATUS.yaml` — current phase, gates, blockers, exceptions, and merge authorization.
- `CODEX_EXECUTION_PROTOCOL.md` — mandatory implementation, debugging, pre-push, review, merge, and post-merge process.
- `LIVE_BUILD_BOARD.md` — human-readable current progress and open gates.
- `reports/` — factual baseline, start, review, completion, and verification reports.

Repository-wide instructions remain in `AGENTS.md`, `CLAUDE.md`, and `CODEX_MASTER_PROMPT.md`.

## Authority boundaries

- **Supabase** is the planned runtime command center for tasks, evidence, queues, approvals, memory, audit, and outcomes.
- **GitHub** governs code, migrations, agent definitions, tests, evaluations, reviews, and releases.
- **OpenAI Agents SDK** is the primary model-agent runtime.
- **Render** runs the web, worker, and maintenance services.
- **Microsoft Graph** provides mailbox access only after mailbox topology and scoped permissions are verified.
- **Lightspeed and existing deterministic services** remain authoritative for sales, inventory, reconciliation, chargeback matching, money, quantities, and record mutation.

## Operating rules

1. Work only the current phase.
2. One phase branch and one pull request at a time by default.
3. Do not push a knowingly broken checkpoint.
4. Debug every applicable failure before another push.
5. Review the entire diff before every push.
6. Never merge around failed, pending, skipped, neutral, or unresolved applicable checks.
7. A gate may be `not_applicable` only when `PHASE_STATUS.yaml` records a factual reason; it is not a bypass.
8. Require independent review, applicable staging evidence or a recorded `not_applicable` rationale, rollback, and explicit merge authorization.
9. Use squash merge only.
10. A phase is complete only after `main` CI and every applicable deployed verification pass.

## P00

P00 establishes the control plane for the build itself. It changes no production data, runtime permission, mailbox access, or agent behavior. Its staging and deployed-runtime gates are therefore recorded as `not_applicable` with factual reasons; Python 3.11/3.13 tests, build-control validation, Docker build, full diff review, independent review, and post-merge `main` CI remain mandatory.

Live tracking:

- Issue: https://github.com/Jmoney1214/legacy-ops-agent/issues/6
- Pull request: https://github.com/Jmoney1214/legacy-ops-agent/pull/7
- Branch: `phase/00-program-control`
- Base commit: `f096b1d533897b6c4d3b4e5c7e37d947b3a5cd40`
- Merge authorized: `false`
