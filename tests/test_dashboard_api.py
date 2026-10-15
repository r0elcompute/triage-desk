from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from triage_sentinel import workflow_api
from triage_sentinel.main import app


async def request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=body, headers=headers)


@pytest.mark.anyio
async def test_dashboard_session_fails_closed_without_trusted_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER", raising=False)

    response = await request(
        "GET",
        "/dashboard/session",
        headers={"X-Clinician-Reference": "SYN-CLINICIAN-FORGED"},
    )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_intake_requires_trusted_workflow_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER", raising=False)

    response = await request(
        "POST",
        "/dashboard/intakes",
        body={
            "complaint_code": "presenting.concern",
            "complaint_summary": "Synthetic test intake",
            "observations": [],
        },
    )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_assessment_review_requires_backend_review_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER", "test:provider")
    monkeypatch.setattr(
        workflow_api,
        "_load_principal_provider",
        lambda _reference: lambda _request: {
            "subject": "clinician-test",
            "reference": "SYN-CLINICIAN-TEST",
            "permissions": ["triage:workflow:run"],
        },
    )

    response = await request(
        "POST",
        f"/dashboard/assessments/{uuid4()}/review",
        body={"decision": "accepted"},
    )

    assert response.status_code == 403
    assert "authorized" in response.json()["detail"]


@pytest.mark.anyio
async def test_override_requires_reason_and_reason_code() -> None:
    response = await request(
        "POST",
        f"/dashboard/assessments/{uuid4()}/review",
        body={"decision": "overridden"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_synthetic_intake_evidence_and_override_are_persisted(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_WORKFLOW_PRINCIPAL_PROVIDER", "test:provider")
    monkeypatch.setattr(
        workflow_api,
        "_load_principal_provider",
        lambda _reference: lambda _request: {
            "subject": "clinician-test",
            "reference": "SYN-CLINICIAN-TEST",
            "permissions": [
                "triage:workflow:run",
                "triage:assessment:approve",
            ],
        },
    )
    test_factory = sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(workflow_api, "_session_factory", lambda: test_factory)

    intake = await request(
        "POST",
        "/dashboard/intakes",
        body={
            "patient_reference": "SYN-PATIENT-DASHBOARD-01",
            "complaint_code": "presenting.concern",
            "complaint_summary": "Synthetic concern for dashboard test",
            "observations": [
                {
                    "measurement": "heart_rate",
                    "value": 72,
                    "unit": "bpm",
                    "measured_at": "2026-10-15T12:00:00Z",
                    "source": "clinician",
                    "source_reference": "SYN-DASHBOARD-TEST-01",
                }
            ],
        },
    )

    assert intake.status_code == 201
    encounter_id = intake.json()["encounter_id"]
    assessment_id = intake.json()["assessment_version_id"]
    detail = await request("GET", f"/dashboard/encounters/{encounter_id}")
    assert detail.status_code == 200
    assert detail.json()["observations"][0]["value"] == "72.0000"
    assert detail.json()["assessments"][0]["reviews"] == []

    decision = await request(
        "POST",
        f"/dashboard/assessments/{assessment_id}/review",
        body={
            "decision": "overridden",
            "override_summary": "Synthetic override review reason",
            "override_reason_code": "clinician.review",
        },
    )

    assert decision.status_code == 200
    updated = await request("GET", f"/dashboard/encounters/{encounter_id}")
    assert updated.json()["assessments"][0]["reviews"][0]["decision"] == "overridden"
    assert updated.json()["assessments"][0]["reviews"][0]["override_summary"] == (
        "Synthetic override review reason"
    )
    assert any(
        item["action"] == "clinician.review.overridden"
        for item in updated.json()["audit_history"]
    )