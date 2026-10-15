# Stage Status: Checks and Integrations

**Status date:** 2026-10-15

This document separates checks that have passed from integrations that still need configuration or a fresh end-to-end run. Passing synthetic tests verify software behavior and safety gates; they do not establish clinical validity or production readiness.

## Current Summary

- The prior configured backend run recorded **42 passed, 7 skipped** against a temporary PostgreSQL test database. The current Stage 9 rerun reported **54 passed, 7 skipped, 1 failed, 14 errors**; the database fixture errors are due to the missing `TEST_DATABASE_URL`, and the failing MCP evaluation contract is documented in [STAGE9_AUDIT.md](STAGE9_AUDIT.md).
- Current frontend ESLint, TypeScript typecheck, and production build passed.
- Ruff passed for the changed backend modules, but the repository-wide check reports 5 pre-existing findings in untouched source/test files.
- The dashboard returned HTTP `200` from its development server.
- Current local backend checks: `/health` returned 200; `/dashboard/session` and `/dashboard/queue` failed closed with 503 because the trusted identity provider and workflow persistence are not configured.
- The latest opt-in live workflow integration attempt is **not fully passing**: 38 passed and `test_local_ollama_model_runs_inside_workflow_graph` failed with `MODEL_UNAVAILABLE`. An earlier successful live run is recorded in [EVALS.md](../EVALS.md); the later failure is the more recent result.
- The trusted clinician identity provider and approved clinical protocol are not configured in this checkout. A deterministic evaluator exists, but the current MCP contract mismatch prevents reliable end-to-end evaluation. The dashboard therefore fails closed and cannot complete a live intake-to-approval workflow here.

## Stage-by-Stage

| Stage                      | Verified                                                                                                                                                                                                                                                       | Still pending                                                                                                                                                                                                                                                 |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1: Application foundation  | FastAPI health tests passed (2 tests). The dashboard page returned HTTP `200`.                                                                                                                                                                                 | Verify the frontend health/API connection with the backend running in the target environment. The original health-only description in the root README predates the Stage 8 dashboard.                                                                         |
| 2: PostgreSQL data layer   | Repository and migration coverage passed as part of the latest 42-test backend run against PostgreSQL. The test fixture created and removed an isolated schema.                                                                                                | Re-run migrations and the suite against the intended deployment database. The temporary test container used for the latest run has been stopped.                                                                                                              |
| 3                          | No separate Stage 3 scope or status is recorded in the current project docs.                                                                                                                                                                                   | Confirm whether a Stage 3 milestone exists and record its acceptance checks rather than inferring its scope.                                                                                                                                                  |
| 4: MCP tools               | Earlier configured runs passed MCP validation, review, approval, and queue-commit tests. Current authorization/idempotency tests pass.                                                                                                                         | Current evaluation test fails: MCP input omits evaluator-required age context and returns `INTERNAL_ERROR`; align contracts before claiming MCP evaluation works.                                                                                             |
| 5: LangGraph workflow      | Workflow control-flow, validation gates, review requests, rejection of model-added findings, and approval-resume behaviors passed synthetic tests.                                                                                                             | Resolve the latest live Ollama failure (`MODEL_UNAVAILABLE`) and rerun the complete opt-in integration command below. The historical 39-pass run in `EVALS.md` does not supersede the later failure.                                                          |
| 6: Filesystem MCP          | The pinned official Filesystem MCP server passed dedicated real-server retrieval and boundary checks previously; the latest comprehensive attempt reported the Filesystem checks passing.                                                                      | Re-run the opt-in container tests as a current standalone gate. Build the pinned image first. Production protocol registry remains empty. Package license applicability remains unresolved; see [docs/README.md](README.md).                                  |
| 7: Reassessment            | An earlier focused run recorded 5 passed: duplicate, untriggered, stale, invalid, and clinician-decision recording.                                                                                                                                            | Current rerun was blocked at fixture setup by missing `TEST_DATABASE_URL`. Reassessment service behavior is not exposed as a dashboard-facing HTTP submission flow.                                                                                           |
| 8: Clinician dashboard     | Current auth/validation tests passed; frontend lint, typecheck, and build passed. A synthetic in-memory route check confirmed rejection/override audit records.                                                                                                | The PostgreSQL-backed intake/detail test could not be rerun without `TEST_DATABASE_URL`; browser intake-to-review remains unverified. Reads are unauthenticated (see Stage 9 audit). Freshness remains unknown because no backend freshness rule is returned. |
| 9: Repository audit        | See [STAGE9_AUDIT.md](STAGE9_AUDIT.md): 19 synthetic scenarios recorded, including expected and actual behavior, evidence, approval state, and timing where measured.                                                                                          | Resolve MCP/evaluator schema mismatch, configure a dedicated PostgreSQL test database, secure dashboard reads, reconcile the unused workflow engine's ORM contract, define duplicate review-request semantics, and review configurable network destinations.  |
| 10: Competition submission | [README.md](../README.md), [ARCHITECTURE.md](../ARCHITECTURE.md), [EVALS.md](../EVALS.md), MIT [LICENSE](../LICENSE), and offline trace script are present. LangGraph package metadata reports MIT. Custom MCP stdio discovery listed all four expected tools. | Not ready: no clean-checkout run, local model completion, Filesystem MCP runtime, real demo video, production identity/protocol setup, or green full suite. Filesystem MCP redistribution terms remain unresolved.                                            |

## Previously Recorded Successful Run

The following checks were recorded before the current Stage 9 rerun:

```text
TEST_DATABASE_URL=... pytest -q
42 passed, 7 skipped

backend Ruff checks and Python compileall
passed

frontend: npm run lint
passed

frontend: npm run typecheck
passed

frontend: npm run build
passed

GET http://127.0.0.1:3000/
HTTP 200
```

That backend suite used PostgreSQL in a temporary Docker container. The container was stopped after the run. The 7 skips include opt-in integration coverage; a passing standard suite is not a replacement for the live Ollama/MCP run.

Stage 7's earlier focused reassessment suite reported **5 passed**. Stage 6's real Filesystem MCP tests also passed in earlier opt-in runs. The detailed historical scenario record and commands are in [EVALS.md](../EVALS.md).

## Current Stage 9 Checks

The current repository-wide and focused results, including the evaluator
failure, database fixture errors, frontend checks, and source-level security
findings, are recorded in [STAGE9_AUDIT.md](STAGE9_AUDIT.md). Do not interpret
the historical successful run above as superseding those results.

## Pending Integration Gates

### 1. Trusted clinician identity

Configure `TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER` as a backend-trusted provider that derives clinician subject, synthetic reference, and permissions from authenticated request state. Do not accept identity or permissions from dashboard form fields or arbitrary headers. Until this is configured, `/dashboard/session` fails closed and intake/review controls remain unavailable.

Verify separately that workflow start requires `triage:workflow:run`, review requires `triage:assessment:approve`, and approval/queue commit requires both the appropriate approval and commit permissions. Exercise unauthenticated, insufficient-permission, wrong-clinician, stale-assessment, and idempotent retry cases against the real provider.

### 2. Authorized protocol and evaluator

The production-approved registry at `docs/clinical-protocols/approved/registry.json` is empty. A deterministic IITT subset evaluator exists, but the current MCP evaluation input/output contract is incompatible with it: required age context is missing and the MCP output schema cannot represent all evaluator statuses. Obtain authorization and reuse terms for a protocol, register its verified version and digest, align the typed contracts without inferring missing clinical facts, and add an end-to-end MCP evaluation test before enabling live evaluation.

### 3. Ollama and end-to-end workflow

The most recent live integration attempt failed because the configured Ollama model was unavailable to the workflow. Check model availability and API reachability, then rerun the complete command with the required PostgreSQL, test fixtures, MCP process, Filesystem MCP image, and opt-in flags:

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/triage_sentinel_test \
TRIAGE_RUN_OLLAMA_INTEGRATION=1 \
TRIAGE_RUN_REAL_MCP_INTEGRATION=1 \
TRIAGE_RUN_FILESYSTEM_MCP_INTEGRATION=1 \
python -m pytest -q
```

Record the new result in `EVALS.md`; keep the older 39-pass result as historical rather than presenting it as the latest state.

### 4. Filesystem MCP runtime

Build the pinned image and rerun actual-server discovery, approved document retrieval, checksum validation, outside-root read denial, and read-only mount write denial:

```bash
docker build -t triage-filesystem-mcp:1.0.0 backend/filesystem-mcp
TRIAGE_RUN_FILESYSTEM_MCP_INTEGRATION=1 python -m pytest -q tests/test_reference_mcp.py
```

The production registry must remain empty until protocol approval and provenance are established. Resolve the third-party package license question before redistribution where required.

### 5. Dashboard end-to-end and reassessment

With a trusted test identity and an approved non-clinical test protocol, run a browser-level workflow covering synthetic intake, backend validation/evaluation, queue visibility, evidence and rule-reference review, protected approval/commit, override with reason, and unauthorized/failed actions. Confirm no priority changes without the protected backend action.

Add an API-backed reassessment submission path before claiming that Stage 7 is integrated into the dashboard. The current dashboard can display pending review requests returned by the backend, but the focused Stage 7 tests exercise the service method rather than a clinician-facing reassessment flow. Define freshness criteria in the selected backend protocol before displaying observations as current or outdated; until then the UI must keep freshness explicitly unknown.

## Configuration State and Scope

- No production trusted clinician identity provider is configured.
- No production deterministic clinical evaluator is configured.
- No approved clinical protocol is registered.
- The Ollama live workflow has a later recorded `MODEL_UNAVAILABLE` failure despite an earlier successful integration run.
- The temporary PostgreSQL test container has been stopped; start/configure a dedicated test database to rerun DB-backed tests.
- The frontend dev server was started at `http://localhost:3000`; this proves the page serves, not that protected backend workflow calls succeed.
- The root README and general docs have not yet been fully reconciled with the Stage 7/8 implementation. This status document is the current checklist; update those guides after the remaining integration boundaries are settled.
- Synthetic tests and test-only identity/evaluator fixtures do not imply clinical validity, production authentication, or medical-device readiness.
