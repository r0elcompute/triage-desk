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
  returns its findings, evidence, and protocol version. No clinical evaluator
  exists in this checkout, so the tool returns
  `EVALUATOR_NOT_CONFIGURED` until one is provided. Clinical logic belongs in
  the backend evaluator, not in the MCP tool handlers.
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

The authenticated backend review workflow must call
`TriageToolsService.approve_review_request` and `revoke_approval` only after
its own authentication and authorization checks. Those trusted backend
operations issue and revoke immutable, assessment-bound approvals; neither is
exposed as an MCP tool.

Tool executions store timestamps, action IDs, error codes, and SHA-256 input
and output digests rather than raw tool payloads. Successful and failed
attempts are associated with append-only audit records. Audit records and new
review/approval/commit tables are added by Alembic revision
`0002_mcp_review_approvals`.
