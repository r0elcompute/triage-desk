import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from triage_sentinel.data.database import create_session_factory
from triage_sentinel.data.enums import Measurement
from triage_sentinel.data.models import (
    AuditLog,
    ClinicianApproval,
    ClinicianReviewRequest,
    QueueCommit,
    QueueEvent,
    WorkflowExecution,
)
from triage_sentinel.data.repositories import (
    AssessmentRepository,
    EncounterRepository,
    PatientRepository,
)
from triage_sentinel.data.schemas import (
    AssessmentVersionCreate,
    EncounterCreate,
    PatientCreate,
)
from triage_sentinel.main import app
from triage_sentinel.reference_mcp import (
    FILESYSTEM_SERVER_ID,
    ReferenceRetrievalAudit,
    ReferenceRetrievalResult,
)
from triage_sentinel.workflow import (
    OllamaStructuredModel,
    RecommendationDraft,
    TriageWorkflow,
    WorkflowRequest,
    _expected_draft,
)
from triage_tools_mcp.schemas import (
    AuthenticatedClinician,
    CommitApprovedQueueUpdateOutput,
    EvaluateTriageOutput,
    ProtocolReferenceInput,
    RequestClinicianReviewOutput,
    ValidateVitalsOutput,
)


class FakeTools:
    def __init__(self, *, complete: bool = True, timeout_validation: bool = False):
        self.complete = complete
        self.timeout_validation = timeout_validation
        self.calls: list[tuple[str, dict]] = []
        self.assessment_id = uuid4()
        self.encounter_id = uuid4()

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name == "validate_vitals":
            if self.timeout_validation:
                raise TimeoutError
            return ValidateVitalsOutput(
                valid=True,
                complete=self.complete,
                missing_measurements=([] if self.complete else [Measurement.HEART_RATE]),
                issues=[],
                validated_at=datetime.now(UTC),
                action_id=uuid4(),
            )
        if name == "evaluate_triage_rules":
            return EvaluateTriageOutput(
                status="evaluated",
                assessment_version_id=self.assessment_id,
                protocol_version="synthetic-test-protocol",
                findings=[],
                supporting_evidence=[],
                evaluated_at=datetime.now(UTC),
                action_id=uuid4(),
            )
        if name == "request_clinician_review":
            return RequestClinicianReviewOutput(
                status="pending",
                request_id=uuid4(),
                assessment_version_id=self.assessment_id,
                created_at=datetime.now(UTC),
                action_id=uuid4(),
            )
        if name == "commit_approved_queue_update":
            return CommitApprovedQueueUpdateOutput(
                status="committed",
                queue_event_id=uuid4(),
                commit_id=uuid4(),
                assessment_version_id=UUID(arguments["assessment_version_id"]),
                approval_id=UUID(arguments["approval_id"]),
                action_id=uuid4(),
            )
        raise AssertionError(f"Unexpected MCP tool call: {name}")


class ExactModel:
    model_id = "test-open-weight-model"

    async def generate(self, evaluation: EvaluateTriageOutput) -> RecommendationDraft:
        return _expected_draft(evaluation)


class FakeReferenceClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def retrieve(self, document_id: str) -> ReferenceRetrievalResult:
        self.calls.append(document_id)
        content = "Synthetic non-clinical protocol fixture."
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ReferenceRetrievalResult(
            document=ProtocolReferenceInput(
                protocol_id=document_id,
                version="synthetic-test-protocol",
                approval_status="approved",
                document_class="synthetic-test",
                source_url="test-fixture://synthetic-protocol",
                license="Synthetic test fixture",
                content_sha256=digest,
                content=content,
            ),
            audit=ReferenceRetrievalAudit(
                server_identity=FILESYSTEM_SERVER_ID,
                requested_document_id=document_id,
                returned_version="synthetic-test-protocol",
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


class InventingModel(ExactModel):
    async def generate(self, evaluation: EvaluateTriageOutput) -> RecommendationDraft:
        expected = _expected_draft(evaluation)
        return expected.model_copy(update={"finding_codes": ["model.invented"]})


def workflow_request(tools: FakeTools) -> WorkflowRequest:
    return WorkflowRequest(
        encounter_id=tools.encounter_id,
        assessment_version_id=tools.assessment_id,
        protocol_id="TEST-SYNTHETIC-PROTOCOL",
        observations=[
            {
                "measurement": "heart_rate",
                "value": 72,
                "unit": "bpm",
                "measured_at": "2026-10-14T12:00:00Z",
                "source": "device",
                "source_reference": "SYN-DEVICE-01",
            }
        ],
        required_measurements=[Measurement.HEART_RATE],
    )


@pytest.mark.anyio
async def test_workflow_calls_tools_and_stops_at_pending_review() -> None:
    tools = FakeTools()
    references = FakeReferenceClient()
    workflow = TriageWorkflow(tools, ExactModel(), reference_client=references)

    result = await workflow.run(workflow_request(tools))

    assert result["status"] == "pending_review"
    assert result["model_id"] == "test-open-weight-model"
    assert result["recommendation"]["workflow_status"] == "pending_clinician_review"
    assert [name for name, _ in tools.calls] == [
        "validate_vitals",
        "evaluate_triage_rules",
        "request_clinician_review",
    ]
    assert all("commit_approved_queue_update" != name for name, _ in tools.calls)
    assert result["review_request"]["status"] == "pending"
    assert references.calls == ["TEST-SYNTHETIC-PROTOCOL"]
    assert result["reference_retrieval"]["status"] == "succeeded"


@pytest.mark.anyio
async def test_incomplete_observations_skip_evaluation_and_cannot_resume() -> None:
    tools = FakeTools(complete=False)
    references = FakeReferenceClient()
    workflow = TriageWorkflow(tools, ExactModel(), reference_client=references)
    result = await workflow.run(workflow_request(tools))

    assert result["status"] == "pending_review"
    assert result["recommendation"] is None
    assert [name for name, _ in tools.calls] == [
        "validate_vitals",
        "request_clinician_review",
    ]
    assert references.calls == []
    with pytest.raises(ValueError, match="not eligible"):
        await workflow.commit_approved(
            result,
            clinician=AuthenticatedClinician(
                subject="clinician-1",
                reference="SYN-CLINICIAN-1",
                permissions=frozenset({"triage:queue:commit"}),
            ),
            approval_id=uuid4(),
            assessment_number=1,
            queue_name="SYN-QUEUE-1",
            idempotency_key="workflow-test-key-0001",
        )


@pytest.mark.anyio
async def test_resume_rejects_artifacts_for_a_different_assessment() -> None:
    tools = FakeTools()
    workflow = TriageWorkflow(
        tools, ExactModel(), reference_client=FakeReferenceClient()
    )
    pending = await workflow.run(workflow_request(tools))
    pending["evaluation"]["assessment_version_id"] = str(uuid4())
    clinician = AuthenticatedClinician(
        subject="clinician-1",
        reference="SYN-CLINICIAN-1",
        permissions=frozenset({"triage:queue:commit"}),
    )

    with pytest.raises(ValueError, match="do not match the assessment"):
        await workflow.commit_approved(
            pending,
            clinician=clinician,
            approval_id=uuid4(),
            assessment_number=1,
            queue_name="SYN-QUEUE-1",
            idempotency_key="workflow-test-key-0003",
        )

    assert all(name != "commit_approved_queue_update" for name, _ in tools.calls)


@pytest.mark.anyio
async def test_model_invented_findings_are_rejected_but_review_is_requested() -> None:
    tools = FakeTools()
    result = await TriageWorkflow(
        tools, InventingModel(), reference_client=FakeReferenceClient()
    ).run(workflow_request(tools))

    assert result["status"] == "pending_review"
    assert result["error_code"] == "MODEL_OUTPUT_REJECTED"
    assert result["recommendation"] is None
    assert tools.calls[-1][0] == "request_clinician_review"


@pytest.mark.anyio
async def test_mcp_timeout_fails_closed_and_still_requests_review() -> None:
    tools = FakeTools(timeout_validation=True)
    result = await TriageWorkflow(
        tools, ExactModel(), reference_client=FakeReferenceClient()
    ).run(workflow_request(tools))

    assert result["status"] == "pending_review"
    assert result["error_code"] == "TOOL_TIMEOUT"
    assert [name for name, _ in tools.calls] == [
        "validate_vitals",
        "request_clinician_review",
    ]


@pytest.mark.anyio
async def test_missing_approved_protocol_skips_evaluation_and_requests_review() -> None:
    tools = FakeTools()
    result = await TriageWorkflow(
        tools, ExactModel(), reference_client=MissingReferenceClient()
    ).run(workflow_request(tools))

    assert result["status"] == "pending_review"
    assert result["error_code"] == "PROTOCOL_DOCUMENT_NOT_FOUND"
    assert result["recommendation"] is None
    assert result["reference_retrieval"]["status"] == "failed"
    assert [name for name, _ in tools.calls] == [
        "validate_vitals",
        "request_clinician_review",
    ]


def test_workflow_rejects_non_synthetic_observation_references() -> None:
    tools = FakeTools()
    data = workflow_request(tools).model_dump()
    data["observations"][0]["source_reference"] = "REAL-PATIENT-REFERENCE"

    with pytest.raises(ValidationError):
        WorkflowRequest.model_validate(data)


def test_workflow_start_requires_configured_trusted_identity_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER", "")
    response = TestClient(app).post(
        "/workflows/triage",
        json={
            "encounter_id": str(uuid4()),
            "assessment_version_id": str(uuid4()),
            "protocol_id": "TEST-SYNTHETIC-PROTOCOL",
            "observations": [],
            "required_measurements": ["heart_rate"],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Trusted workflow identity provider is not configured"


@pytest.mark.anyio
async def test_approved_resume_calls_protected_commit_tool() -> None:
    tools = FakeTools()
    workflow = TriageWorkflow(
        tools, ExactModel(), reference_client=FakeReferenceClient()
    )
    pending = await workflow.run(workflow_request(tools))
    clinician = AuthenticatedClinician(
        subject="clinician-1",
        reference="SYN-CLINICIAN-1",
        permissions=frozenset({"triage:queue:commit"}),
    )

    committed = await workflow.commit_approved(
        pending,
        clinician=clinician,
        approval_id=uuid4(),
        assessment_number=1,
        queue_name="SYN-QUEUE-1",
        idempotency_key="workflow-test-key-0002",
    )

    assert committed["status"] == "committed"
    assert tools.calls[-1][0] == "commit_approved_queue_update"


@pytest.mark.anyio
@pytest.mark.skipif(
    os.getenv("TRIAGE_RUN_OLLAMA_INTEGRATION") != "1",
    reason="set TRIAGE_RUN_OLLAMA_INTEGRATION=1 with a local Ollama model",
)
async def test_local_ollama_model_runs_inside_workflow_graph() -> None:
    tools = FakeTools()
    model = OllamaStructuredModel(
        model_id=os.getenv("TRIAGE_OLLAMA_MODEL", "qwen3:1.7b"),
        base_url=os.getenv("TRIAGE_OLLAMA_URL", "http://127.0.0.1:11434"),
        timeout_seconds=180,
    )

    result = await TriageWorkflow(
        tools, model, reference_client=FakeReferenceClient()
    ).run(workflow_request(tools))

    assert result["status"] == "pending_review"
    assert result["model_id"] == model.model_id
    assert result["recommendation"] == _expected_draft(
        EvaluateTriageOutput.model_validate(result["evaluation"])
    ).model_dump(mode="json")
    assert [name for name, _ in tools.calls] == [
        "validate_vitals",
        "evaluate_triage_rules",
        "request_clinician_review",
    ]


@pytest.mark.anyio
@pytest.mark.skipif(
    os.getenv("TRIAGE_RUN_REAL_MCP_INTEGRATION") != "1"
    or os.getenv("TRIAGE_RUN_FILESYSTEM_MCP_INTEGRATION") != "1",
    reason=(
        "set both integration flags with PostgreSQL, Ollama, Docker, and both MCP servers"
    ),
)
async def test_local_model_mcp_review_and_approved_resume_end_to_end(
    migrated_test_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with migrated_test_engine.connect() as connection:
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    monkeypatch.setenv("DATABASE_URL", str(migrated_test_engine.url))
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema}")
    monkeypatch.setenv(
        "TRIAGE_MCP_CLINICAL_EVALUATOR",
        "fake_evaluator:SyntheticWorkflowEvaluator",
    )
    monkeypatch.setenv(
        "TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER",
        "fake_evaluator:SyntheticWorkflowPrincipalProvider",
    )
    monkeypatch.setenv(
        "TRIAGE_PROTOCOL_REFERENCE_DIRECTORY",
        str(Path(__file__).resolve().parent / "fixtures" / "approved_protocols"),
    )
    tests_path = str(Path(__file__).resolve().parent)
    existing_pythonpath = os.getenv("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH",
        tests_path + (os.pathsep + existing_pythonpath if existing_pythonpath else ""),
    )
    factory: sessionmaker[Session] = create_session_factory(migrated_test_engine)
    with factory.begin() as session:
        patient = PatientRepository(session).create(
            PatientCreate(synthetic_reference=f"SYN-PATIENT-{uuid4().hex.upper()}")
        )
        encounter = EncounterRepository(session).create(patient.id, EncounterCreate())
        assessment = AssessmentRepository(session).create_version(
            encounter.id,
            AssessmentVersionCreate(
                recommendation="Synthetic integration assessment",
                created_by_reference="SYN-WORKFLOW-TEST",
            ),
        )

    request = WorkflowRequest(
        encounter_id=encounter.id,
        assessment_version_id=assessment.id,
        protocol_id="TEST-SYNTHETIC-PROTOCOL",
        observations=[
            {
                "measurement": "heart_rate",
                "value": 72,
                "unit": "bpm",
                "measured_at": "2026-10-14T12:00:00Z",
                "source": "device",
                "source_reference": "SYN-DEVICE-01",
            }
        ],
        required_measurements=[Measurement.HEART_RATE],
    )
    client = TestClient(app)
    start_response = client.post("/workflows/triage", json=request.model_dump(mode="json"))
    assert start_response.status_code == 200, start_response.text
    result = start_response.json()
    assert result["status"] == "pending_review"
    assert result["recommendation"] is not None, result
    assert result["recommendation"]["finding_codes"] == ["synthetic.fixture"]
    assert result["review_request"]["status"] == "pending"
    assert "request" not in result
    assert "protocol_reference" not in result
    assert result["reference_retrieval"]["status"] == "succeeded"
    assert result["protocol_reference_metadata"]["version"] == "test-v1"
    with factory() as session:
        review = session.get(
            ClinicianReviewRequest, UUID(result["review_request"]["request_id"])
        )
        queue_events = session.scalars(select(QueueEvent)).all()
        execution = session.get(WorkflowExecution, UUID(result["workflow_id"]))
    assert review is not None
    assert queue_events == []
    assert execution is not None
    assert execution.status == "pending_review"
    assert execution.model_identifier == "qwen3:1.7b"
    assert execution.reference_retrieval["server_identity"] == FILESYSTEM_SERVER_ID
    assert execution.reference_retrieval["tool_name"] == "read_text_file"
    assert execution.protocol_reference["version"] == "test-v1"
    assert execution.evaluation_result["protocol_version"] == "test-v1"
    assert execution.recommendation_result["finding_codes"] == ["synthetic.fixture"]

    resume_response = client.post(
        f"/workflows/{execution.id}/approve",
        json={
            "assessment_number": assessment.version,
            "queue_name": "SYN-QUEUE-TEST",
            "idempotency_key": "workflow-live-test-key-0001",
        },
    )
    assert resume_response.status_code == 200, resume_response.text
    assert resume_response.json()["status"] == "committed"
    with factory() as session:
        commits = session.scalars(select(QueueCommit)).all()
        events = session.scalars(select(QueueEvent)).all()
        approvals = session.scalars(select(ClinicianApproval)).all()
        reference_audit = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "workflow_execution",
                AuditLog.entity_id == execution.id,
            )
        ).one()
        completed = session.get(WorkflowExecution, execution.id)
    assert len(approvals) == 1
    assert len(commits) == 1
    assert len(events) == 1
    assert completed is not None
    assert completed.status == "committed"
    assert reference_audit.action == "mcp.filesystem.read_text_file.succeeded"