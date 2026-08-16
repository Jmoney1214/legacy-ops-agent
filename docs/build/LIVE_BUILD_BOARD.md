# Legacy Agentic Business OS — Live Build Board

## Current phase

| Field | Value |
|---|---|
| Phase | P00 — Program control and repository baseline |
| Status | `PR_DRAFT` |
| Repository | `Jmoney1214/legacy-ops-agent` |
| Base commit | `f096b1d533897b6c4d3b4e5c7e37d947b3a5cd40` |
| Branch | `phase/00-program-control` |
| Issue | https://github.com/Jmoney1214/legacy-ops-agent/issues/6 |
| Pull request | https://github.com/Jmoney1214/legacy-ops-agent/pull/7 |
| Merge authorized | `false` |
| Target P00 finish | 2026-08-19 |
| Target limited launch | 2026-12-18 |

## P00 progress

### Completed

- [x] GitHub repository and current `main` inspected.
- [x] Live P00 issue, branch, and draft PR created.
- [x] Existing runtime, Dockerfile, dependencies, and CI baseline recorded.
- [x] Pull-request and phase-issue templates installed.
- [x] Codex and Claude Code repository instructions installed.
- [x] Master schedule and P00–P15 phase catalog installed.
- [x] Live phase-status file installed.
- [x] Codex execution, review, and merge protocol installed.
- [x] Root agent rules corrected for a full functional-head architecture and separate three-person strategic council.
- [x] Automated build-control validator added.
- [x] CI expanded to validate the control pack and build the Docker image.

### Open gates

- [ ] Latest Python 3.11 and 3.13 CI pass.
- [ ] Build-control validation job passes.
- [ ] Docker-build job passes.
- [ ] Complete final PR diff review.
- [ ] Automated review requested when configured.
- [ ] One independent human approval.
- [ ] Every review thread resolved.
- [ ] Merge authorization recorded.
- [ ] Squash merge.
- [ ] Post-merge `main` CI and deployed verification.
- [ ] P00 completion report and issue closeout.

## Phase portfolio

| Phase | Status | Target window |
|---|---|---|
| P00 Program control and repository baseline | PR_DRAFT | Aug 17–19 |
| P01 Supabase security and command center | PLANNED | Aug 20–Sep 2 |
| P02 Runtime split | PLANNED | Sep 3–11 |
| P03 Tools and evidence | PLANNED | Sep 14–23 |
| P04 Dashboard and approvals | PLANNED | Sep 24–30 |
| P05 Marketplace reconciliation | PLANNED | Oct 1–7 |
| P06 Chargebacks | PLANNED | Oct 8–15 |
| P07 Inventory, PO, and receiving | PLANNED | Oct 16–27 |
| P08 Outlook read-only intake | PLANNED | Oct 28–Nov 6 |
| P09 Core head agents | PLANNED | Nov 9–18 |
| P10 Commercial head agents | PLANNED | Nov 19–25 |
| P11 Fulfillment, events, people, and compliance | PLANNED | Nov 26–Dec 1 |
| P12 Controlled external actions | PLANNED | Dec 2–8 |
| P13 Strategic advisory council | PLANNED | Dec 9–11 |
| P14 Pilot and hardening | PLANNED | Dec 14–17 |
| P15 Production handoff | PLANNED | Dec 18 |

## Known clarification gates

1. Before P01 production work: confirm the staging Supabase project, backup/restore path, and current production schema inventory.
2. Before P08 permission assignment: verify whether `support@legacywineandliquor.com` is a shared mailbox, licensed user mailbox, or alias, and confirm available Microsoft administrator authority.
3. Before P13: supply the exact three strategic-advisor names in text and approve the public source registry.
4. Before P12/P15: record exact owner-approved action allowlists, thresholds, production permissions, and rollback authority.

## Update rule

The machine-readable authority is `docs/build/PHASE_STATUS.yaml`. This board is updated after every meaningful phase transition, CI result, review outcome, merge, or post-merge verification. A phase is complete only after post-merge verification is green.
