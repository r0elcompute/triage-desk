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
[docs/README.md](./docs/README.md) for the selected protocol, evaluator
limitations, MCP setup, tool details, and identity-provider configuration.

## Setup

From the repository root, create and activate a Python virtual environment and
install the backend, development, MCP, and workflow dependencies:

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

Stage 5 uses a local Ollama-compatible server. Install Ollama separately, then
pull the default small model and start its server:

```bash
ollama pull qwen3:1.7b
ollama serve
```

The default model is listed by Ollama at about 1.4 GB; actual CPU inference
speed depends on the host. Configure `TRIAGE_OLLAMA_MODEL` and
`TRIAGE_OLLAMA_URL` in `backend/.env` as needed. Configure
`TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER` to a trusted backend callable that derives
an `AuthenticatedClinician` from the authenticated FastAPI request; never trust
identity or permissions supplied in workflow JSON. The clinician principal
provider and evaluator are not bundled, so workflow start and queue commits
remain disabled until trusted integrations are configured.

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

The Stage 5 endpoints are `POST /workflows/triage` to start an evaluation and
create a pending review, and `POST /workflows/{workflow_id}/approve` to resume
after independent clinician authentication and authorization. A workflow
cannot commit when input validation, deterministic evaluation, or structured
model-output validation fails. The model only formats values returned by MCP;
it cannot set clinical findings or approve an action. The implementation is a
competition prototype, not a clinically validated or approved medical device.
The selected clinical setting and protocol limitations are documented in
[docs/README.md](./docs/README.md).

## Stage 6 Filesystem MCP

Stage 6 uses the official `@modelcontextprotocol/server-filesystem` npm package
at exact version `1.0.0`, with a committed npm lockfile and immutable Node base
image. Build its isolated runtime image from the repository root:

```bash
docker build -t triage-filesystem-mcp:1.0.0 backend/filesystem-mcp
```

Set `TRIAGE_PROTOCOL_REFERENCE_DIRECTORY` to the dedicated
`docs/clinical-protocols/approved` directory; its example value assumes the
backend is started from `backend/`. The Docker child has no network, a read-only
root filesystem, dropped capabilities, and a read-only bind mount of that
directory at `/protocols`. Although the official server exposes write-capable
tools, the workflow invokes only `read_text_file` and verifies that the server
allows only `/protocols`. Running it requires Docker daemon access, which is
privileged and must be restricted to the trusted backend runtime.

Each workflow request supplies a `protocol_id`. The server reads the registry
and selected UTF-8 document through MCP, checks approval status, version, safe
relative path, and SHA-256, then passes the verified text only to the
deterministic evaluator. Document contents are not sent to the language model.
The approved registry is intentionally empty because this checkout has no
verified/authorized clinical protocol; such requests fail closed to clinician
review. The successful sample is a non-clinical test fixture only.

Build the image, then run actual external-server security and retrieval tests:

```bash
docker build -t triage-filesystem-mcp:1.0.0 backend/filesystem-mcp
TRIAGE_RUN_FILESYSTEM_MCP_INTEGRATION=1 python -m pytest -q tests/test_reference_mcp.py
```

The complete workflow test additionally requires PostgreSQL, Ollama, and the
test-only synthetic evaluator/principal. See [EVALS.md](./EVALS.md) for the
exact command and observed results, and [ARCHITECTURE.md](./ARCHITECTURE.md) for
the execution and security boundaries.

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
