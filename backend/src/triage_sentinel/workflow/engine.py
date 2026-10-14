import asyncio
import hashlib
import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any, NotRequired, Protocol, Required, TypedDict, TypeVar
from uuid import UUID

import httpx
from langgraph.graph import END, START, StateGraph
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from triage_sentinel.data.enums import Measurement
from triage_sentinel.evaluation.schemas import (
    EvaluateTriageInput,
    EvaluateTriageOutput,
)
from triage_sentinel.workflow.errors import WorkflowFailure
from triage_sentinel.workflow.repository import (
    WorkflowRepository,
    WorkflowRepositoryError,
)
from triage_sentinel.workflow.schemas import (
    ModelAcknowledgement,
    WorkflowOutput,
    WorkflowRecommendation,
    WorkflowStartInput,
)
from triage_tools_mcp.schemas import (
    AuthenticatedClinician,
    CommitApprovedQueueUpdateInput,
    CommitApprovedQueueUpdateOutput,
    ObservationPayload,
    RequestClinicianReviewInput,
    RequestClinicianReviewOutput,
    ToolError,
    ValidateVitalsInput,
    ValidateVitalsOutput,
)
from triage_tools_mcp.server import create_mcp_server
from triage_tools_mcp.service import (
    APPROVE_PERMISSION,
    COMMIT_PERMISSION,
    ToolFailure,
    TriageToolsService,
)

LOGGER = logging.getLogger(__name__)
OutputT = TypeVar("OutputT", bound=BaseModel)
_CURRENT_CLINICIAN: ContextVar[AuthenticatedClinician | None] = ContextVar(
    "triage_workflow_clinician", default=None
)
REQUIRED_NUMERIC_MEASUREMENTS = [
    Measurement.HEART_RATE,
    Measurement.RESPIRATORY_RATE,
    Measurement.TEMPERATURE,
    Measurement.OXYGEN_SATURATION,
]


class WorkflowModel(Protocol):
    model_id: str

    async def acknowledge(
        self, result: EvaluateTriageOutput
    ) -> ModelAcknowledgement: ...


class OllamaChatModel:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model_id: str = "qwen2.5:1.5b",
        timeout_seconds: float = 90,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        url = httpx.URL(self.base_url)
        if (
            url.scheme not in {"http", "https"}
            or url.host not in {"localhost", "127.0.0.1", "::1"}
            or url.username
            or url.password
        ):
            raise ValueError("Ollama inference must use a local loopback address")

    async def acknowledge(self, result: EvaluateTriageOutput) -> ModelAcknowledgement:
        evaluator_data = result.model_dump(mode="json")
        payload = {
            "model": self.model_id,
            "stream": False,
            "format": ModelAcknowledgement.model_json_schema(),
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only the requested JSON object. Copy the "
                        "evaluator_status and matched rule IDs exactly from "
                        "the supplied deterministic evaluator output. Do not "
                        "add criteria, evidence, thresholds, diagnoses, "
                        "treatment, or a triage category. The evaluator is "
                        "authoritative."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "schema": ModelAcknowledgement.model_json_schema(),
                            "deterministic_evaluation": evaluator_data,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=5)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self._transport
            ) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
            content = body["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Ollama message content must be a JSON string")
            return ModelAcknowledgement.model_validate_json(content)
        except httpx.TimeoutException as error:
            raise WorkflowFailure("MODEL_TIMEOUT", "Local model timed out") from error
        except (
            httpx.HTTPError,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as error:
            raise WorkflowFailure(
                "MODEL_OUTPUT_INVALID",
                "Local model returned an invalid or unavailable response",
            ) from error


class WorkflowState(TypedDict, total=False):
    mode: Required[str]
    workflow_id: Required[UUID]
    model_id: Required[str]
    request: NotRequired[WorkflowStartInput]
    clinician: NotRequired[AuthenticatedClinician]
    last_node: NotRequired[str]
    error_code: NotRequired[str]
    validation_result: NotRequired[ValidateVitalsOutput]
    evaluation_result: NotRequired[EvaluateTriageOutput]
    recommendation: NotRequired[WorkflowRecommendation]
    review_request: NotRequired[RequestClinicianReviewOutput]
    commit_result: NotRequired[CommitApprovedQueueUpdateOutput]


class TriageWorkflow:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tools_service: TriageToolsService,
        model: WorkflowModel,
    ) -> None:
        self._repository = WorkflowRepository(session_factory)
        self._tools_service = tools_service
        self._model = model
        self._mcp_server = create_mcp_server(
            tools_service, principal_provider=self._principal_provider
        )
        self._graph = self._build_graph()

    async def start(self, data: WorkflowStartInput) -> WorkflowOutput:
        input_digest = self._digest(data.model_dump(mode="json"))
        try:
            execution = await asyncio.to_thread(
                self._repository.create,
                data,
                model_id=self._model.model_id,
                input_digest=input_digest,
            )
        except WorkflowRepositoryError as error:
            raise WorkflowFailure(error.code, "Could not create workflow") from error

        state: WorkflowState = {
            "mode": "start",
            "workflow_id": execution.id,
            "request": data,
            "model_id": self._model.model_id,
        }
        try:
            await self._invoke(state)
        except WorkflowFailure as error:
            await self._mark_failed(execution.id, error.code)
        except Exception as error:
            LOGGER.error(
                "Unexpected triage workflow failure (%s)", type(error).__name__
            )
            await self._mark_failed(execution.id, "WORKFLOW_INTERNAL_ERROR")
            raise WorkflowFailure(
                "WORKFLOW_INTERNAL_ERROR", "Workflow execution failed"
            ) from error
        return await self.get(execution.id)

    async def resume(
        self,
        workflow_id: UUID,
        clinician: AuthenticatedClinician,
    ) -> WorkflowOutput:
        execution = await asyncio.to_thread(self._repository.get, workflow_id)
        if execution is None:
            raise WorkflowFailure("WORKFLOW_NOT_FOUND", "Workflow was not found")
        required_permissions = {APPROVE_PERMISSION, COMMIT_PERMISSION}
        if not required_permissions.issubset(clinician.permissions):
            try:
                await asyncio.to_thread(
                    self._repository.record_event,
                    workflow_id,
                    event_type="workflow.approval.denied",
                    details={
                        "reason": "missing_required_permission",
                        "clinician_reference": clinician.reference,
                    },
                )
            except WorkflowRepositoryError as error:
                LOGGER.error("Could not audit denied approval (%s)", error.code)
            raise WorkflowFailure(
                "FORBIDDEN",
                "Clinician lacks approval or queue-commit authorization",
            )
        if execution.status == "completed":
            return await self.get(workflow_id)
        if execution.status not in {
            "awaiting_clinician_approval",
            "committing",
        }:
            raise WorkflowFailure(
                "WORKFLOW_NOT_AWAITING_APPROVAL",
                "Workflow is not awaiting an approval or commit retry",
            )
        state: WorkflowState = {
            "mode": "resume",
            "workflow_id": workflow_id,
            "clinician": clinician,
            "model_id": execution.model_id,
        }
        token = _CURRENT_CLINICIAN.set(clinician)
        try:
            await self._invoke(state)
        except WorkflowFailure as error:
            await self._mark_failed(workflow_id, error.code)
        except Exception as error:
            LOGGER.error(
                "Unexpected approval workflow failure (%s)", type(error).__name__
            )
            await self._mark_failed(workflow_id, "WORKFLOW_INTERNAL_ERROR")
            raise WorkflowFailure(
                "WORKFLOW_INTERNAL_ERROR", "Workflow approval failed"
            ) from error
        finally:
            _CURRENT_CLINICIAN.reset(token)
        return await self.get(workflow_id)

    async def get(self, workflow_id: UUID) -> WorkflowOutput:
        execution = await asyncio.to_thread(self._repository.get, workflow_id)
        if execution is None:
            raise WorkflowFailure("WORKFLOW_NOT_FOUND", "Workflow was not found")
        events = await asyncio.to_thread(self._repository.events, workflow_id)
        return WorkflowOutput.model_validate(
            {
                "workflow_id": execution.id,
                "encounter_id": execution.encounter_id,
                "assessment_version_id": execution.assessment_version_id,
                "assessment_number": execution.assessment_number,
                "status": execution.status,
                "model_id": execution.model_id,
                "protocol_id": execution.protocol_id,
                "protocol_version": execution.protocol_version,
                "input_digest": execution.input_digest,
                "validation_result": execution.validation_result,
                "evaluation_result": execution.evaluation_result,
                "recommendation": execution.recommendation,
                "review_request_id": execution.review_request_id,
                "approval_id": execution.approval_id,
                "commit_result": execution.commit_result,
                "error_code": execution.error_code,
                "events": [
                    {
                        "state": event.state,
                        "event_type": event.event_type,
                        "action_id": event.action_id,
                        "details": event.details,
                        "occurred_at": self._aware(event.occurred_at),
                    }
                    for event in events
                ],
                "created_at": self._aware(execution.created_at),
                "updated_at": self._aware(execution.updated_at),
            }
        )

    async def _invoke(self, state: WorkflowState) -> None:
        await self._graph.ainvoke(
            state,
            config={
                "configurable": {
                    "thread_id": str(state["workflow_id"]),
                }
            },
        )

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("route", self._route)
        graph.add_node("validate", self._validate)
        graph.add_node("evaluate", self._evaluate)
        graph.add_node("recommend", self._recommend)
        graph.add_node("request_review", self._request_review)
        graph.add_node("approve_and_commit", self._approve_and_commit)
        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route",
            lambda state: (
                "approve_and_commit" if state.get("mode") == "resume" else "validate"
            ),
            {
                "validate": "validate",
                "approve_and_commit": "approve_and_commit",
            },
        )
        graph.add_conditional_edges(
            "validate",
            self._next_or_end,
            {"evaluate": "evaluate", "end": END},
        )
        graph.add_conditional_edges(
            "evaluate",
            self._next_or_end,
            {"recommend": "recommend", "end": END},
        )
        graph.add_conditional_edges(
            "recommend",
            self._next_or_end,
            {"request_review": "request_review", "end": END},
        )
        graph.add_edge("request_review", END)
        graph.add_edge("approve_and_commit", END)
        return graph.compile()

    @staticmethod
    def _route(state: WorkflowState) -> dict[str, object]:
        del state
        return {}

    @staticmethod
    def _next_or_end(state: WorkflowState) -> str:
        return (
            "end"
            if state.get("error_code")
            else {
                "validate": "evaluate",
                "evaluate": "recommend",
                "recommend": "request_review",
            }.get(state.get("last_node", ""), "end")
        )

    async def _validate(self, state: WorkflowState) -> dict[str, object]:
        workflow_id = state["workflow_id"]
        await self._transition(
            workflow_id,
            expected="received",
            status="validating",
            event_type="workflow.validation.started",
        )
        request = state.get("request")
        if request is None:
            raise WorkflowFailure(
                "WORKFLOW_INPUT_UNAVAILABLE", "Workflow input is missing"
            )
        numeric_observations = [
            ObservationPayload.model_validate(
                item.model_dump(mode="json", exclude={"observation_id"})
            )
            for item in request.observations
            if item.measurement.value
            in {measurement.value for measurement in REQUIRED_NUMERIC_MEASUREMENTS}
        ]
        validation_input = ValidateVitalsInput(
            encounter_id=request.encounter_id,
            observations=numeric_observations,
            required_measurements=REQUIRED_NUMERIC_MEASUREMENTS,
            validated_at=request.evaluated_at,
        )
        result = await self._call_tool(
            "validate_vitals", validation_input, ValidateVitalsOutput
        )
        await self._transition(
            workflow_id,
            expected="validating",
            status="evaluating",
            event_type="workflow.validation.completed",
            action_id=result.action_id,
            details={
                "valid": result.valid,
                "complete": result.complete,
                "missing_measurements": [
                    measurement.value for measurement in result.missing_measurements
                ],
            },
            fields={"validation_result": result.model_dump(mode="json")},
        )
        if not result.valid:
            await self._mark_failed(workflow_id, "VITALS_INVALID")
            return {"error_code": "VITALS_INVALID"}
        return {"validation_result": result, "last_node": "validate"}

    async def _evaluate(self, state: WorkflowState) -> dict[str, object]:
        workflow_id = state["workflow_id"]
        request = state.get("request")
        if request is None:
            raise WorkflowFailure(
                "WORKFLOW_INPUT_UNAVAILABLE", "Workflow input is missing"
            )
        evaluation_input = EvaluateTriageInput.model_validate(
            request.model_dump(mode="json", exclude={"queue_name"})
        )
        result = await self._call_tool(
            "evaluate_triage_rules",
            evaluation_input,
            EvaluateTriageOutput,
        )
        await self._transition(
            workflow_id,
            expected="evaluating",
            status="generating_recommendation",
            event_type="workflow.evaluation.completed",
            action_id=result.action_id,
            details={
                "protocol_id": result.protocol_id,
                "protocol_version": result.protocol_version,
                "finding_count": len(result.findings),
                "evaluation_digest": self._digest(result.model_dump(mode="json")),
            },
            fields={
                "evaluation_result": result.model_dump(mode="json"),
                "protocol_id": result.protocol_id,
                "protocol_version": result.protocol_version,
            },
        )
        return {"evaluation_result": result, "last_node": "evaluate"}

    async def _recommend(self, state: WorkflowState) -> dict[str, object]:
        result = state.get("evaluation_result")
        if result is None:
            raise WorkflowFailure(
                "EVALUATION_UNAVAILABLE", "Deterministic evaluation is missing"
            )
        acknowledgement = await self._model.acknowledge(result)
        matched_rule_ids = sorted(
            finding.rule_id
            for finding in result.findings
            if finding.rule_state == "met"
        )
        if (
            acknowledgement.evaluator_status != result.status
            or acknowledgement.matched_rule_ids != matched_rule_ids
        ):
            raise WorkflowFailure(
                "MODEL_OUTPUT_MISMATCH",
                "Model response did not exactly reflect deterministic findings",
            )
        summaries = {
            "red_escalation": (
                "The deterministic evaluator returned a red escalation finding. "
                "Immediate clinician attention is required."
            ),
            "clinician_review_required": (
                "The deterministic evaluator identified high-risk findings "
                "requiring clinician review."
            ),
            "indeterminate": (
                "The deterministic evaluator could not establish a complete "
                "supported category. Clinician assessment is required; no "
                "low-acuity conclusion has been made."
            ),
            "evaluated": (
                "The deterministic evaluator returned an assessment for "
                "clinician review."
            ),
        }
        recommendation = WorkflowRecommendation(
            evaluator_status=result.status,
            triage_category=result.triage_category,
            matched_rule_ids=matched_rule_ids,
            supporting_evidence=result.supporting_evidence,
            missing_information=result.missing_information,
            evaluation_uncertainties=result.evaluation_uncertainties,
            unsupported_criteria=result.unsupported_criteria,
            summary=summaries[result.status],
        )
        workflow_id = state["workflow_id"]
        await self._transition(
            workflow_id,
            expected="generating_recommendation",
            status="requesting_review",
            event_type="workflow.recommendation.validated",
            details={
                "model_id": self._model.model_id,
                "model_output_digest": self._digest(
                    acknowledgement.model_dump(mode="json")
                ),
                "finding_rule_ids": matched_rule_ids,
            },
            fields={"recommendation": recommendation.model_dump(mode="json")},
        )
        return {"recommendation": recommendation, "last_node": "recommend"}

    async def _request_review(self, state: WorkflowState) -> dict[str, object]:
        workflow_id = state["workflow_id"]
        workflow_request = state.get("request")
        if workflow_request is None:
            raise WorkflowFailure(
                "WORKFLOW_INPUT_UNAVAILABLE", "Workflow input is missing"
            )
        request = RequestClinicianReviewInput(
            assessment_version_id=workflow_request.assessment_version_id,
            reason=(
                "Deterministic triage evaluation requires clinician review; "
                "workflow will not change the live queue."
            ),
        )
        result = await self._call_tool(
            "request_clinician_review", request, RequestClinicianReviewOutput
        )
        if result.status != "pending":
            raise WorkflowFailure(
                "REVIEW_REQUEST_INVALID",
                "MCP server did not create a pending review request",
            )
        await self._transition(
            workflow_id,
            expected="requesting_review",
            status="awaiting_clinician_approval",
            event_type="workflow.review.pending",
            action_id=result.action_id,
            details={"review_request_id": str(result.request_id)},
            fields={"review_request_id": result.request_id},
        )
        return {"review_request": result}

    async def _approve_and_commit(self, state: WorkflowState) -> dict[str, object]:
        workflow_id = state["workflow_id"]
        clinician = state.get("clinician")
        if clinician is None:
            raise WorkflowFailure(
                "UNAUTHENTICATED", "Authenticated clinician is missing"
            )
        execution = await asyncio.to_thread(self._repository.get, workflow_id)
        if execution is None:
            raise WorkflowFailure("WORKFLOW_NOT_FOUND", "Workflow was not found")
        if execution.status == "awaiting_clinician_approval":
            await self._transition(
                workflow_id,
                expected="awaiting_clinician_approval",
                status="committing",
                event_type="workflow.clinician_approval.started",
                details={"clinician_reference": clinician.reference},
            )
            execution = await asyncio.to_thread(self._repository.get, workflow_id)
            if execution is None:
                raise WorkflowFailure("WORKFLOW_NOT_FOUND", "Workflow was not found")

        review_request_id = execution.review_request_id
        if review_request_id is None:
            raise WorkflowFailure(
                "REVIEW_REQUEST_NOT_FOUND",
                "No pending review request is associated with this workflow",
            )
        approval = await asyncio.to_thread(
            self._repository.approval_for_request,
            review_request_id,
        )
        if approval is None:
            try:
                approval_id = await asyncio.to_thread(
                    self._tools_service.approve_review_request,
                    review_request_id,
                    clinician,
                )
            except ToolFailure as error:
                if error.code != "ALREADY_APPROVED":
                    raise WorkflowFailure(error.code, str(error)) from error
                approval = await asyncio.to_thread(
                    self._repository.approval_for_request,
                    review_request_id,
                )
                if approval is None:
                    raise WorkflowFailure(
                        "APPROVAL_UNAVAILABLE",
                        "Existing approval could not be recovered",
                    ) from error
                approval_id = approval.id
        else:
            approval_id = approval.id
        approval = await asyncio.to_thread(
            self._repository.approval_for_request,
            review_request_id,
        )
        if (
            approval is None
            or approval.id != approval_id
            or approval.clinician_subject != clinician.subject
            or approval.clinician_reference != clinician.reference
        ):
            raise WorkflowFailure(
                "APPROVAL_CLINICIAN_MISMATCH",
                "The assessment approval belongs to a different clinician",
            )
        await self._record_approval(workflow_id, approval_id, clinician.reference)

        commit_request = CommitApprovedQueueUpdateInput(
            assessment_version_id=execution.assessment_version_id,
            assessment_number=execution.assessment_number,
            approval_id=approval_id,
            queue_name=execution.queue_name,
            idempotency_key=f"workflow-{workflow_id.hex}",
        )
        result = await self._call_tool(
            "commit_approved_queue_update",
            commit_request,
            CommitApprovedQueueUpdateOutput,
        )
        await self._transition(
            workflow_id,
            expected="committing",
            status="completed",
            event_type="workflow.queue_commit.completed",
            action_id=result.action_id,
            details={"commit_status": result.status},
            fields={"commit_result": result.model_dump(mode="json")},
        )
        return {"commit_result": result}

    async def _call_tool(
        self,
        name: str,
        request: BaseModel,
        output_type: type[OutputT],
    ) -> OutputT:
        try:
            async with create_connected_server_and_client_session(
                self._mcp_server,
                read_timeout_seconds=timedelta(seconds=120),
            ) as client:
                await client.initialize()
                response = await client.call_tool(
                    name, {"request": request.model_dump(mode="json")}
                )
        except TimeoutError as error:
            raise WorkflowFailure(
                "MCP_TOOL_TIMEOUT", f"MCP tool {name} timed out"
            ) from error
        except Exception as error:
            LOGGER.error("MCP tool %s was unavailable (%s)", name, type(error).__name__)
            raise WorkflowFailure(
                "MCP_TOOL_UNAVAILABLE", f"MCP tool {name} is unavailable"
            ) from error
        if response.isError:
            raise WorkflowFailure(
                "MCP_TOOL_FAILED", f"MCP tool {name} returned an error"
            )
        payload: object | None = response.structuredContent
        if payload is None:
            text_blocks = [
                item.text for item in response.content if isinstance(item, TextContent)
            ]
            if text_blocks:
                try:
                    payload = json.loads(text_blocks[0])
                except json.JSONDecodeError as error:
                    LOGGER.error("MCP tool %s returned malformed JSON content", name)
                    raise WorkflowFailure(
                        "MCP_OUTPUT_INVALID", f"MCP tool {name} returned invalid JSON"
                    ) from error
        if isinstance(payload, dict) and set(payload) == {"result"}:
            payload = payload["result"]
        if payload is not None:
            tool_error = self._maybe_tool_error(payload)
            if tool_error is not None:
                raise WorkflowFailure(
                    tool_error.code, f"MCP tool {name} returned an error"
                )
        if payload is None:
            raise WorkflowFailure(
                "MCP_TOOL_FAILED", f"MCP tool {name} failed without a valid result"
            )
        try:
            return output_type.model_validate(payload)
        except ValidationError as error:
            issue_shapes = [
                {"location": item["loc"], "type": item["type"]}
                for item in error.errors(include_input=False)
            ]
            LOGGER.error(
                "MCP tool %s returned invalid output for %s: %s",
                name,
                output_type.__name__,
                issue_shapes,
            )
            raise WorkflowFailure(
                "MCP_OUTPUT_INVALID", f"MCP tool {name} returned an invalid result"
            ) from error

    async def _record_approval(
        self, workflow_id: UUID, approval_id: UUID, clinician_reference: str
    ) -> None:
        try:
            await asyncio.to_thread(
                self._repository.record_approval,
                workflow_id,
                approval_id,
                clinician_reference,
            )
        except WorkflowRepositoryError as error:
            raise WorkflowFailure(
                error.code, "Could not record the verified approval"
            ) from error

    async def _transition(
        self,
        workflow_id: UUID,
        *,
        expected: str,
        status: str,
        event_type: str,
        details: dict[str, Any] | None = None,
        action_id: UUID | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._repository.transition,
                workflow_id,
                expected_status=expected,
                status=status,
                event_type=event_type,
                details=details,
                action_id=action_id,
                fields=fields,
            )
        except WorkflowRepositoryError as error:
            raise WorkflowFailure(
                error.code, "Workflow state transition failed"
            ) from error

    async def _mark_failed(self, workflow_id: UUID, code: str) -> None:
        try:
            await asyncio.to_thread(self._repository.fail, workflow_id, code)
        except WorkflowRepositoryError as error:
            LOGGER.error("Could not persist failed workflow state (%s)", error.code)

    @staticmethod
    def _principal_provider(context: object) -> AuthenticatedClinician:
        del context
        clinician = _CURRENT_CLINICIAN.get()
        if clinician is None:
            raise RuntimeError("No authenticated clinician context is available")
        return clinician

    @staticmethod
    def _maybe_tool_error(payload: object) -> ToolError | None:
        try:
            return ToolError.model_validate(payload)
        except ValidationError:
            return None

    @staticmethod
    def _digest(value: object) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
