from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CheckConstraint, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from triage_sentinel.data.enums import (
    AuditEntityType,
    Measurement,
    QueueEventType,
)
from triage_sentinel.data.models import (
    AssessmentVersion,
    AuditLog,
    Base,
    ClinicianReviewRequest,
    QueueCommit,
    QueueEvent,
    ToolExecution,
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
    EvaluateTriageOutput as IittEvaluateTriageOutput,
)
from triage_tools_mcp.schemas import (
    AuthenticatedClinician,
    CommitApprovedQueueUpdateInput,
    EvaluateTriageInput,
    EvaluateTriageOutput,
    RequestClinicianReviewInput,
    TriageFinding,
    ValidateVitalsInput,
)
from triage_tools_mcp.server import create_mcp_server
from triage_tools_mcp.service import (
    APPROVE_PERMISSION,
    COMMIT_PERMISSION,
    ClinicalEvaluator,
    ClinicalEvaluatorUnavailable,
    TriageToolsService,
)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
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


def setup_assessment(factory: sessionmaker[Session]) -> AssessmentVersion:
    with factory.begin() as session:
        patient = PatientRepository(session).create(
            PatientCreate(synthetic_reference=f"SYN-PATIENT-{uuid4().hex[:12].upper()}")
        )
        encounter = EncounterRepository(session).create(patient.id, EncounterCreate())
        assessment = AssessmentRepository(session).create_version(
            encounter.id,
            AssessmentVersionCreate(
                recommendation="Synthetic recommendation",
                created_by_reference="SYN-SYSTEM-TEST",
            ),
        )
        session.flush()
        session.expunge(assessment)
        session.expunge(encounter)
        return assessment


def clinician(*permissions: str) -> AuthenticatedClinician:
    return AuthenticatedClinician(
        subject="authenticated-clinician-subject",
        reference="SYN-CLINICIAN-TEST",
        permissions=frozenset(permissions),
    )


def request_review(service: TriageToolsService, assessment_id) -> str:
    result = service.request_clinician_review(
        RequestClinicianReviewInput(
            assessment_version_id=assessment_id,
            reason="Approval required before queue change",
        )
    )
    assert result.status == "pending"
    return str(result.request_id)


def approve_request(service: TriageToolsService, assessment_id) -> str:
    request_id = request_review(service, assessment_id)
    approval_id = service.approve_review_request(
        UUID(request_id),
        clinician(APPROVE_PERMISSION),
    )
    return str(approval_id)


def commit_input(assessment: AssessmentVersion, approval_id: str, key: str):
    return CommitApprovedQueueUpdateInput(
        assessment_version_id=assessment.id,
        assessment_number=assessment.version,
        approval_id=approval_id,
        queue_name="SYN-QUEUE-TRIAGE",
        idempotency_key=key,
    )


def test_validate_vitals_reports_validity_completeness_and_audit(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = setup_assessment(session_factory)
    service = TriageToolsService(session_factory)
    result = service.validate_vitals(
        ValidateVitalsInput(
            encounter_id=assessment.encounter_id,
            required_measurements=[Measurement.HEART_RATE, Measurement.TEMPERATURE],
            observations=[
                {
                    "measurement": "heart_rate",
                    "value": "72",
                    "unit": "bpm",
                    "measured_at": "2026-10-14T16:00:00Z",
                    "source": "device",
                },
                {
                    "measurement": "temperature",
                    "value": "37",
                    "unit": "mmHg",
                    "measured_at": "2026-10-14T16:00:00Z",
                    "source": "device",
                },
                {
                    "measurement": "temperature",
                    "value": "37",
                    "unit": "degC",
                    "measured_at": "2026-10-14T16:00:00",
                    "source": "device",
                },
            ],
        )
    )
    valid_result = service.validate_vitals(
        ValidateVitalsInput(
            encounter_id=assessment.encounter_id,
            required_measurements=[Measurement.HEART_RATE, Measurement.TEMPERATURE],
            observations=[
                {
                    "measurement": "heart_rate",
                    "value": "72",
                    "unit": "bpm",
                    "measured_at": "2026-10-14T16:00:00Z",
                    "source": "device",
                },
                {
                    "measurement": "temperature",
                    "value": "37",
                    "unit": "degC",
                    "measured_at": "2026-10-14T16:00:00Z",
                    "source": "device",
                },
            ],
        )
    )

    assert result.valid is False
    assert result.complete is False
    assert result.missing_measurements == [Measurement.TEMPERATURE]
    assert len(result.issues) == 2
    assert valid_result.valid is True
    assert valid_result.complete is True
    with session_factory() as session:
        executions = session.scalars(select(ToolExecution)).all()
        audit_rows = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.TOOL_EXECUTION.value,
            )
        ).all()
    assert len(executions) == 2
    invalid_execution = next(row for row in executions if row.id == result.action_id)
    assert invalid_execution.input_digest is not None
    assert invalid_execution.output_digest is not None
    assert len(audit_rows) == 2
    assert all(row.action == "tool.validate_vitals.succeeded" for row in audit_rows)


class FixedEvaluator(ClinicalEvaluator):
    def evaluate(self, data: EvaluateTriageInput) -> EvaluateTriageOutput:
        return IittEvaluateTriageOutput(
            status="evaluated",
            assessment_version_id=data.assessment_version_id,
            protocol_id="SYNTHETIC-PROTOCOL",
            protocol_version="synthetic-protocol-1",
            assessment_output_version="1.0.0",
            findings=[
                TriageFinding(
                    code="synthetic.finding",
                    summary="Synthetic finding",
                    severity="info",
                    evidence=["observation:0"],
                    context_evidence=[],
                    rule_id="synthetic.finding",
                    rule_state="met",
                    source_reference="synthetic test protocol",
                )
            ],
            supporting_evidence=["observation:0"],
            missing_information=[],
            evaluation_uncertainties=[],
            unsupported_criteria=[],
            evaluated_at=datetime.now(UTC),
            action_id=uuid4(),
        )


class UnavailableEvaluator(ClinicalEvaluator):
    def evaluate(self, data: EvaluateTriageInput) -> EvaluateTriageOutput:
        raise ClinicalEvaluatorUnavailable("Synthetic evaluator unavailable")


def test_evaluate_triage_rules_calls_evaluator_and_structures_errors(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = setup_assessment(session_factory)
    data = EvaluateTriageInput(
        encounter_id=assessment.encounter_id,
        assessment_version_id=assessment.id,
        age_months=600,
        observations=[],
    )
    result = TriageToolsService(
        session_factory, FixedEvaluator()
    ).evaluate_triage_rules(data)
    unavailable = TriageToolsService(
        session_factory, UnavailableEvaluator()
    ).evaluate_triage_rules(data)
    default_evaluator_result = TriageToolsService(
        session_factory
    ).evaluate_triage_rules(data)

    assert result.status == "evaluated"
    assert result.protocol_version == "synthetic-protocol-1"
    assert result.findings[0].evidence == ["observation:0"]
    assert result.assessment_version_id == assessment.id
    assert result.action_id != uuid4()
    assert unavailable.code == "EVALUATOR_UNAVAILABLE"
    assert default_evaluator_result.status == "indeterminate"
    assert default_evaluator_result.protocol_id == "WHO-IITT-ROUTINE"
    with session_factory() as session:
        rows = session.scalars(
            select(ToolExecution).where(
                ToolExecution.tool_name == "evaluate_triage_rules"
            )
        ).all()
        audit_logs = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.TOOL_EXECUTION.value
            )
        ).all()
    assert len(rows) == 3
    assert sum(row.status == "failed" for row in rows) == 1
    assert len(audit_logs) == 3


def test_configured_iitt_evaluator_returns_versioned_findings_without_queue_write(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = setup_assessment(session_factory)
    data = EvaluateTriageInput(
        encounter_id=assessment.encounter_id,
        assessment_version_id=assessment.id,
        age_months=600,
        observations=[
            {
                "observation_id": "SYN-OBS-HR-1",
                "measurement": "heart_rate",
                "value": "49",
                "unit": "bpm",
                "measured_at": "2026-10-14T16:00:00Z",
                "source": "clinician",
            },
        ],
        presenting_complaints=[
            {
                "complaint_id": "SYN-COMPLAINT-1",
                "concept_code": "SYN-TEST",
                "summary": "Synthetic complaint",
            }
        ],
    )

    result = TriageToolsService(session_factory).evaluate_triage_rules(data)

    assert result.status == "red_escalation"
    assert result.protocol_id == "WHO-IITT-ROUTINE"
    assert result.triage_category == "red"
    assert result.findings[0].evidence == ["SYN-OBS-HR-1"]
    assert result.action_id.int != 0
    with session_factory() as session:
        assert session.scalars(select(QueueEvent)).all() == []


def test_review_request_is_pending_and_does_not_change_queue(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = setup_assessment(session_factory)
    service = TriageToolsService(session_factory)
    result = service.request_clinician_review(
        RequestClinicianReviewInput(
            assessment_version_id=assessment.id, reason="Needs clinician review"
        )
    )

    assert result.status == "pending"
    assert result.assessment_version_id == assessment.id
    with session_factory() as session:
        request = session.get(ClinicianReviewRequest, result.request_id)
        queue_events = session.scalars(select(QueueEvent)).all()
    assert request is not None
    assert request.reason == "Needs clinician review"
    assert queue_events == []


def test_queue_commit_requires_authorized_exact_active_approval_and_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = setup_assessment(session_factory)
    service = TriageToolsService(session_factory)
    approval_id = approve_request(service, assessment.id)
    data = commit_input(assessment, approval_id, "queue-update-key-0001")

    unauthorized = service.commit_approved_queue_update(data, None)
    missing_approval = service.commit_approved_queue_update(
        data.model_copy(update={"approval_id": uuid4()}),
        clinician(COMMIT_PERMISSION),
    )
    mismatched_clinician = service.commit_approved_queue_update(
        data,
        AuthenticatedClinician(
            subject="another-authenticated-subject",
            reference="SYN-CLINICIAN-OTHER",
            permissions=frozenset({COMMIT_PERMISSION}),
        ),
    )
    committed = service.commit_approved_queue_update(data, clinician(COMMIT_PERMISSION))
    repeated = service.commit_approved_queue_update(data, clinician(COMMIT_PERMISSION))
    conflicting_retry = service.commit_approved_queue_update(
        data.model_copy(update={"queue_name": "SYN-QUEUE-OTHER"}),
        clinician(COMMIT_PERMISSION),
    )
    other_subject_retry = service.commit_approved_queue_update(
        data,
        AuthenticatedClinician(
            subject="another-authenticated-subject",
            reference="SYN-CLINICIAN-TEST",
            permissions=frozenset({COMMIT_PERMISSION}),
        ),
    )

    assert unauthorized.code == "UNAUTHENTICATED"
    assert missing_approval.code == "APPROVAL_NOT_FOUND"
    assert mismatched_clinician.code == "APPROVAL_CLINICIAN_MISMATCH"
    assert committed.status == "committed"
    assert repeated.status == "already_committed"
    assert conflicting_retry.code == "IDEMPOTENCY_KEY_REUSED"
    assert other_subject_retry.code == "IDEMPOTENCY_KEY_REUSED"
    assert repeated.commit_id == committed.commit_id
    with session_factory() as session:
        commits = session.scalars(select(QueueCommit)).all()
        events = session.scalars(select(QueueEvent)).all()
        queue_audit = session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.QUEUE_EVENT.value,
                AuditLog.entity_id == committed.queue_event_id,
            )
        ).one()
        failed_attempt = session.scalar(
            select(ToolExecution).where(ToolExecution.error_code == "UNAUTHENTICATED")
        )
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.entity_id == unauthorized.action_id,
                AuditLog.entity_type == AuditEntityType.TOOL_EXECUTION.value,
            )
        )
    assert len(commits) == 1
    assert len(events) == 1
    assert events[0].event_type == QueueEventType.ENTERED.value
    assert queue_audit.action == "queue.update.committed"
    assert failed_attempt is not None
    assert audit is not None
    assert audit.action == "tool.commit_approved_queue_update.failed"


def test_stale_and_revoked_approvals_are_rejected_and_audited(
    session_factory: sessionmaker[Session],
) -> None:
    assessment = setup_assessment(session_factory)
    service = TriageToolsService(session_factory)
    stale_approval = approve_request(service, assessment.id)
    with session_factory.begin() as session:
        AssessmentRepository(session).create_version(
            assessment.encounter_id,
            AssessmentVersionCreate(
                recommendation="New synthetic recommendation",
                created_by_reference="SYN-SYSTEM-TEST",
            ),
        )
    stale = service.commit_approved_queue_update(
        commit_input(assessment, stale_approval, "stale-approval-key-0001"),
        clinician(COMMIT_PERMISSION),
    )
    latest = setup_new_assessment_for_same_encounter(
        session_factory, assessment.encounter_id
    )
    revoked_approval = approve_request(service, latest.id)
    service.revoke_approval(
        UUID(revoked_approval),
        clinician("triage:approval:revoke"),
    )
    revoked = service.commit_approved_queue_update(
        commit_input(latest, revoked_approval, "revoked-approval-key-0001"),
        clinician(COMMIT_PERMISSION),
    )

    assert stale.code == "STALE_ASSESSMENT"
    assert revoked.code == "APPROVAL_REVOKED"
    with session_factory() as session:
        assert session.scalar(select(QueueCommit)) is None
        assert session.scalars(
            select(ToolExecution).where(ToolExecution.status == "failed")
        ).all()


def setup_new_assessment_for_same_encounter(
    factory: sessionmaker[Session], encounter_id
) -> AssessmentVersion:
    with factory.begin() as session:
        assessment = AssessmentRepository(session).create_version(
            encounter_id,
            AssessmentVersionCreate(
                recommendation="Latest synthetic recommendation",
                created_by_reference="SYN-SYSTEM-TEST",
            ),
        )
        session.flush()
        session.expunge(assessment)
        return assessment


def test_mcp_server_registers_all_tools_with_typed_io_schemas() -> None:
    server = create_mcp_server(TriageToolsService(sessionmaker()))
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert set(tools) == {
        "validate_vitals",
        "evaluate_triage_rules",
        "request_clinician_review",
        "commit_approved_queue_update",
    }
    for tool in tools.values():
        assert tool.output_schema is not None
        assert "$defs" in tool.parameters
        assert "request" in tool.parameters["properties"]
