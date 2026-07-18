# RISK-DISPUTE-001 — Chargeback dispute workflow

This workflow monitors Gmail for payment-dispute notices, identifies the corresponding Lightspeed sale, assembles verified evidence, drafts the MerchantOS challenge reason, and files the form only after owner approval.

## Pipeline

```text
Gmail dispute notice
        ↓
Deterministic email parser
        ↓
Lightspeed sale lookup and guarded matching
        ↓
Evidence requirements by dispute reason
        ↓
100–1,000 character challenge draft
        ↓
Owner approval queue
        ↓
MerchantOS form fill
        ↓
Final submit after explicit approval
        ↓
Submission reference and audit log
```

## Email intake

`GmailDisputeClient` searches for chargeback and dispute signals, retrieves full Gmail messages, decodes text and HTML parts, and exposes attachment metadata. OAuth access tokens are injected at runtime and are never persisted.

The parser extracts, when present:

- dispute or case ID;
- disputed amount;
- response deadline;
- transaction date;
- transaction or payment reference;
- card last four digits;
- dispute reason;
- MerchantOS dispute URL.

A missing case ID or amount stops the automated path. A missing deadline or portal URL leaves the case in evidence-incomplete status for manual review.

## Lightspeed sale match

Matching is deterministic. The workflow does not ask an LLM to select a financial transaction.

1. Exact transaction, payment, external-order, or receipt reference.
2. Fallback by amount, card last four, transaction date, location, and customer name.
3. A minimum score and uniqueness gap are required.
4. Multiple plausible sales produce `ambiguous`, not an automatic match.

The sale adapter must provide normalized `PosSale` records. The existing Lightspeed OAuth credentials must be rotated before this workflow is connected to production because a prior token appeared in repository history.

## Evidence requirements

Every challenge requires a dispute notice, itemized receipt, and payment authorization record. Additional requirements are selected by reason:

| Reason | Additional verified evidence |
|---|---|
| Fraudulent, card present | EMV, chip, tap, or terminal record |
| Fraudulent, card not present | Delivery or pickup proof |
| Product or service not received | Delivery or pickup proof |
| Duplicate charge | Transaction history distinguishing the charges |
| Credit not processed | Refund or credit receipt |
| Cancelled transaction | Cancellation policy and fulfillment record |
| Not as described | Original product description and customer communication |

The system may identify missing evidence, but it may not invent evidence or assert facts that are not in the source records.

## Challenge reason

The challenge draft is generated from the matched sale and verified evidence only. It is validated to remain between 100 and 1,000 characters, matching the MerchantOS form shown in the operating screenshot. Full card numbers are blocked; only the last four digits may be used.

## Approval boundary

The system may automatically:

- search and read relevant email;
- parse the dispute notice;
- locate and score Lightspeed sales;
- identify missing evidence;
- assemble a submission package;
- draft the challenge reason;
- fill the MerchantOS form in preview mode;
- create an approval request and audit events.

The system may not click the final submit control unless all of the following are true:

1. the chargeback package is complete;
2. the deadline has not passed;
3. the portal is `https://us.merchantos.com`;
4. the approval action is `file_chargeback_dispute`;
5. the owner approved that exact case ID;
6. live-validated submit and confirmation selectors are configured.

## Browser automation

`browser_automation/merchantos_dispute.py` uses a secret-mounted Playwright storage-state file. Login credentials, session cookies, and MFA data must never be committed.

Preview mode fills the reason, uploads evidence, and can capture a screenshot without submitting. Final mode requires explicit selectors because portal layouts can change; the automation fails closed rather than guessing which button submits the dispute.

Install browser support separately:

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
```

## Production data still required

- Gmail OAuth client and refresh-token workflow;
- current Lightspeed sales/payment feed with transaction references and card last four;
- actual dispute-email samples from the processor;
- evidence storage directory mounted into the cloud service;
- validated MerchantOS submit and confirmation selectors;
- owner notification destination;
- run schedule and deadline escalation thresholds.
