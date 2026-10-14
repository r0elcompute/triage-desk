# Triage Sentinel

Triage Sentinel is the foundation for a healthcare triage MVP. This repository
contains a Next.js frontend, a FastAPI backend, the Stage 4
`triage-tools-mcp` server, and the Stage 5 LangGraph workflow. It includes a
limited, deterministic evaluator for
selected WHO IITT vital-sign criteria; it is not a complete clinical
evaluator, does not provide medical advice, and is not clinically validated.

## Prerequisites

- Node.js 20.19 or newer and npm
- Python 3.11 or newer
- PostgreSQL 15 or newer

PostgreSQL is required for the Stage 2 data layer and repository tests.
Supabase and orchestration remain optional integrations. See
[docs/README.md](./docs/README.md) for the selected protocol, evaluator
limitations, MCP setup, tool details, and identity-provider configuration.

## Setup

From the repository root, create and activate a Python virtual environment and
install the backend, development, MCP, and workflow dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "backend[dev,database,mcp,workflow]"
```

In another terminal, install the frontend dependencies and create its local
environment file:

```bash
cd frontend
cp .env.example .env.local
npm ci
```

The backend reads `backend/.env` when started from the `backend` directory.
Copy its example if you want to customize the defaults:

```bash
cp backend/.env.example backend/.env
```

Environment files are ignored by Git. Do not put secrets in tracked files.
Set `DATABASE_URL` in `backend/.env` to a PostgreSQL connection string.

Create a local application database and a separate test database, for example:

```bash
createdb triage_sentinel
createdb triage_sentinel_test
```

Set `TEST_DATABASE_URL` to the test database connection string before running
tests. Repository tests refuse a non-PostgreSQL URL or a database whose name
does not include `test`; they create and remove an isolated temporary schema.

Apply migrations from the backend directory:

```bash
cd backend
python -m alembic upgrade head
```

To roll back the current migration:

```bash
python -m alembic downgrade -1
```

## Run the applications

Start the backend from the repository root (in the activated Python
environment):

```bash
cd backend
python -m uvicorn triage_sentinel.main:app --reload
```

Start the frontend in a second terminal:

```bash
cd frontend
npm run dev
```

Open <http://localhost:3000>. The landing page requests the frontend's
same-origin `/api/health` proxy, which checks the backend's `/health` endpoint
and displays whether the connection succeeded. The backend health endpoint is
also available directly at <http://localhost:8000/health>.

Set `TRIAGE_API_URL` in `frontend/.env.local` to change the backend URL. Set
`CORS_ORIGINS` in `backend/.env` to a comma-separated list of allowed browser
origins for direct API access.

## Tests and quality checks

Run backend tests from the repository root:

```bash
export TEST_DATABASE_URL=postgresql+psycopg://localhost:5432/triage_sentinel_test
python -m pytest
```

Run frontend linting, type-checking, and production build from `frontend`:

```bash
npm run lint
npm run typecheck
npm run build
```

Check formatting for supported files from the repository root with:

```bash
./frontend/node_modules/.bin/prettier --check .
```

## Optional backend dependency groups

Install only the integrations needed by later stages:

```bash
python -m pip install -e "backend[database]"       # Psycopg PostgreSQL driver
python -m pip install -e "backend[supabase]"       # Supabase Python client
python -m pip install -e "backend[workflow]"      # LangGraph workflow
python -m pip install -e "backend[mcp]"           # Python MCP SDK
```

The workflow API calls typed MCP tools over an in-process MCP SDK transport;
it does not expose database access to the model. It persists explicit states
and outcomes, pauses for clinician approval, and uses the protected,
idempotent MCP queue commit. No authentication middleware is bundled, so the
approval route fails closed until trusted middleware populates
`request.state.authenticated_clinician`. See [docs/WORKFLOW.md](./docs/WORKFLOW.md)
for configuration, execution details, hardware findings, test results, and
remaining limitations.

## Stage 5 LangGraph workflow

The persisted FastAPI/LangGraph workflow now calls the deterministic evaluator
and MCP tools, stops with a pending clinician review, and only commits a queue
update after an authenticated clinician resumes the workflow. It uses a
locally hosted Ollama model for a schema-validated acknowledgement; tool
results remain authoritative. See [docs/WORKFLOW.md](./docs/WORKFLOW.md) for
the actual execution path, configuration, API routes, hardware check,
test results, and limitations. Ollama was not installed or reachable on the
checked development machine, so real local-model inference remains
unverified.

## Clinical protocol and evaluator

The project-selected reference is the WHO/ICRC/MSF routine Interagency
Integrated Triage Tool (IITT), for facility-based emergency-unit triage:
adult poster for age 12 and above, pediatric poster for age under 12. The
implementation covers only a documented subset of high-risk vital-sign
criteria and two explicit red criteria. It does not infer green from absent
findings, does not diagnose or prescribe, and never modifies the live queue.

See [docs/clinical-protocols/WHO-IITT.md](./docs/clinical-protocols/WHO-IITT.md)
for source URLs, version metadata, licensing limitations, exact encoded
rules, and unsupported criteria. [docs/EVALS.md](./docs/EVALS.md) records
source-derived synthetic test expectations, actual test results, and
limitations. This is a competition prototype, not a Kenyan Ministry of
Health-approved protocol or a clinically validated/approved medical device.

## Repository layout

- `frontend/` — Next.js application
- `backend/` — FastAPI application and Python project configuration
- `docs/` — architecture and stage scope
- `tests/` — backend API tests
