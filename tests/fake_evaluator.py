from datetime import UTC, datetime
from uuid import uuid4

from triage_tools_mcp.schemas import (
    AuthenticatedClinician,
    EvaluateTriageInput,
    EvaluateTriageOutput,
    TriageFinding,
)


class SyntheticWorkflowEvaluator:
    def evaluate(self, data: EvaluateTriageInput) -> EvaluateTriageOutput:
        return EvaluateTriageOutput(
            status="evaluated",
            assessment_version_id=data.assessment_version_id,
            protocol_version=data.protocol_reference.version,
            findings=[
                TriageFinding(
                    code="synthetic.fixture",
                    summary="Synthetic evaluator fixture finding",
                    severity="info",
                    evidence=["observation:0"],
                )
            ],
            supporting_evidence=["observation:0"],
            evaluated_at=datetime.now(UTC),
            action_id=uuid4(),
        )


class SyntheticWorkflowPrincipalProvider:
    def __call__(self, _request: object) -> AuthenticatedClinician:
        return AuthenticatedClinician(
            subject="synthetic-workflow-clinician",
            reference="SYN-CLINICIAN-WORKFLOW-TEST",
            permissions=frozenset(
                {
                    "triage:workflow:run",
                    "triage:assessment:approve",
                    "triage:queue:commit",
                }
            ),
        )
