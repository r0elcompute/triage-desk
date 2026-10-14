# Triage Sentinel foundation

## Stage 1

Stage 1 established the Next.js frontend and FastAPI health endpoint. The
frontend uses a same-origin `/api/health` proxy to check the backend. No
clinical rules or agents were added.

## Stage 2 data layer

The persistence package lives in `backend/src/triage_sentinel/data/`. It
contains SQLAlchemy models, Pydantic validation schemas, a PostgreSQL engine
and session helper, and repositories that accept caller-managed SQLAlchemy
sessions. It is separate from FastAPI routes and does not implement clinical
decision logic.

The schema stores synthetic patient references only, with no names, dates of
birth, contact details, or raw tool inputs/outputs. Vital observations retain
their value, canonical unit, measurement time, and source. Supporting evidence
references complaints or observations belonging to the same encounter.

Assessment recommendations, evidence, clinician reviews, queue events, tool
execution metadata, and audit records are append-only. A changed recommendation
must be stored as a new assessment version; database triggers reject updates
and deletes to those history tables. Tool logs retain SHA-256 digests and error
codes only, never request/response payloads.

Run `python -m alembic upgrade head` from `backend/` to create the schema.
Repository tests require `TEST_DATABASE_URL` to point to a dedicated
PostgreSQL test database. They migrate an isolated temporary schema and remove
it after the test session. See the root README for setup and commands.

Configuration examples are in `backend/.env.example` and
`frontend/.env.example`. Copy them to local environment files as described in
the root README; local environment files must never be committed.

## Stage 3 deterministic evaluation

The project selects the WHO/ICRC/MSF routine Interagency Integrated Triage
Tool (IITT) for unscheduled, acutely ill or injured patients in facility-based
emergency units: the adult poster applies at age 12 and above, and the
pediatric poster below 12. Mass-casualty triage is excluded. The project does
not claim Kenyan Ministry of Health approval or universal outpatient/health
post applicability.

The canonical [protocol record](./clinical-protocols/WHO-IITT.md) documents
official sources, version metadata, reuse terms, exact implemented criteria,
and unsupported rules. The evaluator in
`backend/src/triage_sentinel/evaluation/` validates synthetic typed complaints
and observations and returns versioned, evidence-linked findings. It
implements a subset of high-risk vital-sign criteria plus the adult red
heart-rate and under-2-month pediatric red temperature criteria. Positive
red findings identify red; high-risk vital signs require clinician review;
all other cases remain indeterminate because remaining red/yellow criteria
are not encoded. It never assigns yellow or green and never writes a queue
event. Source-derived synthetic test expectations and actual results are in
[EVALS.md](./EVALS.md).

The adult and pediatric poster PDFs do not include a separate reuse notice
visible in the retrieved documents. The reference card states CC BY-NC-SA
3.0 IGO; see the protocol record for WHO's general policy and the need to
confirm rights before commercial use or redistribution. No source PDFs are
copied into this repository.

## Stage 4 MCP server

`backend/src/triage_tools_mcp/` contains the `triage-tools-mcp` stdio MCP
server. Install it from the repository root with:

```bash
python -m pip install -e "backend[mcp,dev,database]"
cd backend
python -m alembic upgrade head
triage-tools-mcp
```

The server requires `DATABASE_URL`. It exposes typed Pydantic input and output
schemas for:

- `validate_vitals`: checks observation structure, measurement/unit pairs,
  finite values, timezone-aware measurement times, and the measurements
  explicitly required by the caller.
- `evaluate_triage_rules`: calls the configured deterministic evaluator and
  returns its findings, evidence, uncertainty, and protocol version. By
  default the server uses the bundled WHO IITT subset evaluator. An optional
  `TRIAGE_MCP_CLINICAL_EVALUATOR` can replace it with an object implementing
  `evaluate(EvaluateTriageInput) -> EvaluateTriageOutput`. Clinical logic
  belongs in the backend evaluator, not in the MCP transport handlers.
- `request_clinician_review`: creates an immutable pending request for the
  exact assessment version; it does not write a queue event.
- `commit_approved_queue_update`: writes a queue-entry event only after the
  caller identity is authenticated and authorized, the approval belongs to
  that same clinician and exact assessment version, the assessment is still
  current, and the approval has not been revoked. An idempotency key and
  one-commit-per-approval constraints prevent duplicate queue entries.

Set `TRIAGE_MCP_CLINICAL_EVALUATOR` to `python.module:class_or_object` for an
object implementing `evaluate(EvaluateTriageInput) -> EvaluateTriageOutput`.
Set `TRIAGE_MCP_PRINCIPAL_PROVIDER` to `python.module:class_or_callable` for an
object callable with the MCP request context that returns an authenticated
`AuthenticatedClinician` with trusted `subject`, synthetic clinician
`reference`, and permissions. The server does not accept identity or
permissions in tool arguments. With no identity provider configured (the
default), queue commits fail closed as unauthenticated; do not expose the stdio
server to untrusted clients or configure a provider that trusts model-supplied
fields.

Tool executions store timestamps, action IDs, error codes, and SHA-256 input
and output digests rather than raw tool payloads. Successful and failed
attempts are associated with append-only audit records. Audit records and new
review/approval/commit tables are added by Alembic revision
`0002_mcp_review_approvals`.

## Stage 5 LangGraph workflow

The FastAPI backend now exposes a persisted LangGraph workflow that calls the
four MCP tools over the in-process MCP SDK transport. It validates inputs,
records deterministic evaluator output and protocol version, validates a
constrained Ollama model acknowledgement, creates a pending clinician review,
and stops before any queue change. Queue mutation is only available through
the MCP commit tool after a clinician resumes the workflow with an exact,
active approval. See [WORKFLOW.md](./WORKFLOW.md) for the full execution path,
routes, local model configuration, authentication boundary, hardware check,
test outcomes, and known limitations.

Install `backend[workflow,mcp]` to enable the optional graph dependency.
`DATABASE_URL` must be configured to enable workflow routes. The application
does not bundle authentication middleware: approval requests fail closed
unless trusted middleware populates `request.state.authenticated_clinician`.
Do not derive that trusted state from model output or request body fields.
The separately configured `triage-tools-mcp` stdio service remains available;
the FastAPI workflow uses the same service methods through the MCP protocol
in-process.
