# P00 Repository Baseline

## Evidence timestamp

2026-08-16T01:27:48-04:00

## Repository

- Name: `Jmoney1214/legacy-ops-agent`
- Visibility: public
- Default branch: `main`
- Baseline commit: `f096b1d533897b6c4d3b4e5c7e37d947b3a5cd40`
- Phase branch: `phase/00-program-control`
- Phase issue: https://github.com/Jmoney1214/legacy-ops-agent/issues/6

## Confirmed existing capabilities

The current README and commit history confirm these delivered areas:

- approval and audit control plane;
- deterministic Uber Eats and DoorDash reconciliation against Lightspeed;
- chargeback intake, deterministic sale matching, evidence planning, approval-gated MerchantOS preview/submission controls;
- governed agent build pipeline with manifests, tool permissions, lifecycle, artifact binding, tracing, eval and release gates.

## Confirmed runtime and deployment baseline

- Runtime entrypoint: `http_server:app`.
- Web framework: Starlette served by Uvicorn.
- Agent runtime dependency: `openai-agents`.
- Current HTTP endpoints: `/health` and `/run`.
- Current app uses one module-level `Agent` and an optional read-only Lightspeed item tool.
- Docker base image: `python:3.13-slim`.
- Docker command: Uvicorn on `${PORT:-10000}`.
- Current requirements include `openai-agents`, `httpx`, `starlette`, `uvicorn`, `python-dotenv`, and PyYAML.

## Confirmed CI baseline

`.github/workflows/ci.yml` runs on pull requests and pushes to `main` with:

- Python 3.11;
- Python 3.13;
- dependency installation from `requirements.txt`;
- `python -m unittest discover -s tests -v`;
- upload of unit-test output;
- `python -m compileall -q legacy_ops browser_automation`.

## P00 technical observations

- The current HTTP runtime remains a prototype and is not yet the Supabase-backed command center.
- Existing deterministic business services must remain authoritative when later exposed as agent tools.
- SQLite remains in existing operational services and must not be treated as the final horizontally scalable runtime store.
- Supabase command-center migrations, web/worker split, Microsoft Graph, dashboard, durable production sessions, and functional head agents are not yet implemented.
- P00 changes are governance and documentation only; no runtime behavior or production permission is changed.

## Open baseline checks

The following must be verified by the draft PR CI and review cycle:

- complete unit-test result on Python 3.11;
- complete unit-test result on Python 3.13;
- source compilation;
- full changed-file and diff review;
- Docker build status;
- independent review;
- post-merge `main` verification.
