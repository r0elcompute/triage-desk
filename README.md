# Triage Sentinel

Triage Sentinel is the foundation for a healthcare triage MVP. This repository
contains a Next.js frontend, a FastAPI backend, and the Stage 4
`triage-tools-mcp` server. The frontend health check remains a connectivity
check; the repository does not provide medical advice or ship a clinical
evaluator.

## Prerequisites

- Node.js 20.19 or newer and npm
- Python 3.11 or newer
- PostgreSQL 15 or newer

PostgreSQL is required for the Stage 2 data layer and repository tests.
Supabase and orchestration remain optional integrations. See
[docs/README.md](./docs/README.md) for MCP setup, tool details, and required
evaluator and identity-provider configuration.

## Setup

From the repository root, create and activate a Python virtual environment and
install the backend and development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "backend[dev,database,mcp]"
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
python -m pip install -e "backend[orchestration]" # LangGraph
python -m pip install -e "backend[mcp]"           # Python MCP SDK
```

The persistence repositories are deliberately not connected to API routes.
No clinical evaluator or authenticated principal provider is bundled. The MCP
triage evaluator and consequential queue tool fail closed until configured.

## Repository layout

- `frontend/` — Next.js application
- `backend/` — FastAPI application and Python project configuration
- `docs/` — architecture and stage scope
- `tests/` — backend API tests
