import importlib
import logging
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic_settings import BaseSettings, SettingsConfigDict

from triage_sentinel.data.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from triage_tools_mcp.schemas import (
    AuthenticatedClinician,
    CommitApprovedQueueUpdateInput,
    CommitApprovedQueueUpdateOutput,
    EvaluateTriageInput,
    EvaluateTriageOutput,
    RequestClinicianReviewInput,
    RequestClinicianReviewOutput,
    ToolError,
    ValidateVitalsInput,
    ValidateVitalsOutput,
)
from triage_tools_mcp.service import (
    ClinicalEvaluator,
    PrincipalProvider,
    TriageToolsService,
)

LOGGER = logging.getLogger(__name__)


class MCPSettings(BaseSettings):
    triage_mcp_clinical_evaluator: str | None = None
    triage_mcp_principal_provider: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def _load_configured_object(setting: str, reference: str | None) -> Any | None:
    if not reference:
        return None
    module_name, separator, object_name = reference.partition(":")
    if not separator or not module_name or not object_name:
        raise RuntimeError(f"{setting} must use 'module.path:object_name' syntax")
    configured = getattr(importlib.import_module(module_name), object_name)
    return configured() if isinstance(configured, type) else configured


def create_mcp_server(
    service: TriageToolsService,
    *,
    principal_provider: PrincipalProvider | None = None,
) -> FastMCP:
    server = FastMCP("triage-tools-mcp")

    @server.tool()
    def validate_vitals(
        request: ValidateVitalsInput,
    ) -> ValidateVitalsOutput | ToolError:
        """Validate observation structure, units, times, and completeness."""
        return service.validate_vitals(request)

    @server.tool()
    def evaluate_triage_rules(
        request: EvaluateTriageInput,
    ) -> EvaluateTriageOutput | ToolError:
        """Run the configured deterministic clinical evaluator for one assessment."""
        return service.evaluate_triage_rules(request)

    @server.tool()
    def request_clinician_review(
        request: RequestClinicianReviewInput,
    ) -> RequestClinicianReviewOutput | ToolError:
        """Create a pending review request without changing the live queue."""
        return service.request_clinician_review(request)

    @server.tool()
    def commit_approved_queue_update(
        request: CommitApprovedQueueUpdateInput,
        context: Context,
    ) -> CommitApprovedQueueUpdateOutput | ToolError:
        """Commit a queue entry using an exact, active clinician approval."""
        clinician: AuthenticatedClinician | None = None
        if principal_provider is not None:
            try:
                clinician = AuthenticatedClinician.model_validate(
                    principal_provider(context)
                )
            except Exception:
                LOGGER.exception("Could not authenticate MCP request principal")
        return service.commit_approved_queue_update(request, clinician)

    return server


def main() -> None:
    settings = DatabaseSettings()
    mcp_settings = MCPSettings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    evaluator: ClinicalEvaluator | None = _load_configured_object(
        "TRIAGE_MCP_CLINICAL_EVALUATOR",
        mcp_settings.triage_mcp_clinical_evaluator,
    )
    principal_provider: PrincipalProvider | None = _load_configured_object(
        "TRIAGE_MCP_PRINCIPAL_PROVIDER",
        mcp_settings.triage_mcp_principal_provider,
    )
    server = create_mcp_server(
        TriageToolsService(session_factory, evaluator),
        principal_provider=principal_provider,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
