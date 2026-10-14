from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from triage_sentinel.data.database import (
    create_database_engine,
    create_session_factory,
)
from triage_sentinel.workflow.errors import WorkflowFailure
from triage_sentinel.workflow.schemas import (
    WorkflowOutput,
    WorkflowStartInput,
)
from triage_tools_mcp.schemas import AuthenticatedClinician
from triage_tools_mcp.service import TriageToolsService

if TYPE_CHECKING:
    from triage_sentinel.workflow.engine import TriageWorkflow


class Settings(BaseSettings):
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    database_url: str | None = None
    triage_ollama_base_url: str = "http://127.0.0.1:11434"
    triage_ollama_model_id: str = "qwen2.5:1.5b"
    triage_ollama_timeout_seconds: float = Field(default=90, gt=0)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


settings = Settings()


def _workflow_failure(error: WorkflowFailure) -> HTTPException:
    codes_to_status = {
        "FORBIDDEN": status.HTTP_403_FORBIDDEN,
        "UNAUTHENTICATED": status.HTTP_401_UNAUTHORIZED,
        "WORKFLOW_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "MODEL_TIMEOUT": status.HTTP_503_SERVICE_UNAVAILABLE,
        "MODEL_OUTPUT_INVALID": status.HTTP_502_BAD_GATEWAY,
        "MODEL_OUTPUT_MISMATCH": status.HTTP_502_BAD_GATEWAY,
        "MCP_TOOL_TIMEOUT": status.HTTP_503_SERVICE_UNAVAILABLE,
        "MCP_TOOL_FAILED": status.HTTP_503_SERVICE_UNAVAILABLE,
        "MCP_TOOL_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    }
    return HTTPException(
        status_code=codes_to_status.get(error.code, status.HTTP_409_CONFLICT),
        detail={"code": error.code, "message": str(error)},
    )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    engine = None
    if settings.database_url:
        from triage_sentinel.workflow.engine import (
            OllamaChatModel,
            TriageWorkflow,
        )

        engine = create_database_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        application.state.triage_workflow = TriageWorkflow(
            session_factory,
            TriageToolsService(session_factory),
            OllamaChatModel(
                base_url=settings.triage_ollama_base_url,
                model_id=settings.triage_ollama_model_id,
                timeout_seconds=settings.triage_ollama_timeout_seconds,
            ),
        )
    else:
        application.state.triage_workflow = None
    try:
        yield
    finally:
        if engine is not None:
            engine.dispose()


app = FastAPI(
    title="Triage Sentinel API",
    description="API foundation for the Triage Sentinel MVP.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _get_workflow(request: Request) -> TriageWorkflow:
    workflow = getattr(request.app.state, "triage_workflow", None)
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WORKFLOW_NOT_CONFIGURED",
                "message": "Configure DATABASE_URL to enable triage workflows",
            },
        )
    return workflow


@app.post(
    "/workflows",
    response_model=WorkflowOutput,
    status_code=status.HTTP_201_CREATED,
    tags=["workflow"],
)
async def start_workflow(
    data: WorkflowStartInput,
    request: Request,
) -> WorkflowOutput:
    try:
        return await _get_workflow(request).start(data)
    except WorkflowFailure as error:
        raise _workflow_failure(error) from error


@app.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowOutput,
    tags=["workflow"],
)
async def get_workflow(
    workflow_id: UUID,
    request: Request,
) -> WorkflowOutput:
    try:
        return await _get_workflow(request).get(workflow_id)
    except WorkflowFailure as error:
        raise _workflow_failure(error) from error


@app.post(
    "/workflows/{workflow_id}/approval",
    response_model=WorkflowOutput,
    tags=["workflow"],
)
async def approve_workflow(
    workflow_id: UUID,
    request: Request,
) -> WorkflowOutput:
    trusted_principal = getattr(request.state, "authenticated_clinician", None)
    if trusted_principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHENTICATED",
                "message": "Trusted clinician authentication is required",
            },
        )
    try:
        clinician = AuthenticatedClinician.model_validate(trusted_principal)
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHENTICATED",
                "message": "Trusted clinician identity is invalid",
            },
        ) from error
    try:
        return await _get_workflow(request).resume(workflow_id, clinician)
    except WorkflowFailure as error:
        raise _workflow_failure(error) from error
