# Stage 5 Workflow Architecture

## Runtime path

1. `POST /workflows/triage` requires a trusted `TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER` and `triage:workflow:run` permission. The provider derives the clinician principal from the authenticated FastAPI request; request JSON cannot supply identity or permissions.
2. The request schema accepts synthetic encounter identifiers and observation payloads only. Workflow state begins with an explicit `received` status.
3. A LangGraph validation node calls the existing `validate_vitals` MCP tool over stdio. Invalid, incomplete, unavailable, or timed-out validation branches directly to clinician review; the deterministic evaluator is not called.
4. Valid and complete observations call `evaluate_triage_rules` through the same stdio MCP server. A tool error or timeout goes to clinician review without asking the model to supply clinical results.
5. The Ollama adapter (`POST /api/chat`) receives only the structured deterministic evaluation. Its JSON response is validated against `RecommendationDraft` and compared field-for-field against the expected tool-derived structure. Additional or changed claims reject the draft.
6. The workflow calls `request_clinician_review`. This tool creates a pending request and does not create a queue event. A workflow execution row records the final state, model identifier, transitions, action IDs, validation gates, and input/result SHA-256 digests, not raw intake payloads.
7. `POST /workflows/{workflow_id}/approve` requires both assessment-approval and queue-commit permissions from a fresh trusted identity-provider result. It checks the persisted workflow gates and the current assessment version, then calls `TriageToolsService.approve_review_request` for that exact review request.
8. The backend starts a child `triage-tools-mcp` stdio process with the authenticated clinician principal injected through its private process environment. The MCP tool independently checks permission, principal/approval equality, exact current assessment version, revocation, and idempotency before writing a queue event. The backend records the MCP commit action ID and marks the workflow committed only after a valid tool result.

## State transitions

`received -> validating -> validated -> evaluating -> evaluated -> generating_recommendation -> recommendation_validated -> requesting_clinician_review -> pending_clinician_review`

Validation/evaluation/model failures take a branch to `requesting_clinician_review`; if that request fails, the workflow ends as `failed`. A pending state is eligible for resume only if validation was valid and complete, an evaluator action exists, a model draft passed exact schema/content validation, and a review request exists. A rejected or failed commit does not become an approved action.

## Failure behavior

- No trusted FastAPI principal provider: workflow endpoints return `503` and do not start the graph.
- No deterministic evaluator: the MCP service returns `EVALUATOR_NOT_CONFIGURED`; a review request may be recorded, but the workflow is not eligible to commit.
- Ollama unavailable, timed out, malformed, or altered output: the result has no recommendation and cannot resume to commit.
- MCP tool failure or timeout: the workflow records an error and seeks clinician review where possible; it never synthesizes tool output.
- Stale assessment, wrong clinician, missing/revoked approval, permission failure, or idempotency conflict: the existing MCP service rejects the commit.

## Runtime and deployment

The workflow dependency group installs LangGraph, MCP, and the HTTP client. The model runs separately in a local Ollama-compatible runtime; default model identifier is `qwen3:1.7b`. On 2026-10-14 the official Ollama model catalog reported approximately 1.4 GB for that model. This development container exposed 2 vCPUs and 7.8 GiB RAM with no GPU runtime. Ollama was run in Docker and the model was pulled into a persistent volume. The full opt-in integration test using the actual model, real MCP stdio process, PostgreSQL, and test-only synthetic evaluator/principal passed. Its result verifies workflow wiring and safety gates, not clinical evaluation or production authentication.

The stdio MCP server still requires `DATABASE_URL` and `TRIAGE_MCP_CLINICAL_EVALUATOR`. Approval and commit also require trusted identity integration. The synthetic evaluator and identity provider used by the opt-in integration test live only under `tests/` and are not production configuration. No queue operation runs during workflow start. The model never receives approval authority or invokes the queue tool itself.

This is a synthetic-data competition prototype, not a clinically validated or approved medical device. No diagnosis, treatment, discharge, admission, or referral behavior is implemented. It must not delay direct clinical assessment or emergency response. See [EVALS.md](EVALS.md) for scenario-level checks and limitations.

## Stage 6 External Filesystem MCP

### Data flow

1. The FastAPI workflow request includes an explicit `protocol_id`; it does not contain a file path.
2. After vital validation succeeds, LangGraph starts the official Filesystem MCP package as an MCP stdio child in a Docker container.
3. The container is built from the exact npm package `@modelcontextprotocol/server-filesystem@1.0.0`, locked by `backend/filesystem-mcp/package-lock.json`, and a Node base image pinned by digest.
4. The process has `--network=none`, `--read-only`, dropped capabilities, and one read-only host mount at `/protocols`. The Filesystem server itself offers write-capable tools; only `read_text_file` and `list_allowed_directories` are called.
5. The client discovers tools, verifies the sole allowed root is `/protocols`, reads the registry, resolves exactly one document ID, checks approval status and a path-relative filename, retrieves the UTF-8 document, and verifies its SHA-256 against registry metadata.
6. The graph passes the resulting typed document reference to `evaluate_triage_rules`. The deterministic evaluator must return the same document version. The model receives only the evaluator result, never the document text.
7. Workflow persistence stores reference ID/version/source/license/class/checksum metadata and retrieval audit details, but not the source document body. The append-only audit event records `mcp.filesystem.read_text_file.succeeded` or `.failed` and refers to the workflow row containing server identity, tool name, timestamp, requested ID, returned version, digest, and error code.

### Fail-closed conditions

Missing/unavailable server, missing tool, an unexpected allowed root, missing/duplicate document ID, malformed registry, unapproved document, unsafe relative path, checksum mismatch, or invalid document response prevents evaluator invocation and requests clinician review where possible. Approval resume additionally rechecks that the stored evaluator version, retrieved version, document ID, digest, pinned server identity, and tool name agree. The external server cannot approve, change evaluator rules, or invoke the protected queue tool.

`docs/clinical-protocols/approved/registry.json` contains no clinical entries at this time. Therefore production clinical evaluation remains unavailable until a source is verified, reuse terms and clinical authorization are recorded, and the deterministic evaluator supports that exact version. Tests use only a synthetic non-clinical document. It is not a substitute protocol.

### Third-party package verification

- Repository: `modelcontextprotocol/servers`, official Filesystem server under `src/filesystem`.
- Package: `@modelcontextprotocol/server-filesystem`, npm stable `1.0.0`; published 2026-10-11. Repository release tag `v1.0.0` resolves to commit `c003d0d807409b22fa640d1f8e11f38f15755721`.
- Published npm integrity: `sha512-2fN6qhpsiPSBAe0ev1qDPk4sCWdULCZQTuYc4ZY7+BDWQ1XCxLB+7dqStF2ZflgG4G/9vTwQsjxh7pjFqrrpcg==`; the committed lockfile pins this tarball and all transitive versions.
- Compatibility: package manifest does not declare a Node engine. It built and ran on Node 24 in the pinned `node:24-alpine` container; the server uses MCP stdio and exposes documented read/write tools.
- License: GitHub API reports repository `NOASSERTION`; npm metadata declares `SEE LICENSE IN LICENSE`, but the published tarball has no license file. The source repository root contains a mixed-license transition notice (Apache-2.0 for new/consented work, MIT for some prior contributions, CC-BY-4.0 for non-specification documentation). Exact package-code licensing is unclear; no source is copied here, and redistribution requires legal review.

The reproducible build and integration command and actual results are recorded in [EVALS.md](EVALS.md). Docker daemon access is privileged and must be restricted to the backend service account.
