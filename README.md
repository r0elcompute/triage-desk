# Triage Sentinel

Triage Sentinel is a synthetic-data, clinician-supervised triage workflow
prototype. It combines a Next.js dashboard, FastAPI backend, a deterministic
subset of the WHO/ICRC/MSF routine IITT protocol, a custom MCP tool server, a
LangGraph workflow, a local Ollama adapter, and an external Filesystem MCP
reference reader.

It is not a medical device and is not clinically validated. It does not
diagnose, prescribe, or independently approve an action. An absent escalation
finding is not a finding that a patient is safe. The approved clinical protocol
registry is empty, and the current MCP/evaluator schema mismatch prevents
reliable end-to-end deterministic evaluation. Do not use this prototype for
patient care.

## Competition submission status

The repository contains the requested architecture and evaluation
documentation, an MIT license for project-authored material, and an offline
workflow trace script. It is **not yet submission-ready**:

- This environment has no configured test PostgreSQL database, Ollama,
  Docker, trusted clinician identity provider, or approved clinical protocol.
- The latest backend suite recorded **54 passed, 7 skipped, 1 failed, 14
  errors**. The details and the genuine MCP/evaluator failure are in
  [EVALS.md](./EVALS.md) and [docs/STAGE9_AUDIT.md](./docs/STAGE9_AUDIT.md).
- The video requirement is not met. No recording was made; an offline trace
  using test doubles is not a real live model/MCP demonstration.
- The borrowed Filesystem MCP package's precise license terms remain
  unresolved; review [ARCHITECTURE.md](./ARCHITECTURE.md) before distributing
  it.

The competition checklist below evaluates the requirements provided to this
project; no separate competition rulebook was supplied.

| Requirement                                                                   | Current evidence                                                                             | Status                                               |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Purpose, setup, configuration, limitations                                    | This README                                                                                  | Documented                                           |
| Architecture, workflow, MCP, model, approval/audit                            | [ARCHITECTURE.md](./ARCHITECTURE.md)                                                         | Documented with known defects                        |
| Ten or more synthetic scenarios and actual results                            | [EVALS.md](./EVALS.md), detailed Stage 9 matrix                                              | Documented; failures remain                          |
| OSI-approved license                                                          | [LICENSE](./LICENSE) (MIT, project-authored work only)                                       | Added; third-party terms still apply                 |
| Demonstration script                                                          | `python scripts/demo_workflow.py`                                                            | Offline graph trace only; test doubles               |
| Unedited video under three minutes with visible real tool calls and a failure | None recorded                                                                                | **Blocked**                                          |
| Custom MCP server                                                             | Four tools discovered; `validate_vitals` executed and audit persisted using temporary SQLite | Partial; PostgreSQL and evaluation path not verified |
| Filesystem MCP server                                                         | Discovery attempted                                                                          | **Blocked**: Docker executable unavailable           |
| Clean-checkout execution and local model completion                           | Not verified in this environment                                                             | **Blocked**                                          |

## Prerequisites

For the application and tests:

- Python 3.11 or later
- Node.js 20.19 or later and npm
- PostgreSQL 15 or later
- Git

For the optional integrations:

- Ollama and the configured local Qwen3 1.7B open-weight model (upstream model
  card declares Apache-2.0; the Ollama artifact itself is not digest-pinned)
- Docker for the pinned Filesystem MCP container
- A trusted backend clinician identity provider
- An explicitly authorized protocol entry and a compatible configured
  deterministic evaluator

The project has no one-command full-stack launcher: these integrations need
separate configuration and some are intentionally unavailable by default.

## Setup

Commands below use PowerShell on Windows. Create local virtual environment and
install the backend, database driver, MCP, orchestration, and test dependencies:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e "backend[dev,database,orchestration]"
```

Install the frontend:

```powershell
Copy-Item frontend\.env.example frontend\.env.local
Push-Location frontend
npm ci
Pop-Location
```

Copy the backend example configuration:

```powershell
Copy-Item backend\.env.example backend\.env
```

Set `DATABASE_URL` in `backend\.env` to a local PostgreSQL database, then
apply the schema migrations:

```powershell
Push-Location backend
python -m alembic upgrade head
Pop-Location
```

Database-backed tests require a dedicated disposable PostgreSQL database whose
name includes `test`. Set `TEST_DATABASE_URL` in the current shell before
running them. The test fixture migrates an isolated schema and removes it
afterward.

## Run locally

Start FastAPI in one terminal:

```powershell
.\.venv\Scripts\Activate.ps1
Set-Location backend
python -m uvicorn triage_sentinel.main:app --reload
```

Start Next.js in another:

```powershell
Set-Location frontend
npm run dev
```

Open <http://localhost:3000>. Backend health is at
<http://localhost:8000/health>. Without database configuration the queue
returns an explicit service error. Without a trusted identity provider,
workflow session/start endpoints fail closed; this is expected, not a
successful clinical workflow.

The backend MCP server is installed as the `triage-tools-mcp` command and is
started by the workflow over stdio:

```powershell
python -m pip install -e "backend[mcp,database]"
triage-tools-mcp
```

The standalone command expects a configured `DATABASE_URL`. It does not expose
arbitrary database access. See [ARCHITECTURE.md](./ARCHITECTURE.md) for the
four typed tools and trust boundaries.

### Optional local model and reference MCP

Install Ollama separately, pull the configured model, and start its service:

```powershell
ollama pull qwen3:1.7b
ollama serve
```

Configure `TRIAGE_OLLAMA_MODEL` and `TRIAGE_OLLAMA_URL` in `backend\.env`.
This repository does not install or launch Ollama.

Build the external Filesystem MCP image from the repository root:

```powershell
docker build -t triage-filesystem-mcp:1.0.0 backend/filesystem-mcp
```

Set `TRIAGE_PROTOCOL_REFERENCE_DIRECTORY` to
`..\docs\clinical-protocols\approved` when the backend process working
directory is `backend\`, or use an absolute path, and set
`TRIAGE_FILESYSTEM_MCP_IMAGE` to the image tag. The
registry is intentionally empty; a real clinical reference will not be
substituted.

## Configuration

| Variable                              | Purpose                             | Default / state                               |
| ------------------------------------- | ----------------------------------- | --------------------------------------------- |
| `DATABASE_URL`                        | Application and MCP persistence     | Must point to local PostgreSQL                |
| `TEST_DATABASE_URL`                   | Disposable PostgreSQL test database | Not configured by default                     |
| `CORS_ORIGINS`                        | Allowed browser origins             | Localhost ports 3000                          |
| `TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER`  | Trusted FastAPI identity provider   | Unset; required for workflow start            |
| `TRIAGE_MCP_PRINCIPAL_PROVIDER`       | Trusted MCP-side principal provider | Unset; commit fails closed                    |
| `TRIAGE_MCP_CLINICAL_EVALUATOR`       | Evaluator object `module:attribute` | Unset; MCP contract also needs reconciliation |
| `TRIAGE_OLLAMA_URL`                   | Local Ollama API endpoint           | `http://127.0.0.1:11434`                      |
| `TRIAGE_OLLAMA_MODEL`                 | Local open-weight model identifier  | `qwen3:1.7b`                                  |
| `TRIAGE_MCP_COMMAND`                  | Custom MCP executable               | `triage-tools-mcp`                            |
| `TRIAGE_PROTOCOL_REFERENCE_DIRECTORY` | Read-only reference directory       | See `backend/.env.example`                    |
| `TRIAGE_FILESYSTEM_MCP_IMAGE`         | Pinned reference MCP image tag      | `triage-filesystem-mcp:1.0.0`                 |
| `TRIAGE_API_URL`                      | Frontend proxy backend destination  | `http://127.0.0.1:8000`                       |

Local `.env` files are ignored by Git. Keep credentials and private data out of
the repository. The proxy and model URLs are configurable; do not set them to
untrusted remote hosts, especially where authorization headers could be
forwarded.

## Checks

```powershell
# From the repository root
$env:TEST_DATABASE_URL = "postgresql+psycopg://localhost:5432/triage_sentinel_test"
python -m pytest -q

# From frontend/
npm run lint
npm run typecheck
npm run build
```

An offline LangGraph control-flow trace is available with:

```powershell
python scripts/demo_workflow.py
```

It uses in-memory test doubles for MCP and the model, and is explicitly **not**
evidence that Ollama or either MCP server is available. Actual results and
limitations are summarized in [EVALS.md](./EVALS.md).

## Troubleshooting

- **`Workflow persistence is not configured`**: set `DATABASE_URL`, start
  PostgreSQL, and apply Alembic migrations.
- **`Trusted workflow identity provider is not configured`**: provide a
  backend-trusted provider; never supply clinician identity through request
  fields or model output.
- **`EVALUATOR_NOT_CONFIGURED` or MCP `INTERNAL_ERROR`**: the production
  evaluator is not configured and the present MCP/evaluator schemas are
  incompatible. Do not add inferred age or complaint data to work around it.
- **`FILESYSTEM_MCP_UNAVAILABLE`**: install/start Docker, build the pinned
  image, and check the approved reference directory. The registry being empty
  is an intentional fail-closed condition.
- **Ollama connection/model errors**: start Ollama locally and confirm
  `ollama list` includes the exact configured model.
- **Test fixture errors**: configure a dedicated PostgreSQL database through
  `TEST_DATABASE_URL`; SQLite is not a substitute for repository integration
  tests.

## Known limitations

- No production identity provider, approved clinical protocol registry entry,
  or live production evaluator is configured.
- `EvaluateTriageInput` in the MCP schema omits context required by the
  deterministic IITT evaluator, and the MCP result schema cannot represent all
  evaluator statuses. The current MCP evaluation path has a genuine failing
  test.
- Dashboard queue/detail reads currently lack authentication.
- Synthetic intake accepts free text that can contain sensitive-looking
  strings.
- The unused `workflow.engine` / `workflow.repository` path does not match the
  active ORM model and needs repair or decommissioning.
- The full backend suite is not green; database-dependent tests were blocked
  in the latest run.
- The latest live Ollama integration attempt failed with `MODEL_UNAVAILABLE`;
  an earlier successful run is historical only.
- Exact license terms for the borrowed Filesystem MCP package are unresolved.
- No clean-checkout run or qualifying demo video has been produced for this
  submission.
