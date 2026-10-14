# Stage 5 LangGraph workflow

## Execution path

The FastAPI backend creates a single LangGraph workflow graph when
`DATABASE_URL` is configured. The graph calls the repository's registered
`triage-tools-mcp` tools through an in-process MCP SDK memory transport; it
does not open an unrestricted database connection for the model and does not
start a separate stdio MCP process.

`POST /workflows` accepts a typed `WorkflowStartInput` containing synthetic
encounter and assessment identifiers, age in months, presenting complaints,
observations, evaluation time, and an optional synthetic queue name. It:

1. Persists the workflow ID, assessment number, configured model ID, protocol
   ID/version, input SHA-256 digest, and a `received` event. The intake body is
   not persisted as a workflow request.
2. Calls MCP `validate_vitals`, including a required set of numeric
   observations. Invalid values fail the run. Missing measurements are
   preserved as incomplete and are not converted to normal.
3. Calls MCP `evaluate_triage_rules` with the source-protocol evaluator input.
   The evaluator's returned status, findings, rule references, evidence,
   protocol version, missing information, and uncertainty are authoritative.
4. Sends the structured evaluator result to local Ollama in JSON-schema mode.
   The only model response accepted is an exact acknowledgement of the
   evaluator status and matched rule IDs. A malformed, inconsistent, or timed
   out response fails the workflow; the model cannot add rules, evidence,
   categories, or clinical text.
5. Builds the recommendation from evaluator fields and fixed explanatory text,
   then calls MCP `request_clinician_review`. The output remains
   `awaiting_clinician_approval`; no queue event is created.

`GET /workflows/{workflow_id}` returns the persisted state, tool results,
recommendation, action IDs, and immutable workflow event history.

`POST /workflows/{workflow_id}/approval` is the explicit clinician approval
and commit action. It accepts no identity in the request body. A trusted
authentication middleware must populate
`request.state.authenticated_clinician`; absent or malformed trusted identity
is rejected. The caller must have both `triage:assessment:approve` and
`triage:queue:commit`. The workflow issues/reuses an immutable approval bound
to the exact assessment, then calls MCP `commit_approved_queue_update`.
The MCP service independently checks caller permissions, exact assessment
version and number, latest assessment, clinician identity, revocation, and
idempotency before writing the queue event. A retry uses a stable
workflow-derived idempotency key. A process restart can resume from persisted
`awaiting_clinician_approval` or `committing` state; a completed workflow
returns its stored result.

The API currently has no authentication provider or middleware. Therefore the
approval endpoint intentionally returns 401 in the unconfigured application.
Do not populate the trusted request state from model output, query parameters,
or JSON request fields. Connecting the deployment's authenticated identity
provider remains necessary before interactive approval can be used.

## Configuration

Install the optional workflow and MCP dependencies and migrate the database:

```bash
python -m pip install -e "backend[workflow,mcp,database]"
cd backend
python -m alembic upgrade head
```

Set these values in the backend environment:

```dotenv
DATABASE_URL=postgresql+psycopg://...
TRIAGE_OLLAMA_BASE_URL=http://127.0.0.1:11434
TRIAGE_OLLAMA_MODEL_ID=qwen2.5:1.5b
TRIAGE_OLLAMA_TIMEOUT_SECONDS=90
```

The model adapter accepts only loopback inference URLs. Install/start Ollama
separately and retrieve the configured model before starting a workflow. The
default candidate is
[Qwen2.5 1.5B Instruct](https://ollama.com/library/qwen2.5:1.5b), a small
open-weight option with CPU inference available. Confirm the model's current
license and Ollama's model metadata before redistribution or deployment.

## Hardware/runtime check and limitations

The checked development machine is a Dell Latitude E7240 with 11.9 GiB RAM, an
Intel Core i5-4310U (2 cores/4 threads), and integrated Intel graphics. No
Ollama executable/service was present and a connection to
`127.0.0.1:11434` timed out. The small Qwen2.5 1.5B candidate was selected as
a CPU-only option based on that hardware; no model was downloaded and no real
Ollama inference or complete local-model-backed workflow has been executed.
The model adapter's request, JSON-schema validation, malformed-response, and
timeout behavior is covered using synthetic/mock tests only. Consequently,
the local inference acceptance criterion remains unverified.

The evaluator is still the project's documented WHO/ICRC/MSF routine IITT
subset and does not diagnose, prescribe, assign yellow or green, or establish
that a case is safe when no implemented escalation rule fires. This
prototype is synthetic-data-only and is not clinically validated. A trusted
clinician authentication integration and a real local-model smoke test are
also outstanding before the complete deployed workflow can be accepted.

## Verification

Expected outcomes for the synthetic workflow tests are declared in
`tests/test_workflow.py` before test execution. The targeted Stage 3-5 suite
was run with:

```bash
python -m pytest tests/test_workflow.py tests/test_iitt_evaluator.py \
  tests/test_mcp_tools.py tests/test_health.py -q
```

It passed **46 tests** after the final workflow changes. The workflow tests
cover pending/no-queue behavior, all four MCP calls, persisted resume from a
fresh workflow instance, duplicate-resume protection, unauthorized and stale
approval, incomplete information, evaluator unavailability, model
mismatch/timeout, loopback enforcement, JSON parsing, and an unauthenticated
API approval request. SQLite is used for this focused synthetic integration
suite; it does not replace running the PostgreSQL Alembic migrations and
repository suite against the configured test database.
