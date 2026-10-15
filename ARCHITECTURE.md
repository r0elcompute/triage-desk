# Triage Sentinel Architecture

## Scope and trust boundaries

This is a clinician-supervised engineering prototype using synthetic
encounters. It is not a medical device. It does not diagnose, prescribe, infer
that a person is safe, or independently change a live queue. The protocol
registry at `docs/clinical-protocols/approved/registry.json` is empty, so a
production-approved clinical reference is not available.

```text
Clinician browser
    |
    v
Next.js dashboard ---- same-origin proxy ----> FastAPI
                                               |  \
                                               |   \-- SQLAlchemy/PostgreSQL
                                               |
                                               +-- LangGraph, single workflow
                                                     |-- custom triage-tools-mcp (stdio)
                                                     |-- official Filesystem MCP (Docker/stdio)
                                                     `-- local Ollama HTTP API
```

The frontend contains no clinical rules and no direct database driver. The
backend owns authentication, workflow execution, persistence, and consequential
action checks. The two MCP servers are subprocesses, not network endpoints
exposed to the browser.

## Components

- **Next.js / TypeScript dashboard** (`frontend/`): synthetic intake, proposed
  assessment queue, observation/evidence detail, review controls, history, and
  visible error/pending states. It calls the backend through a same-origin
  proxy. Current audit finding: queue and detail GET routes do not authenticate.
- **FastAPI backend** (`backend/src/triage_sentinel/`): health, workflow, and
  dashboard API routes. A trusted clinician principal provider is expected for
  workflow and approval paths; none is configured by default.
- **PostgreSQL data layer**: encounters, observations, assessment versions,
  workflow executions, review requests, approvals, queue events, tool
  executions, and audit events. Local development and test databases must be
  separate.
- **Deterministic evaluator** (`evaluation/iitt.py`): encodes a documented,
  limited subset of WHO/ICRC/MSF routine IITT adult and pediatric vital-sign
  rules. Rule references, evidence, uncertainty, missing fields, and unsupported
  criteria are represented. No category is inferred from an absence of
  escalation. The source snapshot and reuse limitations are in
  [WHO-IITT.md](./docs/clinical-protocols/WHO-IITT.md).
- **LangGraph**: one stateful graph, not a multi-agent system. The package
  metadata installed in the audit environment identifies LangGraph 1.2.15 as
  MIT-licensed. This verifies an open-source orchestration dependency, not
  end-to-end execution on a clean checkout.
- **Ollama adapter**: the workflow sends structured evaluator output to a
  local Ollama-compatible endpoint; configured model defaults to
  `qwen3:1.7b`. The model may format a recommendation draft only. Its output
  must exactly match the deterministic tool result; it cannot create findings,
  evidence, thresholds, or approval.

## Workflow and state gates

The workflow in `triage_sentinel.workflow.service.TriageWorkflow` uses one
LangGraph graph with explicit transitions:

1. Receive a typed synthetic encounter request.
2. Call `validate_vitals` and check structure, units, timestamps, and caller
   required-measurement completeness.
3. On invalid/incomplete/tool failure, do not evaluate or call the model;
   request clinician review if the review tool remains available.
4. Retrieve the explicitly requested protocol from the Filesystem MCP server.
   Verify registry approval, safe relative path, version, and SHA-256. No local
   file fallback is used.
5. Call `evaluate_triage_rules` only when validation and reference checks pass.
6. Send the deterministic evaluation to Ollama for a structured draft; reject
   malformed, changed, or added claims.
7. Call `request_clinician_review`. The pending request cannot update the live
   queue.
8. Pause for a separately authenticated clinician decision.
9. On resume, the FastAPI backend checks workflow gates, exact assessment
   version, authenticated permissions, and approval state. It then invokes the
   protected MCP commit tool.
10. Record workflow state, transitions, tool action IDs, timestamps, and
    digests. Raw model/tool payloads are not included in the workflow execution
    record.

The model has no approval identity and cannot invoke its own queue commit.
Database-backed audit history exists for tool attempts and clinical review
actions. However, the complete authorization/audit design has not been
verified end-to-end in this checkout; see the findings below and
[EVALS.md](./EVALS.md).

## Custom MCP server: `triage-tools-mcp`

`backend/src/triage_tools_mcp/server.py` exposes typed Pydantic schemas through
MCP stdio:

| Tool                           | Intended behavior                                                                                                                                             | Queue effect                                 |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `validate_vitals`              | Validate observation values, units, timestamps, and explicit completeness requirements; return issues and an action ID.                                       | None                                         |
| `evaluate_triage_rules`        | Invoke the configured deterministic evaluator and return protocol/rule findings and evidence.                                                                 | None                                         |
| `request_clinician_review`     | Create a pending request bound to an assessment version.                                                                                                      | None                                         |
| `commit_approved_queue_update` | Independently check authenticated clinician permission, approval ownership/status, current assessment version, revocation, and idempotency; audit the result. | Append a queue event only after checks pass. |

No unrestricted SQL or database tool is exposed to the model. The tool process
expects `DATABASE_URL`; `TRIAGE_MCP_CLINICAL_EVALUATOR` and
`TRIAGE_MCP_PRINCIPAL_PROVIDER` configure Python `module:attribute` adapters.
The principal provider is a trusted-code boundary and must not accept
model/request-supplied identity.

**Known defect:** the current MCP `EvaluateTriageInput` lacks `age_months` and
presenting-complaint context required by the IITT evaluator. Its output schema
also narrows status/findings compared with the evaluator output. The latest
test observed `INTERNAL_ERROR` rather than the expected structured evaluator
error. The MCP evaluation path is therefore not currently reliable.

## Borrowed Filesystem MCP server

The reference reader uses the official
`@modelcontextprotocol/server-filesystem` package from
`modelcontextprotocol/servers`, pinned at version `1.0.0` and commit
`c003d0d807409b22fa640d1f8e11f38f15755721`. A Docker image pins Node 24 by
digest, uses the committed npm lockfile, runs with networking disabled and a
read-only root filesystem, drops capabilities, and mounts only the approved
reference directory read-only at `/protocols`.

The workflow discovers tools and verifies the root, reads the registry and
selected reference using `read_text_file`, validates approval/path/version, and
checks the source content SHA-256. It does not call write tools and does not
send document text to Ollama. The official server exposes write-capable tools,
so the container boundary remains important.

**License blocker:** the repository license API reports `NOASSERTION`; npm
metadata says `SEE LICENSE IN LICENSE`, but the published package tarball has
no license file. The upstream source has a mixed-license transition notice.
No package source is copied into this repository, but the precise terms for
the pinned package require legal review before redistribution.

## Model and local inference

The adapter uses Ollama's `/api/chat`, JSON-schema output mode, and temperature
zero. The configured model ID is persisted with workflow metadata. The upstream
[Qwen3-1.7B model card](https://huggingface.co/Qwen/Qwen3-1.7B) declares an
Apache-2.0 license; this is an open-weight model family, but the exact Ollama
artifact digest is not pinned here. No cloud model endpoint is selected by
default; however, `TRIAGE_OLLAMA_URL` is configurable and must remain loopback
unless remote use is explicitly reviewed.

At this audit, this Windows host reported 4 logical processors, 11.9 GiB RAM,
and Intel HD Graphics; Ollama was not installed/responding and no model
completion was verified. The preceding integration environment recorded one
successful 39-test live run; a later attempt reported 38 passed and one
`MODEL_UNAVAILABLE` failure. The later failure is the current result, not a
claim of local readiness.

## Human approval and audit

- Workflow start and approval must obtain a principal from a trusted backend
  provider; request JSON and LLM output are not identity sources.
- Pending review is distinct from an approved clinician decision.
- The protected commit tool independently checks exact approval/assessment
  binding, current version, revocation, authorization, and idempotency.
- Successful and failed tool actions are represented by execution and audit
  records with action IDs, timestamps, and digests. Review/override records
  include clinician reference and reason.
- Dashboard review/override requires authorization; overrides require a
  reason. The protected queue commit is not equivalent to recording a review.

Audit gaps: dashboard queue/detail reads are unauthenticated, complaint text is
not guarded against sensitive-looking values, and the database-backed full
suite could not run in the latest environment. These findings preclude a
submission-ready security claim.

## License

The root [MIT license](./LICENSE) is an
[OSI-approved license](https://opensource.org/license/mit/) and applies to
project-authored material only.
Third-party dependencies and the WHO reference criteria have separate
copyright/license terms. See [WHO-IITT.md](./docs/clinical-protocols/WHO-IITT.md)
and the Filesystem MCP notice above; do not infer that the root license grants
rights to external content.
