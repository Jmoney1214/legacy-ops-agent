# Legacy Ops Agent

Cloud-ready, approval-gated operations platform for Legacy Wine & Liquor.

## Delivered workflows

### Phase 1 — control plane

- durable approval requests and human decisions;
- audit events for high-impact actions;
- credential and token redaction before persistence;
- atomic approval transitions and SQLite WAL durability;
- Python 3.11 and 3.13 cloud CI.

### Phase 2 — `FIN-RECON-001`

Weekly Uber Eats and DoorDash payout reconciliation against Lightspeed, with deterministic order matching, settlement variance analysis, optional bank-deposit verification, exception persistence, and executive reporting.

### Phase 3 — `RISK-DISPUTE-001`

Chargeback-dispute workflow that:

1. searches Gmail for dispute notices;
2. parses the case ID, amount, deadline, reason, transaction references, and MerchantOS link;
3. retrieves read-only Lightspeed R-Series Sale records and matches the disputed transaction without LLM-based financial guessing;
4. identifies required evidence by dispute reason;
5. generates a verified 100–1,000 character challenge statement;
6. creates an owner approval request;
7. fills the MerchantOS form in preview mode;
8. permits final submission only after approval and live portal-selector validation;
9. records the confirmation reference and audit trail.

See `docs/chargeback-dispute-workflow.md`.

## Test

```bash
python -m unittest discover -s tests -v
python -m compileall -q legacy_ops browser_automation
```

## Optional browser automation

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
```

Browser storage state, dispute evidence, screenshots, OAuth tokens, API keys, and `.env` files must remain outside Git.

## Security

Never commit access tokens, OAuth codes, passwords, session cookies, API keys, card numbers, or `.env` files. Any credential that has ever appeared in Git history must be treated as compromised and rotated before production use.

## Agent platform

The repository now includes a governed build pipeline for future business agents.
This is separate from the deterministic operational services already implemented.

Core platform components:

- strict versioned agent manifests;
- standard agent scaffolding and workspace validation;
- immutable artifact digests;
- typed tool schemas and least-privilege permissions;
- exact approval-action binding for side-effecting tools;
- bounded loop, runtime, failure, tool-call, and cost controls;
- durable agent lifecycle registry;
- redacted, ordered trace events;
- ordered contract, tool, guardrail, test, eval, review, staging, and release gates;
- owner-approved production promotion bound to the exact artifact.

Create a scaffold:

```bash
python -m legacy_ops.agent_cli scaffold inventory_agent \
  --display-name "Inventory Agent" \
  --purpose "Review inventory and surface replenishment exceptions." \
  --owner inventory_operations
```

Validate an agent and calculate its artifact digest:

```bash
python -m legacy_ops.agent_cli validate agents/inventory_agent
```

See `docs/agent-build-pipeline.md` for the complete contract. This platform does not
yet mean that LLM-powered business agents are live; it governs how those agents will
be created and released in subsequent phases.
