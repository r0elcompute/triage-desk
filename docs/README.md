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

## Stage 5 LangGraph workflow

`backend/src/triage_sentinel/workflow.py` defines a single explicit LangGraph
workflow. It validates synthetic input through `validate_vitals`, calls the
configured deterministic `evaluate_triage_rules` MCP tool only when validation
is valid and complete, requests a structured Ollama response, checks that the
response exactly matches the tool output, and creates a pending clinician
review through `request_clinician_review`. MCP tools are called through the
existing stdio MCP server. Tool output, not the model, is authoritative.

The API exposes `POST /workflows/triage` and
`POST /workflows/{workflow_id}/approve`. A workflow execution record stores the
model identifier, transition timestamps, tool action IDs, digests, validation
gates, review request, and result status; raw observation/request/response
payloads are not persisted in that record. Alembic revision
`0003_workflow_executions` adds its table. See [ARCHITECTURE.md](../ARCHITECTURE.md)
for the complete execution sequence and [EVALS.md](../EVALS.md) for actual
synthetic test outcomes.

Workflow start requires `TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER` to identify a
trusted backend callable that accepts the FastAPI request and returns an
`AuthenticatedClinician`. It must derive subject, synthetic reference, and
permissions from trusted authentication state, not request body fields. The
default is unset and fails closed. The approve endpoint independently requires
both `triage:assessment:approve` and `triage:queue:commit`. It creates or
reuses an approval only for the same authenticated clinician, then invokes the
existing protected MCP commit tool in a child stdio server with that trusted
principal. The MCP service rechecks permission, approval ownership, revocation,
and current assessment version. The model cannot approve or commit.

Ollama is the selected local inference runtime adapter. The default model is
`qwen3:1.7b`; the official Ollama catalog listed it at approximately 1.4 GB on
2026-10-14. This container exposed 2 vCPUs, 7.8 GiB RAM, and no GPU runtime.
Ollama ran in Docker and the model completed synthetic integration tests; this
does not assess clinical quality or production performance. Set
`TRIAGE_OLLAMA_MODEL` and `TRIAGE_OLLAMA_URL` to configure another local Ollama
model/server. Inference/network failures create a pending review request when
possible, but do not produce a recommendation or allow the workflow to resume
into a queue commit.

## Stage 6 third-party Filesystem MCP

Stage 6 connects to the official `@modelcontextprotocol/server-filesystem`
npm package at version `1.0.0` (tag `v1.0.0`, commit
`c003d0d807409b22fa640d1f8e11f38f15755721`). Its locked runtime is packaged in
`backend/filesystem-mcp/`; it is an external dependency, not a team-authored
server. The application uses the existing MCP SDK over stdio, discovers tools,
confirms `list_allowed_directories` contains only `/protocols`, then calls
`read_text_file` for the registry and selected UTF-8 reference.

Set `TRIAGE_PROTOCOL_REFERENCE_DIRECTORY` to
`docs/clinical-protocols/approved` and build the runtime image with
`docker build -t triage-filesystem-mcp:1.0.0 backend/filesystem-mcp`. The child
container has no network, a read-only root filesystem, dropped Linux
capabilities, and a read-only bind mount of that directory. The external server
exposes write-capable tools, but the workflow does not call them. Actual
integration tests verify both outside-root reads and writes to the mount are
blocked. Docker daemon access is privileged; restrict it to the trusted backend
runtime.

Each registry entry specifies document ID, version, approval status, relative
filename, SHA-256, source URL, and license/reuse information. A retrieval audit
records server identity, tool name, time, requested ID, returned version,
content digest, and success/error in the workflow record and an append-only
audit event. Raw patient data is not included in the retrieval event. Missing,
malformed, unapproved, or checksum-mismatched references block evaluation and
route to clinician review. Retrieved content is reference data, never model
instructions; it grants no approval or queue authority.

The production-approved registry at
`docs/clinical-protocols/approved/registry.json` is intentionally empty. This
checkout has no verified and authorized clinical protocol; the non-clinical
test fixture must never be copied into production. Therefore the integration
proves external-server wiring and safety boundaries, not an operational IITT
evaluation.

### Package verification and license

The official GitHub API reports repository license `NOASSERTION`; the package
metadata says `SEE LICENSE IN LICENSE`, but its published `1.0.0` npm tarball
contains no license file. The repository-root license text describes a
transition: Apache-2.0 for new or explicitly relicensed contributions, MIT for
some earlier contributions, and CC-BY-4.0 for non-specification documentation.
The exact license applicable to all code in this package is unclear. This
repository consumes the pinned package without copying its source; obtain legal
review before redistribution if required.

See [ARCHITECTURE.md](../ARCHITECTURE.md) for the data flow and [EVALS.md](../EVALS.md)
for commands and actual results.

The clinical evaluator remains unconfigured in this repository. Therefore the
workflow can be exercised with synthetic fakes, but it cannot complete an
operational clinical evaluation or an approved queue commit without the
separately authorized IITT evaluator, identity provider, PostgreSQL database,
and local inference runtime. The project is a competition prototype, not a
clinically validated or approved medical device. Direct clinician assessment
and emergency response must not be delayed by this workflow.
