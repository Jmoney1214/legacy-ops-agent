# Legacy Agent Build Control

This directory is the durable program record for the phased Legacy Wine & Liquor multi-agent business operating system.

## Authority boundaries

- **Supabase** is the planned runtime command center for tasks, evidence, queues, approvals, memory, audit, and outcomes.
- **GitHub** governs code, migrations, agent definitions, tests, evaluations, reviews, and releases.
- **OpenAI Agents SDK** is the primary model-agent runtime.
- **Render** runs the web, worker, and maintenance services.
- **Microsoft Graph** will provide mailbox access after mailbox topology and scoped permissions are verified.
- **Lightspeed and existing deterministic services** remain authoritative for sales, inventory, reconciliation, chargeback matching, money, quantities, and record mutation.

## Operating rules

1. Work only the current phase.
2. One phase branch and one pull request at a time by default.
3. Do not push a knowingly broken checkpoint.
4. Debug every required failure before pushing another commit.
5. Review the entire diff before every push.
6. Never merge around red or skipped required checks.
7. Require independent review, staging evidence, rollback, and explicit merge authorization.
8. Use squash merge only.
9. A phase is complete only after post-merge `main` and deployed verification.

## P00

Phase P00 establishes the control plane for the build itself. It changes no production data, runtime permission, mailbox access, or agent behavior.

Live tracking:

- Issue: https://github.com/Jmoney1214/legacy-ops-agent/issues/6
- Branch: `phase/00-program-control`
- Base commit: `f096b1d533897b6c4d3b4e5c7e37d947b3a5cd40`
