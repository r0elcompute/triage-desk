"""Offline LangGraph trace using test doubles; not a live MCP/model demo."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from triage_sentinel.data.enums import Measurement
from triage_sentinel.reference_mcp import (
    FILESYSTEM_SERVER_ID,
    ReferenceRetrievalAudit,
    ReferenceRetrievalResult,
)
from triage_sentinel.workflow import (
    RecommendationDraft,
    TriageWorkflow,
    WorkflowRequest,
    _expected_draft,
)
from triage_sentinel.workflow.service import WorkflowObservation
from triage_tools_mcp.schemas import (
    EvaluateTriageOutput,
    ProtocolReferenceInput,
    RequestClinicianReviewOutput,
    ValidateVitalsOutput,
)


class DemoToolCaller:
    def __init__(self, assessment_id: UUID) -> None:
        self.assessment_id = assessment_id
        self.calls: list[str] = []

    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append(name)
        print(
            f"MCP TOOL (test double): {name} "
            f"arguments={json.dumps(arguments, sort_keys=True, default=str)}"
        )
        if name == "validate_vitals":
            result = ValidateVitalsOutput(
                valid=True,
                complete=True,
                missing_measurements=[],
                issues=[],
                validated_at=datetime.now(UTC),
                action_id=uuid4(),
            )
        elif name == "evaluate_triage_rules":
            result = EvaluateTriageOutput(
                status="evaluated",
                assessment_version_id=self.assessment_id,
                protocol_version="synthetic-test-only",
                findings=[],
                supporting_evidence=[],
                evaluated_at=datetime.now(UTC),
                action_id=uuid4(),
            )
        elif name == "request_clinician_review":
            result = RequestClinicianReviewOutput(
                status="pending",
                request_id=uuid4(),
                assessment_version_id=self.assessment_id,
                created_at=datetime.now(UTC),
                action_id=uuid4(),
            )
        else:
            raise AssertionError(f"Unexpected tool call in demo: {name}")
        return result.model_dump(mode="json")


class DemoModel:
    model_id = "synthetic-test-double-not-a-model"

    async def generate(self, evaluation: EvaluateTriageOutput) -> RecommendationDraft:
        print("MODEL (test double): returning only the supplied synthetic tool result")
        return _expected_draft(evaluation)


class DemoReferenceClient:
    async def retrieve(self, document_id: str) -> ReferenceRetrievalResult:
        content = "Synthetic non-clinical protocol fixture."
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document = ProtocolReferenceInput(
            protocol_id=document_id,
            version="synthetic-test-only",
            approval_status="approved",
            document_class="synthetic-test",
            source_url="test-fixture://synthetic-protocol",
            license="Synthetic fixture",
            content_sha256=digest,
            content=content,
        )
        return ReferenceRetrievalResult(
            document=document,
            audit=ReferenceRetrievalAudit(
                server_identity=FILESYSTEM_SERVER_ID,
                requested_document_id=document_id,
                returned_version=document.version,
                content_sha256=digest,
                status="succeeded",
            ),
        )


class MissingReferenceClient:
    async def retrieve(self, document_id: str) -> ReferenceRetrievalResult:
        return ReferenceRetrievalResult(
            document=None,
            audit=ReferenceRetrievalAudit(
                requested_document_id=document_id,
                status="failed",
                error_code="PROTOCOL_DOCUMENT_NOT_FOUND",
            ),
        )


async def run_case(name: str, *, reference_available: bool) -> None:
    print(f"\n=== {name} ===")
    assessment_id = uuid4()
    encounter_id = uuid4()
    tools = DemoToolCaller(assessment_id)
    reference_client = (
        DemoReferenceClient() if reference_available else MissingReferenceClient()
    )
    workflow = TriageWorkflow(
        tools,
        DemoModel(),
        reference_client=reference_client,
    )
    request = WorkflowRequest(
        encounter_id=encounter_id,
        assessment_version_id=assessment_id,
        protocol_id="SYNTHETIC-DEMO-ONLY",
        observations=[
            WorkflowObservation(
                measurement="heart_rate",
                value=72,
                unit="bpm",
                measured_at=datetime.now(UTC),
                source="device",
                source_reference="SYN-DEMO-OBS-1",
            )
        ],
        required_measurements=[Measurement.HEART_RATE],
    )
    result = await workflow.run(request)
    print("VISIBLE MCP TOOL CALLS:", ", ".join(tools.calls) or "(none)")
    print(
        "WORKFLOW RESULT:",
        json.dumps(
            {
                "status": result.get("status"),
                "error_code": result.get("error_code"),
                "recommendation": result.get("recommendation"),
                "review_request": result.get("review_request"),
            },
            sort_keys=True,
        ),
    )
    print("QUEUE COMMIT: not called; no approval was granted")


async def main() -> None:
    print("OFFLINE DEMONSTRATION ONLY: MCP and model adapters are test doubles.")
    print("No real clinical protocol, patient data, approval, or queue write.")
    await run_case("Synthetic path with a fixture reference", reference_available=True)
    await run_case("Failure path: requested reference unavailable", reference_available=False)


if __name__ == "__main__":
    asyncio.run(main())
