# FIN-RECON-001 Data Contract

The weekly reconciliation engine compares Uber Eats and DoorDash order-level exports with Lightspeed sales for the same Monday-through-Sunday period.

## Matching priority

1. Exact marketplace order ID against the Lightspeed external order ID.
2. Amount, timestamp, and location fallback within the configured time window.
3. Ambiguous or unmatched records become exceptions for manual review.

All arithmetic uses Python `Decimal`. An LLM never performs financial calculations or record matching.

## Expected payout formula

```text
merchandise subtotal
+ tax remitted to merchant
+ tips
+ platform-funded promotions
- merchant-funded promotions
- commission
- fees
- refunds
- chargebacks
+ signed adjustments
```

Fee, refund, chargeback, and commission exports may be positive or negative; they are normalized as deductions. Adjustments retain their sign.

## Input fields

The marketplace parser accepts normalized aliases for order ID, order date, location, customer total, subtotal, tax, tips, promotions, commission, fees, refunds, chargebacks, adjustments, and payout ID.

Lightspeed inputs require transaction ID, sale date, location, customer total, and payment type. External order ID and refunds are optional but materially improve matching quality.

Actual Uber Eats, DoorDash, and Lightspeed exports must be validated against these mappings before production scheduling. APIs or official exports are preferred over browser automation.

## Bank verification

Bank-deposit matching is policy-controlled. It remains optional until bank data is connected, then can be made mandatory without changing order reconciliation.

## Approval boundary

The engine may calculate results, persist exceptions, and generate reports automatically. It may not send a platform dispute, modify Lightspeed, issue a refund, accept an adjustment, or write off a variance without owner approval.
