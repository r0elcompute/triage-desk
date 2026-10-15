import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID, uuid4

import httpx
from langgraph.graph import END, START, StateGraph
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from triage_sentinel.data.enums import Measurement
from triage_sentinel.reference_mcp import (
    FILESYSTEM_SERVER_ID,
    ReferenceRetrievalAudit,
)
from triage_tools_mcp.schemas import (
    AuthenticatedClinician,
    CommitApprovedQueueUpdateOutput,
    EvaluateTriageOutput,
    ProtocolReferenceInput,
    RequestClinicianReviewOutput,
    ToolError,
    ValidateVitalsOutput,
)


class WorkflowObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement: str | None = None
    value: int | float | str | None = None
    unit: str | None = None
    measured_at: datetime | str | None = None
    source: str | None = None
    source_reference: str | None = None

    @field_validator("source_reference")
    @classmethod
    def validate_synthetic_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("SYN-"):
            raise ValueError("Only synthetic source references are accepted")
        return value


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encounter_id: UUID
    assessment_version_id: UUID
    protocol_id: str = Field(min_length=1, max_length=128)
    observations: list[WorkflowObservation]
    required_measurements: list[Measurement] = Field(min_length=1)


class RecommendationDraft(BaseModel):
    """Structured, non-authoritative rendering of deterministic tool output."""

    model_config = ConfigDict(extra="forbid")

    workflow_status: Literal["pending_clinician_review"]
    protocol_version: str
    finding_codes: list[str]
    supporting_evidence: list[str]
    summary: str


class ToolCaller(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class ProtocolReferenceClient(Protocol):
    async def retrieve(self, document_id: str) -> Any: ...


class StructuredModel(Protocol):
    model_id: str

    async def generate(
        self, evaluation: EvaluateTriageOutput
    ) -> RecommendationDraft: ...


class OllamaStructuredModel:
    def __init__(
        self,
        *,
        model_id: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 90,
    ) -> None:
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def generate(self, evaluation: EvaluateTriageOutput) -> RecommendationDraft:
        expected = _expected_draft(evaluation)
        payload = {
            "model": self.model_id,
            "stream": False,
            "format": RecommendationDraft.model_json_schema(),
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only the supplied deterministic evaluation values "
                        "in the requested JSON schema. Do not add clinical facts, "
                        "thresholds, findings, evidence, or actions. The summary "
                        "must be exactly the provided summary."
                    ),
                },
                {"role": "user", "content": expected.model_dump_json()},
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
        content = response.json()["message"]["content"]
        return RecommendationDraft.model_validate_json(content)


class StdioMCPToolCaller:
    def __init__(
        self,
        *,
        command: str = "triage-tools-mcp",
        cwd: str | Path | None = None,
        timeout_seconds: float = 90,
        clinician: AuthenticatedClinician | None = None,
    ) -> None:
        self.command = command
        self.cwd = str(cwd) if cwd else None
        self.timeout_seconds = timeout_seconds
        self.clinician = clinician

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        environment = os.environ.copy()
        if name == "commit_approved_queue_update" and self.clinician is not None:
            environment["TRIAGE_MCP_PRINCIPAL_PROVIDER"] = (
                "triage_sentinel.workflow:principal_from_environment"
            )
            environment["TRIAGE_WORKFLOW_PRINCIPAL_JSON"] = (
                self.clinician.model_dump_json()
            )
        params = StdioServerParameters(
            command=self.command,
            cwd=self.cwd,
            env=environment,
        )
        async with asyncio.timeout(self.timeout_seconds):
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        name,
                        {"request": arguments},
                        read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                    )
        if result.structuredContent is not None:
            payload = result.structuredContent
        else:
            text_blocks = [
                block.text
                for block in result.content
                if getattr(block, "text", None)
            ]
            if len(text_blocks) != 1:
                raise RuntimeError(f"MCP tool {name} returned no structured result")
            try:
                payload = json.loads(text_blocks[0])
            except json.JSONDecodeError as error:
                if result.isError:
                    raise RuntimeError(
                        f"MCP tool {name} returned an execution error"
                    ) from error
                raise RuntimeError(
                    f"MCP tool {name} returned a non-JSON result"
                ) from error
        if result.isError and not isinstance(payload, Mapping):
            raise RuntimeError(f"MCP tool {name} returned an execution error")
        return payload


class WorkflowState(TypedDict, total=False):
    workflow_id: str
    request: dict[str, Any]
    status: str
    transitions: list[dict[str, str]]
    validation: dict[str, Any]
    evaluation: dict[str, Any]
    recommendation: dict[str, Any] | None
    review_request: dict[str, Any]
    protocol_reference: dict[str, Any]
    protocol_reference_metadata: dict[str, Any]
    reference_retrieval: dict[str, Any]
    error_code: str | None
    model_id: str
    started_at: str
    encounter_id: str
    assessment_version_id: str


class TriageWorkflow:
    def __init__(
        self,
        tools: ToolCaller,
        model: StructuredModel,
        *,
        reference_client: ProtocolReferenceClient | None = None,
        timeout_seconds: float = 90,
    ) -> None:
        self.tools = tools
        self.model = model
        self.reference_client = reference_client
        self.timeout_seconds = timeout_seconds
        builder = StateGraph(WorkflowState)
        builder.add_node("validate", self._validate)
        builder.add_node("retrieve_reference", self._retrieve_reference)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("draft", self._draft)
        builder.add_node("request_review", self._request_review)
        builder.add_edge(START, "validate")
        builder.add_conditional_edges(
            "validate",
            self._after_validation,
            {
                "retrieve_reference": "retrieve_reference",
                "request_review": "request_review",
            },
        )
        builder.add_conditional_edges(
            "retrieve_reference",
            self._after_reference,
            {"evaluate": "evaluate", "request_review": "request_review"},
        )
        builder.add_conditional_edges(
            "evaluate",
            self._after_evaluation,
            {"draft": "draft", "request_review": "request_review"},
        )
        builder.add_edge("draft", "request_review")
        builder.add_edge("request_review", END)
        self.graph = builder.compile()

    async def run(self, request: WorkflowRequest) -> WorkflowState:
        return await self.graph.ainvoke(
            {
                "workflow_id": str(uuid4()),
                "request": request.model_dump(mode="json"),
                "status": "received",
                "transitions": [],
                "recommendation": None,
                "model_id": self.model.model_id,
                "started_at": datetime.now(UTC).isoformat(),
            }
        )

    async def commit_approved(
        self,
        state: WorkflowState,
        *,
        clinician: AuthenticatedClinician,
        approval_id: UUID,
        assessment_number: int,
        queue_name: str,
        idempotency_key: str,
    ) -> WorkflowState:
        validation = state.get("validation", {})
        evaluation = state.get("evaluation")
        review_request = state.get("review_request")
        if (
            state.get("status") != "pending_review"
            or not validation.get("valid")
            or not validation.get("complete")
            or not evaluation
            or not state.get("recommendation")
            or state.get("error_code")
            or review_request is None
            or state.get("reference_retrieval", {}).get("status") != "succeeded"
        ):
            raise ValueError("Workflow is not eligible for an approved queue commit")
        if "triage:queue:commit" not in clinician.permissions:
            raise PermissionError("Clinician cannot commit a queue update")

        request_data = state.get("request")
        if request_data is not None:
            assessment_version_id = WorkflowRequest.model_validate(
                request_data
            ).assessment_version_id
        else:
            assessment_version_id = UUID(state["assessment_version_id"])
        evaluation_result = EvaluateTriageOutput.model_validate(evaluation)
        recommendation = RecommendationDraft.model_validate(state["recommendation"])
        review_result = RequestClinicianReviewOutput.model_validate(review_request)
        reference = state.get("protocol_reference") or state.get(
            "protocol_reference_metadata", {}
        )
        if (
            evaluation_result.assessment_version_id != assessment_version_id
            or review_result.assessment_version_id != assessment_version_id
            or review_result.status != "pending"
        ):
            raise ValueError("Workflow artifacts do not match the assessment")
        if (
            reference.get("approval_status") != "approved"
            or reference.get("protocol_id")
            != state["reference_retrieval"].get("requested_document_id")
            or evaluation_result.protocol_version != reference.get("version")
            or state["reference_retrieval"].get("returned_version")
            != reference.get("version")
            or state["reference_retrieval"].get("content_sha256")
            != reference.get("content_sha256")
            or state["reference_retrieval"].get("server_identity")
            != FILESYSTEM_SERVER_ID
            or state["reference_retrieval"].get("tool_name") != "read_text_file"
        ):
            raise ValueError("Workflow artifacts do not match the approved protocol")
        if state.get("protocol_reference"):
            ProtocolReferenceInput.model_validate(state["protocol_reference"])
        _validate_draft_matches_evaluation(recommendation, evaluation_result)
        result = await self._call_tool(
            "commit_approved_queue_update",
            {
                "assessment_version_id": str(assessment_version_id),
                "assessment_number": assessment_number,
                "approval_id": str(approval_id),
                "queue_name": queue_name,
                "idempotency_key": idempotency_key,
            },
        )
        committed = _as_tool_result(result, CommitApprovedQueueUpdateOutput)
        next_state = _transition(state, "queue_commit_accepted")
        next_state["status"] = "committed"
        next_state["queue_commit"] = committed.model_dump(mode="json")
        next_state["approved_assessment_version_id"] = str(assessment_version_id)
        return next_state

    async def _validate(self, state: WorkflowState) -> dict[str, Any]:
        request = WorkflowRequest.model_validate(state["request"])
        next_state = _transition(state, "validating")
        try:
            result = await self._call_tool(
                "validate_vitals",
                {
                    "encounter_id": str(request.encounter_id),
                    "observations": [
                        observation.model_dump(mode="json")
                        for observation in request.observations
                    ],
                    "required_measurements": [
                        item.value for item in request.required_measurements
                    ],
                },
            )
            validation = _as_tool_result(result, ValidateVitalsOutput)
            next_state["validation"] = validation.model_dump(mode="json")
            if validation.valid and validation.complete:
                next_state = _transition(next_state, "validated")
            else:
                next_state = _transition(next_state, "needs_review")
            return next_state
        except Exception as error:
            next_state["error_code"] = _error_code(error)
            return _transition(next_state, "validation_failed")

    @staticmethod
    def _after_validation(state: WorkflowState) -> str:
        validation = state.get("validation")
        if validation and validation.get("valid") and validation.get("complete"):
            return "retrieve_reference"
        return "request_review"

    async def _retrieve_reference(self, state: WorkflowState) -> dict[str, Any]:
        next_state = _transition(state, "retrieving_protocol_reference")
        if self.reference_client is None:
            audit = ReferenceRetrievalAudit(
                requested_document_id=state["request"]["protocol_id"],
                status="failed",
                error_code="FILESYSTEM_MCP_UNCONFIGURED",
            )
            next_state["reference_retrieval"] = audit.model_dump(mode="json")
            next_state["error_code"] = audit.error_code
            return _transition(next_state, "protocol_reference_unavailable")
        try:
            result = await self.reference_client.retrieve(
                state["request"]["protocol_id"]
            )
            next_state["reference_retrieval"] = result.audit.model_dump(mode="json")
            if result.document is None:
                next_state["error_code"] = result.audit.error_code
                return _transition(next_state, "protocol_reference_unavailable")
            reference = ProtocolReferenceInput.model_validate(result.document)
            next_state["protocol_reference"] = reference.model_dump(mode="json")
            next_state["protocol_reference_metadata"] = _reference_metadata(reference)
            return _transition(next_state, "protocol_reference_verified")
        except Exception:
            audit = ReferenceRetrievalAudit(
                requested_document_id=state["request"]["protocol_id"],
                status="failed",
                error_code="PROTOCOL_REFERENCE_INVALID",
            )
            next_state["reference_retrieval"] = audit.model_dump(mode="json")
            next_state["error_code"] = audit.error_code
            return _transition(next_state, "protocol_reference_rejected")

    @staticmethod
    def _after_reference(state: WorkflowState) -> str:
        reference = state.get("protocol_reference")
        audit = state.get("reference_retrieval", {})
        if reference and audit.get("status") == "succeeded":
            return "evaluate"
        return "request_review"

    async def _evaluate(self, state: WorkflowState) -> dict[str, Any]:
        request = WorkflowRequest.model_validate(state["request"])
        next_state = _transition(state, "evaluating")
        try:
            protocol_reference = ProtocolReferenceInput.model_validate(
                state["protocol_reference"]
            )
            result = await self._call_tool(
                "evaluate_triage_rules",
                {
                    "encounter_id": str(request.encounter_id),
                    "assessment_version_id": str(request.assessment_version_id),
                    "observations": [
                        observation.model_dump(mode="json")
                        for observation in request.observations
                    ],
                    "protocol_reference": protocol_reference.model_dump(mode="json"),
                },
            )
            evaluation = _as_tool_result(result, EvaluateTriageOutput)
            if evaluation.protocol_version != protocol_reference.version:
                raise ValueError("Evaluator returned a different protocol version")
            next_state["evaluation"] = evaluation.model_dump(mode="json")
            return _transition(next_state, "evaluated")
        except Exception as error:
            next_state["error_code"] = _error_code(error)
            return _transition(next_state, "evaluation_failed")

    @staticmethod
    def _after_evaluation(state: WorkflowState) -> str:
        if state.get("evaluation"):
            return "draft"
        return "request_review"

    async def _draft(self, state: WorkflowState) -> dict[str, Any]:
        next_state = _transition(state, "generating_recommendation")
        try:
            evaluation = EvaluateTriageOutput.model_validate(state["evaluation"])
            async with asyncio.timeout(self.timeout_seconds):
                draft = await self.model.generate(evaluation)
            _validate_draft_matches_evaluation(draft, evaluation)
            next_state["recommendation"] = draft.model_dump(mode="json")
            return _transition(next_state, "recommendation_validated")
        except (TimeoutError, httpx.HTTPError):
            next_state["error_code"] = "MODEL_UNAVAILABLE"
        except (ValidationError, ValueError, KeyError, TypeError):
            next_state["error_code"] = "MODEL_OUTPUT_REJECTED"
        except Exception:
            next_state["error_code"] = "MODEL_EXECUTION_FAILED"
        next_state["recommendation"] = None
        return _transition(next_state, "recommendation_failed")

    async def _request_review(self, state: WorkflowState) -> dict[str, Any]:
        request = WorkflowRequest.model_validate(state["request"])
        next_state = _transition(state, "requesting_clinician_review")
        reason = _review_reason(next_state)
        try:
            result = await self._call_tool(
                "request_clinician_review",
                {
                    "assessment_version_id": str(request.assessment_version_id),
                    "reason": reason,
                },
            )
            review_request = _as_tool_result(result, RequestClinicianReviewOutput)
            next_state["review_request"] = review_request.model_dump(mode="json")
            next_state["status"] = "pending_review"
            return _transition(next_state, "pending_clinician_review")
        except Exception as error:
            next_state["error_code"] = _error_code(error)
            next_state["status"] = "failed"
            return _transition(next_state, "workflow_failed")

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with asyncio.timeout(self.timeout_seconds):
            return await self.tools.call_tool(name, arguments)


def _transition(state: WorkflowState, name: str) -> WorkflowState:
    transitions = [*state.get("transitions", [])]
    transitions.append({"state": name, "at": datetime.now(UTC).isoformat()})
    return {**state, "transitions": transitions}


def _as_tool_result(value: Any, schema: type[BaseModel]) -> Any:
    if isinstance(value, ToolError):
        raise RuntimeError(value.code)
    if isinstance(value, Mapping):
        if "structuredContent" in value:
            value = value["structuredContent"]
        if isinstance(value, Mapping) and "result" in value:
            value = value["result"]
    elif hasattr(value, "structuredContent"):
        value = value.structuredContent
    if isinstance(value, Mapping) and {"code", "message", "action_id"} <= value.keys():
        raise RuntimeError(str(value["code"]))
    return schema.model_validate(value)


def _expected_draft(evaluation: EvaluateTriageOutput) -> RecommendationDraft:
    return RecommendationDraft(
        workflow_status="pending_clinician_review",
        protocol_version=evaluation.protocol_version,
        finding_codes=[finding.code for finding in evaluation.findings],
        supporting_evidence=evaluation.supporting_evidence,
        summary=(
            f"Deterministic evaluation returned {len(evaluation.findings)} finding(s); "
            "clinician review is required."
        ),
    )


def _validate_draft_matches_evaluation(
    draft: RecommendationDraft, evaluation: EvaluateTriageOutput
) -> None:
    expected = _expected_draft(evaluation)
    if draft != expected:
        raise ValueError("Model output did not exactly match deterministic findings")


def _review_reason(state: WorkflowState) -> str:
    if state.get("error_code"):
        return (
            "Automated workflow could not complete all configured steps; "
            "clinician review is required."
        )
    validation = state.get("validation")
    if validation and (not validation.get("valid") or not validation.get("complete")):
        return (
            "Supplied observations are invalid or incomplete; "
            "clinician review is required."
        )
    return "Automated evaluation is a draft and requires clinician review."


def _error_code(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "TOOL_TIMEOUT"
    message = str(error)
    if message and message.replace("_", "").isalnum() and len(message) <= 64:
        return message
    return "TOOL_EXECUTION_FAILED"


def principal_from_environment(_context: object) -> AuthenticatedClinician:
    """Read identity injected by the authenticated backend into its stdio child."""
    value = os.environ.get("TRIAGE_WORKFLOW_PRINCIPAL_JSON")
    if not value:
        raise RuntimeError("No authenticated workflow principal was provided")
    return AuthenticatedClinician.model_validate_json(value)


def persist_workflow_execution(
    session_factory: Any,
    request: WorkflowRequest,
    state: WorkflowState,
) -> None:
    from triage_sentinel.data.repositories import WorkflowExecutionRepository

    validation = state.get("validation", {})
    evaluation = state.get("evaluation") or {}
    review_request = state.get("review_request") or {}
    digest_source = {
        key: state.get(key)
        for key in (
            "workflow_id",
            "model_id",
            "status",
            "transitions",
            "validation",
            "evaluation",
            "recommendation",
            "review_request",
            "reference_retrieval",
            "protocol_reference_metadata",
            "error_code",
        )
    }
    input_digest = hashlib.sha256(
        request.model_dump_json().encode("utf-8")
    ).hexdigest()
    result_digest = hashlib.sha256(
        json.dumps(digest_source, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    started_at = datetime.fromisoformat(state["started_at"])
    completed_at = datetime.now(UTC)
    with session_factory.begin() as session:
        WorkflowExecutionRepository(session).add(
            id=UUID(state["workflow_id"]),
            encounter_id=request.encounter_id,
            assessment_version_id=request.assessment_version_id,
            model_identifier=state["model_id"],
            status=state.get("status", "failed"),
            started_at=started_at,
            completed_at=completed_at,
            input_digest=input_digest,
            result_digest=result_digest,
            transitions=state.get("transitions", []),
            reference_retrieval=state.get(
                "reference_retrieval",
                ReferenceRetrievalAudit(
                    requested_document_id=request.protocol_id,
                    status="failed",
                    error_code="FILESYSTEM_MCP_NOT_RUN",
                ).model_dump(mode="json"),
            ),
            protocol_reference=state.get("protocol_reference_metadata"),
            validation_result=validation,
            validation_valid=bool(validation.get("valid")),
            validation_complete=bool(validation.get("complete")),
            evaluation_result=evaluation or None,
            evaluation_action_id=(
                UUID(evaluation["action_id"]) if evaluation.get("action_id") else None
            ),
            recommendation_result=state.get("recommendation"),
            recommendation_valid=state.get("recommendation") is not None,
            review_request_result=state.get("review_request"),
            review_request_id=(
                UUID(review_request["request_id"])
                if review_request.get("request_id")
                else None
            ),
            error_code=state.get("error_code"),
        )
        from triage_sentinel.data.enums import AuditEntityType
        from triage_sentinel.data.repositories import EventRepository
        from triage_sentinel.data.schemas import AuditLogCreate

        retrieval = state.get("reference_retrieval", {})
        EventRepository(session).add_audit_log(
            AuditLogCreate(
                entity_type=AuditEntityType.WORKFLOW_EXECUTION,
                entity_id=UUID(state["workflow_id"]),
                action=(
                    "mcp.filesystem.read_text_file.succeeded"
                    if retrieval.get("status") == "succeeded"
                    else "mcp.filesystem.read_text_file.failed"
                ),
                actor_reference="SYN-WORKFLOW-SERVICE",
                occurred_at=datetime.fromisoformat(
                    retrieval.get("occurred_at", completed_at.isoformat())
                ),
            )
        )


def _reference_metadata(reference: ProtocolReferenceInput) -> dict[str, Any]:
    return reference.model_dump(exclude={"content"})

