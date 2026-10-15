# Evaluation Record

## Scope and result summary

This evaluation uses synthetic cases only. It is an engineering verification,
not clinical validation, safety validation, or evidence of suitability for
patient care. Expected behavior is based on the selected protocol subset and
software contracts; no expected result was changed to fit an observation.

Latest full backend result recorded in the Stage 9 audit:

```text
python -m pytest -q
54 passed, 7 skipped, 1 failed, 14 errors in 6.15 s
```

The 14 errors are database-test fixture setup failures because
`TEST_DATABASE_URL` was unset. The failed MCP evaluation test is an actual
schema/runtime defect: `EvaluateTriageInput` lacks `age_months`, which the
IITT evaluator immediately accesses, so the result is `INTERNAL_ERROR`.

Fresh focused rerun for this submission:

```text
python -m pytest -q tests/test_iitt_evaluator.py tests/test_mcp_tools.py \
  tests/test_workflow.py tests/test_reference_mcp.py
42 passed, 7 skipped, 1 failed in 3.99 s
```

It reproduced the same MCP/evaluator failure. The skips are opt-in integration
tests requiring PostgreSQL, Ollama, Docker, and/or the Filesystem MCP image.

Other recorded checks:

| Check                                                       | Actual result                                                                                                                                                               |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| IITT evaluator suite                                        | 27 passed                                                                                                                                                                   |
| MCP tools suite                                             | 6 passed, 1 failed                                                                                                                                                          |
| Workflow suite                                              | 9 passed, 2 skipped                                                                                                                                                         |
| Dashboard API auth checks                                   | 4 passed, 1 deselected                                                                                                                                                      |
| Workflow + reference MCP tests with opt-in integrations off | 9 passed, 7 skipped                                                                                                                                                         |
| Frontend lint, typecheck, production build                  | Passed                                                                                                                                                                      |
| Ruff on changed backend modules and Python compileall       | Passed                                                                                                                                                                      |
| Repository-wide `ruff check backend/src tests`              | Failed: 5 findings in untouched files                                                                                                                                       |
| Most recent live Ollama attempt                             | 38 passed, 1 failed (`MODEL_UNAVAILABLE`)                                                                                                                                   |
| Prior live integration attempt                              | 39 passed in 91.31 s; historical only, does not supersede the later failure                                                                                                 |
| Custom MCP stdio process                                    | Tool discovery listed all four tools; `validate_vitals` returned valid/incomplete with missing `heart_rate`; 1 tool execution and 1 audit row persisted in temporary SQLite |
| Official Filesystem MCP                                     | Discovery failed with `FileNotFoundError` because Docker CLI/runtime is unavailable                                                                                         |

Detailed commands, timings, tool results, findings, and source links are in
[docs/STAGE9_AUDIT.md](./docs/STAGE9_AUDIT.md).

## Synthetic scenarios

The detailed audit matrix has 19 scenarios. This summary retains the actual
outcome and tool/approval state; see the linked matrix for input details,
evidence, exact timings, and test references.

|   # | Scenario                                                  | Actual result                                                                                  | Tool behavior / approval state                                  |
| --: | --------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
|   1 | Ordinary adult observations; no escalation rule fires     | **PASS**: `indeterminate`, no category; not labelled safe                                      | Evaluator only; no approval/queue write                         |
|   2 | Adult heart rate 49 bpm                                   | **PASS**: red rule `IITT-ADULT-RED-HEART-RATE`, evidence linked                                | Evaluator finding only; no approval                             |
|   3 | Missing observations and complaint                        | **PASS**: missing information explicit; no normal defaults                                     | No queue write                                                  |
|   4 | Wrong unit, naive/future timestamp, duplicate observation | **PASS**: validation rejects invalid structures                                                | No evaluation/approval                                          |
|   5 | Stale/duplicate reassessment trigger                      | **BLOCKED**: PostgreSQL fixture errors; ad-hoc SQLite attempt also failed before assertion     | No completed reassessment call                                  |
|   6 | Authenticated clinician rejects synthetic assessment      | **PASS** in one-off in-memory API check; review/audit record observed                          | Rejected; no queue commit                                       |
|   7 | Authorized clinician overrides with reason                | **PASS** in one-off in-memory API check; override/audit record observed                        | Override recorded; not a queue approval                         |
|   8 | Override without required reason fields                   | **PASS**: HTTP 422                                                                             | No review/approval                                              |
|   9 | Unauthenticated protected MCP queue commit                | **PASS**: `UNAUTHENTICATED`, audited                                                           | No unauthorized commit                                          |
|  10 | Stale and revoked approvals                               | **PASS**: `STALE_ASSESSMENT` and `APPROVAL_REVOKED`                                            | No queue commit                                                 |
|  11 | Duplicate commit / idempotency key conflict               | **PASS**: `already_committed` on retry; conflicting reuse rejected                             | Exactly one queue event                                         |
|  12 | Duplicate identical clinician review request              | **PASS against current contract**: two pending IDs and audit rows; retry semantics not defined | No approval; duplicate review requests remain a design question |
|  13 | Model output adds an unsupported finding                  | **PASS**: `MODEL_OUTPUT_REJECTED`; pending review                                              | No commit tool call                                             |
|  14 | Protocol reference unavailable                            | **PASS**: `PROTOCOL_DOCUMENT_NOT_FOUND`; evaluation skipped                                    | Pending review; no commit                                       |
|  15 | MCP evaluation input omits evaluator-required age         | **FAIL**: actual `INTERNAL_ERROR` / `AttributeError` instead of structured evaluator error     | No approval or queue commit                                     |
|  16 | Dashboard queue with persistence unconfigured             | **PASS**: explicit HTTP 503, not empty success                                                 | No queue update                                                 |
|  17 | Anonymous dashboard encounter detail read                 | **FAIL**: HTTP 200 returned synthetic reference and complaint without identity provider        | Read-only; privacy/access-control finding                       |
|  18 | Complaint contains sensitive-looking text                 | **FAIL**: request accepted unchanged                                                           | No database write in check; synthetic-only intake guard absent  |
|  19 | Invalid then valid `validate_vitals` MCP calls            | **PASS**: structured invalid/incomplete then valid/complete result; both audited               | No approval/queue update                                        |

## Protocol traceability

Clinical test expectations trace to the documented limited routine IITT
subset, not to an LLM-generated rubric. It covers selected adult and pediatric
heart-rate, respiratory-rate, temperature, oxygen saturation, AVPU, and
under-two-month temperature criteria. Rules, exact comparators, source
locations, ages, limitations, provenance, and reuse concerns are documented in
[WHO-IITT.md](./docs/clinical-protocols/WHO-IITT.md).

The evaluator is deterministic and can be tested directly. However, the custom
MCP input/output contract currently does not carry all evaluator inputs/results,
and the approved production registry is empty. Therefore test-level evaluator
success must not be represented as a usable production workflow.

## Live model and MCP status

The repository records an earlier opt-in integration run using local Ollama,
the custom MCP stdio process, the official Filesystem MCP container,
PostgreSQL, and test-only synthetic evaluator/identity adapters: **39 passed
in 91.31 seconds**. The later attempt reported **38 passed and 1 failed** with
`MODEL_UNAVAILABLE`; current environment checks also found no responding
Ollama endpoint, Docker CLI, or PostgreSQL test configuration. In this
submission run, the custom stdio server successfully discovered its four tools
and executed `validate_vitals`, persisting one tool execution and one audit row
in a temporary SQLite fixture. This did not verify PostgreSQL behavior or the
broken evaluator path. Filesystem MCP discovery failed because the Docker
executable was unavailable.

The framework dependency is open source: installed package metadata identified
LangGraph 1.2.15 as MIT. This does not establish full clean-checkout execution.

## Unresolved failure and next step

The genuine runtime failure is the MCP/evaluator schema mismatch: the MCP
input lacks `age_months` and presenting-complaint context used by the
deterministic evaluator, while its output schema narrows evaluator statuses.
The latest MCP test observed `INTERNAL_ERROR` at `data.age_months`; no approval
or queue write occurred.

**Next step:** define one versioned typed contract that carries age and
complaint data only from explicit validated encounter inputs, supports all
evaluator statuses/findings, and preserves evidence references. Add a
successful evaluator-through-real-MCP test with a synthetic approved test
protocol, then rerun the MCP, workflow, database, and full suites. Do not infer
or fabricate missing clinical inputs to make the call pass.

## Submission gaps

- No clean-checkout run has been performed; current worktree contains
  uncommitted changes.
- No current local model task completed because Ollama is unavailable here.
- Both MCP servers need fresh integration verification with configured local
  services.
- The offline `python scripts/demo_workflow.py` trace ran successfully, showing
  the graph's pending-review path and missing-reference failure using explicit
  test doubles. It is not a recording, live MCP tool run, or model execution.
- No video has been recorded. The workflow script below uses test doubles and
  does not meet the live demonstration requirement.
- Database outage injection, packet-level egress capture, and full
  database-backed audit checks were not performed.
- A targeted scan for common credential/private-key token patterns found no
  matches in the current working tree. Git tracks only `.env.example` files
  among environment-file names. The evaluation used synthetic identifiers;
  this scan is not a guarantee against every possible secret or personal-data
  format.
- No external request was made during this audit. Source review found
  configurable remote backend/model URLs, and no packet-level egress capture
  was performed.
