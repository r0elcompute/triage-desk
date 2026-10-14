import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import CheckConstraint, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from triage_sentinel.data.enums import (
    AuditEntityType,
)
from triage_sentinel.data.models import (
    AssessmentVersion,
    AuditLog,
    Base,
    QueueCommit,
    QueueEvent,
    ToolExecution,
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
from triage_sentinel.evaluation.schemas import (
    ClinicalObservation,
    EvaluateTriageInput,
    EvaluateTriageOutput,
)
from triage_sentinel.workflow.engine import (
    OllamaChatModel,
    TriageWorkflow,
    WorkflowFailure,
)
from triage_sentinel.workflow.schemas import (
    ModelAcknowledgement,
    WorkflowStartInput,
)
from triage_tools_mcp.schemas import AuthenticatedClinician
from triage_tools_mcp.service import (
    APPROVE_PERMISSION,
    COMMIT_PERMISSION,
    ClinicalEvaluator,
    ClinicalEvaluatorUnavailable,
    TriageToolsService,
)

SYNTHETIC_EXPECTED_PENDING = {
    "status": "awaiting_clinician_approval",
    "queue_event_count": 0,
    "validation_complete": True,
    "evaluator_status": "indeterminate",
}
SYNTHETIC_EXPECTED_COMMIT = {
    "status": "completed",
    "queue_event_count": 1,
    "commit_status": "committed",
}


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record) -> None:
        del record
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    removed_checks = [
        (table, constraint)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    for table, constraint in removed_checks:
        table.constraints.remove(constraint)
    try:
        Base.metadata.create_all(engine)
    finally:
        for table, constraint in removed_checks:
            table.append_constraint(constraint)

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def create_assessment(factory: sessionmaker[Session]) -> AssessmentVersion:
    with factory.begin() as session:
        patient = PatientRepository(session).create(
            PatientCreate(synthetic_reference=f"SYN-PATIENT-{uuid4().hex[:12].upper()}")
        )
        encounter = EncounterRepository(session).create(patient.id, EncounterCreate())
        assessment = AssessmentRepository(session).create_version(
            encounter.id,
            AssessmentVersionCreate(
                recommendation="Synthetic assessment",
                created_by_reference="SYN-SYSTEM-TEST",
            ),
        )
        session.expunge(assessment)
        return assessment


def synthetic_request(assessment: AssessmentVersion) -> WorkflowStartInput:
    measured_at = datetime(2026, 10, 14, 16, tzinfo=UTC)
    readings = [
        ("heart_rate", "72", "bpm"),
        ("respiratory_rate", "18", "breaths/min"),
        ("temperature", "37", "degC"),
        ("oxygen_saturation", "98", "%"),
    ]
    return WorkflowStartInput(
        encounter_id=assessment.encounter_id,
        assessment_version_id=assessment.id,
        age_months=Decimal("600"),  # noqa: FURB157
        observations=[
            ClinicalObservation.model_validate(
                {
                    "observation_id": f"SYN-OBS-{measurement.upper().replace('_', '-')}",
                    "measurement": measurement,
                    "value": value,
                    "unit": unit,
                    "measured_at": measured_at,
                    "source": "device",
                }
            )
            for measurement, value, unit in readings
        ],
        evaluated_at=datetime(2026, 10, 14, 16, 1, tzinfo=UTC),
    )


class EchoModel:
    model_id = "synthetic-echo-model-v1"

    async def acknowledge(self, result: EvaluateTriageOutput) -> ModelAcknowledgement:
        return ModelAcknowledgement(
            evaluator_status=result.status,
            matched_rule_ids=sorted(
                finding.rule_id
                for finding in result.findings
                if finding.rule_state == "met"
            ),
        )


class MismatchedModel(EchoModel):
    async def acknowledge(self, result: EvaluateTriageOutput) -> ModelAcknowledgement:
        del result
        return ModelAcknowledgement(
            evaluator_status="evaluated",
            matched_rule_ids=["invented.rule"],
        )


class TimedOutModel(EchoModel):
    async def acknowledge(self, result: EvaluateTriageOutput) -> ModelAcknowledgement:
        del result
        raise WorkflowFailure("MODEL_TIMEOUT", "synthetic local model timeout")


class UnavailableEvaluator:
    def evaluate(self, data: EvaluateTriageInput) -> EvaluateTriageOutput:
        del data
        raise ClinicalEvaluatorUnavailable("synthetic protocol unavailable")


def clinician() -> AuthenticatedClinician:
    return AuthenticatedClinician(
        subject="synthetic-clinician-subject",
        reference="SYN-CLINICIAN-TEST",
        permissions=frozenset({APPROVE_PERMISSION, COMMIT_PERMISSION}),
    )


def create_workflow(
    factory: sessionmaker[Session],
    model: EchoModel | None = None,
    evaluator: ClinicalEvaluator | None = None,
) -> TriageWorkflow:
    return TriageWorkflow(
        factory,
        TriageToolsService(factory, evaluator),
        model or EchoModel(),
    )


def test_synthetic_workflow_stops_pending_without_live_queue_change(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = create_assessment(session_factory)
    output = asyncio.run(
        create_workflow(session_factory).start(synthetic_request(assessment))
    )
    assert output.status == SYNTHETIC_EXPECTED_PENDING["status"]
    assert output.validation_result is not None
    assert output.validation_result.valid is True
    assert (
        output.validation_result.complete
        is SYNTHETIC_EXPECTED_PENDING["validation_complete"]
    )
    assert output.evaluation_result is not None
    assert (
        output.evaluation_result.status
        == SYNTHETIC_EXPECTED_PENDING["evaluator_status"]
    )
    assert output.recommendation is not None
    assert output.recommendation.triage_category is None
    assert output.recommendation.requires_clinician_review is True
    assert output.review_request_id is not None
    with session_factory() as session:
        assert session.scalars(select(QueueEvent)).all() == []
        workflow_events = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.WORKFLOW_EXECUTION.value
            )
        ).all()
    assert len(workflow_events) >= 5


def test_workflow_resumes_with_authorized_clinician_and_commits_once(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = create_assessment(session_factory)
    pending = asyncio.run(
        create_workflow(session_factory).start(synthetic_request(assessment))
    )
    workflow = create_workflow(session_factory)

    with pytest.raises(WorkflowFailure, match="lacks approval"):
        asyncio.run(
            workflow.resume(
                pending.workflow_id,
                AuthenticatedClinician(
                    subject="unprivileged-clinician",
                    reference="SYN-CLINICIAN-OTHER",
                    permissions=frozenset(),
                ),
            )
        )

    completed = asyncio.run(workflow.resume(pending.workflow_id, clinician()))
    repeated = asyncio.run(workflow.resume(pending.workflow_id, clinician()))

    assert completed.status == SYNTHETIC_EXPECTED_COMMIT["status"]
    assert any(
        event.event_type == "workflow.approval.denied" for event in completed.events
    )
    assert completed.commit_result is not None
    assert completed.commit_result.status == SYNTHETIC_EXPECTED_COMMIT["commit_status"]
    assert repeated.commit_result == completed.commit_result
    with session_factory() as session:
        queue_events = session.scalars(select(QueueEvent)).all()
        commits = session.scalars(select(QueueCommit)).all()
        execution = session.get(WorkflowExecution, pending.workflow_id)
        tool_executions = session.scalars(select(ToolExecution)).all()
        tool_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.TOOL_EXECUTION.value
            )
        ).all()
    assert len(queue_events) == SYNTHETIC_EXPECTED_COMMIT["queue_event_count"]
    assert len(commits) == 1
    assert execution is not None
    assert execution.approval_id == completed.approval_id
    assert {item.tool_name for item in tool_executions} == {
        "validate_vitals",
        "evaluate_triage_rules",
        "request_clinician_review",
        "commit_approved_queue_update",
    }
    assert all(item.status == "succeeded" for item in tool_executions)
    assert len(tool_audits) == len(tool_executions)


def test_incomplete_information_is_preserved_as_pending_and_indeterminate(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = create_assessment(session_factory)
    request = synthetic_request(assessment).model_copy(update={"observations": []})

    output = asyncio.run(create_workflow(session_factory).start(request))

    assert output.status == "awaiting_clinician_approval"
    assert output.validation_result is not None
    assert output.validation_result.valid is True
    assert output.validation_result.complete is False
    assert output.evaluation_result is not None
    assert output.evaluation_result.status == "indeterminate"
    assert output.evaluation_result.triage_category is None
    assert output.recommendation is not None
    assert "no low-acuity conclusion" in output.recommendation.summary


def test_model_mismatch_fails_without_creating_review_or_queue_change(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = create_assessment(session_factory)
    output = asyncio.run(
        create_workflow(session_factory, MismatchedModel()).start(
            synthetic_request(assessment)
        )
    )

    assert output.status == "failed"
    assert output.error_code == "MODEL_OUTPUT_MISMATCH"
    assert output.review_request_id is None
    with session_factory() as session:
        assert session.scalars(select(QueueEvent)).all() == []
        assert session.scalars(select(QueueCommit)).all() == []


def test_model_timeout_fails_closed_without_creating_review(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = create_assessment(session_factory)
    output = asyncio.run(
        create_workflow(session_factory, TimedOutModel()).start(
            synthetic_request(assessment)
        )
    )

    assert output.status == "failed"
    assert output.error_code == "MODEL_TIMEOUT"
    assert output.review_request_id is None
    with session_factory() as session:
        assert session.scalars(select(QueueEvent)).all() == []
        workflow_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.WORKFLOW_EXECUTION.value
            )
        ).all()
    assert any(row.action == "workflow.failed" for row in workflow_audits)


def test_unavailable_evaluator_returns_audited_failure_without_review(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = create_assessment(session_factory)
    output = asyncio.run(
        create_workflow(session_factory, evaluator=UnavailableEvaluator()).start(
            synthetic_request(assessment)
        )
    )

    assert output.status == "failed"
    assert output.error_code == "EVALUATOR_UNAVAILABLE"
    assert output.evaluation_result is None
    assert output.review_request_id is None
    with session_factory() as session:
        assert session.scalars(select(QueueEvent)).all() == []
        failed_tools = session.scalars(
            select(ToolExecution).where(
                ToolExecution.tool_name == "evaluate_triage_rules"
            )
        ).all()
        failed_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.TOOL_EXECUTION.value
            )
        ).all()
    assert len(failed_tools) == 1
    assert failed_tools[0].status == "failed"
    assert failed_tools[0].error_code == "EVALUATOR_UNAVAILABLE"
    assert len(failed_audits) == 2
    assert any(
        row.action == "tool.evaluate_triage_rules.failed" for row in failed_audits
    )


def test_stale_assessment_is_rejected_on_resume(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = create_assessment(session_factory)
    workflow = create_workflow(session_factory)
    pending = asyncio.run(workflow.start(synthetic_request(assessment)))
    create_assessment_version(session_factory, assessment)

    failed = asyncio.run(workflow.resume(pending.workflow_id, clinician()))

    assert failed.status == "failed"
    assert failed.error_code == "STALE_ASSESSMENT"
    with session_factory() as session:
        assert session.scalars(select(QueueEvent)).all() == []


def create_assessment_version(
    factory: sessionmaker[Session], previous: AssessmentVersion
) -> None:
    with factory.begin() as session:
        AssessmentRepository(session).create_version(
            previous.encounter_id,
            AssessmentVersionCreate(
                recommendation="New synthetic assessment",
                created_by_reference="SYN-SYSTEM-TEST",
            ),
        )


def test_model_runtime_requires_loopback_host() -> None:
    with pytest.raises(ValueError, match="local loopback"):
        OllamaChatModel(base_url="http://model.example:11434")


def test_ollama_model_validates_json_response_and_maps_bad_output() -> None:
    evaluation = EvaluateTriageOutput(
        status="indeterminate",
        assessment_version_id=uuid4(),
        protocol_id="WHO-IITT-ROUTINE",
        protocol_version="synthetic-protocol-1",
        assessment_output_version="1.0.0",
        findings=[],
        supporting_evidence=[],
        missing_information=["heart_rate"],
        evaluation_uncertainties=[],
        unsupported_criteria=["synthetic.unsupported"],
        evaluated_at=datetime.now(UTC),
    )

    async def valid_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "qwen2.5:1.5b"
        assert body["options"]["temperature"] == 0
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"evaluator_status":"indeterminate",'
                    '"matched_rule_ids":[]}'
                }
            },
        )

    model = OllamaChatModel(
        transport=httpx.MockTransport(valid_handler),
    )
    result = asyncio.run(model.acknowledge(evaluation))
    assert result.evaluator_status == evaluation.status
    assert result.matched_rule_ids == []

    async def malformed_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    malformed_model = OllamaChatModel(
        transport=httpx.MockTransport(malformed_handler),
    )
    with pytest.raises(WorkflowFailure) as failure:
        asyncio.run(malformed_model.acknowledge(evaluation))
    assert failure.value.code == "MODEL_OUTPUT_INVALID"

    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    timeout_model = OllamaChatModel(
        transport=httpx.MockTransport(timeout_handler),
    )
    with pytest.raises(WorkflowFailure) as failure:
        asyncio.run(timeout_model.acknowledge(evaluation))
    assert failure.value.code == "MODEL_TIMEOUT"


def test_approval_route_requires_trusted_request_context() -> None:
    from triage_sentinel.main import approve_workflow

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/workflows/test/approval",
            "headers": [],
            "query_string": b"",
            "state": {},
        }
    )

    with pytest.raises(HTTPException) as failure:
        asyncio.run(approve_workflow(uuid4(), request))
    assert failure.value.status_code == 401
