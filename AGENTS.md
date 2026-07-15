# Legacy Ops Agent Rules

## Operating principle

Agents analyze, prepare, classify, reconcile, and draft. They do not independently commit cash, change prices, alter financial records, send legal correspondence, issue refunds, place purchase orders, or send external disputes.

## Approval tiers

| Tier | Examples | Execution |
|---|---|---|
| Read-only | reports, summaries, anomaly detection | automatic |
| Internal write | create task, create exception, save draft | automatic and audited |
| External communication | vendor email, platform dispute, customer response | owner approval |
| Financial mutation | PO submission, refund, write-off, payment promise | owner approval |
| Legal/compliance | law firm, FedEx account dispute, shipping policy change | owner/legal approval |

## Required run behavior

1. Validate inputs.
2. Retrieve only the minimum context needed.
3. Use deterministic code for arithmetic and record matching.
4. Redact credentials before logging.
5. Create an approval request before high-impact execution.
6. Write a structured audit event for every decision and tool call.
7. Fail closed when credentials, data, or policy are missing.
