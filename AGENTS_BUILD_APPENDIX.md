# AGENTS.md Build-Control Appendix

These rules are also enforced in the repository-root `AGENTS.md`. This file is retained as a focused review checklist.

## Phase control

- Work only the phase marked current in `docs/build/PHASE_STATUS.yaml`.
- Read that phase's contract in `docs/build/PHASE_CATALOG.yaml`.
- Follow `docs/build/CODEX_EXECUTION_PROTOCOL.md`.
- Keep one active implementation phase at a time unless a separately documented parallel exception is approved.
- Do not begin the next phase until the current phase is post-merge verified.
- Do not expand scope; create a linked future issue.

## Pre-push gate

Before any push:

- run targeted and full required tests;
- debug every new failure;
- run formatting, lint, typing, compilation, migration/RLS, integration, security, secret, eval, Docker, and smoke checks applicable to the change;
- inspect the full diff line by line;
- confirm no secret, PII, production data, local database, token, `.env`, attachment, or debug artifact;
- confirm a coherent commit and clean worktree.

Never push a knowingly broken checkpoint and never use `--no-verify` to bypass a gate.

## Pull request and review

- Never push directly to `main`.
- Use the exact phase branch.
- Open one PR per phase and keep phase fixes in that PR.
- Request automated Codex review when configured.
- STANDARD requires one independent human approval.
- HIGH and CRITICAL require two independent human reviews and a security-focused review.
- The PR author cannot count as the independent reviewer.
- Resolve every review conversation.
- Rerun the applicable full gate after review fixes.

## Merge

- Squash merge only.
- Merge only after all applicable CI, review, staging, rollback, and authorization gates are green.
- A gate may be `not_applicable` only when `PHASE_STATUS.yaml` records a factual reason; `not_applicable` is never a bypass for an applicable check.
- A target date is not merge authorization.
- HIGH production releases and every CRITICAL phase require explicit owner release approval.
- After merge, verify `main` CI and every applicable deployed environment; record a factual `not_applicable` reason for a non-runtime phase.

## Reporting

Every completion report must include phase, branch, commits, PR, changed files, migrations, tests/evals, pre-push review, CI, code review, applicable staging evidence or `not_applicable` rationale, security, blockers, rollback, deployed SHA when applicable, and next action.

Never claim `COMPLETE` without all applicable post-merge verification.
