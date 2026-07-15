# Legacy Ops Agent

Cloud-ready, approval-gated operations platform for Legacy Wine & Liquor.

## Current scope

Phase 1 provides the control plane required before business agents are allowed to act:

- durable approval requests and decisions;
- audit events for every high-impact action;
- credential and token redaction before persistence;
- environment-based configuration;
- cloud CI on Python 3.11 and 3.13.

The first business workflow being built is `FIN-RECON-001`: a weekly Uber Eats and DoorDash settlement audit against Lightspeed, followed by bank-deposit matching when bank data is connected.

## Test

```bash
python -m unittest discover -s tests -v
```

## Security

Never commit access tokens, OAuth codes, passwords, API keys, or `.env` files. Any credential that has ever appeared in Git history must be treated as compromised and rotated.
