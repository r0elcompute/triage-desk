'use client';

import { FormEvent, useEffect, useState } from 'react';

type QueueItem = {
  workflow_id: string;
  encounter_id: string;
  assessment_version_id: string;
  assessment_number: number;
  patient_reference: string;
  complaint: string;
  started_at: string;
  workflow_status: string;
  review_status: string;
  proposed_findings: Finding[];
  protocol_reference: { protocol_id?: string; version?: string } | null;
  error_code: string | null;
  review_request_id: string | null;
};

type Finding = {
  code: string;
  summary: string;
  severity: 'info' | 'warning' | 'urgent';
  evidence: string[];
};

type DashboardSession = {
  authenticated: boolean;
  clinician_reference: string;
  can_run_workflow: boolean;
  can_review: boolean;
  can_commit_queue: boolean;
};

type EncounterDetail = {
  encounter: {
    id: string;
    status: string;
    started_at: string;
    patient_reference: string;
  };
  complaints: { id: string; code: string; summary: string }[];
  observations: {
    id: string;
    measurement: string;
    value: string;
    unit: string;
    measured_at: string;
    source: string;
    source_reference: string | null;
  }[];
  assessments: {
    id: string;
    version: number;
    recommendation: string;
    reviews: {
      id: string;
      decision: string;
      clinician_reference: string;
      reviewed_at: string;
      override_summary: string | null;
      override_reason_code: string | null;
    }[];
  }[];
  workflows: {
    id: string;
    assessment_version_id: string;
    status: string;
    started_at: string;
    validation: {
      valid?: boolean;
      complete?: boolean;
      missing_measurements?: string[];
      issues?: { code: string; path: string; message: string }[];
    };
    evaluation: {
      protocol_version?: string;
      findings?: Finding[];
      supporting_evidence?: string[];
    } | null;
    recommendation: { summary?: string } | null;
    protocol_reference: { protocol_id?: string; version?: string } | null;
    reference_retrieval: { status?: string; error_code?: string };
    error_code: string | null;
    transitions: { state: string; at: string }[];
  }[];
  review_requests: {
    id: string;
    assessment_version_id: string;
    reason: string;
    created_at: string;
    pending: boolean;
  }[];
  audit_history: { action: string; actor_reference: string; occurred_at: string }[];
};

type MeasurementField = {
  measurement: string;
  label: string;
  unit: string;
};

const measurementFields: MeasurementField[] = [
  { measurement: 'heart_rate', label: 'Heart rate', unit: 'bpm' },
  { measurement: 'respiratory_rate', label: 'Respiratory rate', unit: 'breaths/min' },
  { measurement: 'temperature', label: 'Temperature', unit: 'degC' },
  { measurement: 'systolic_blood_pressure', label: 'Systolic pressure', unit: 'mmHg' },
  { measurement: 'diastolic_blood_pressure', label: 'Diastolic pressure', unit: 'mmHg' },
  { measurement: 'oxygen_saturation', label: 'Oxygen saturation', unit: '%' },
];

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, {
    cache: 'no-store',
    ...options,
    headers: { 'content-type': 'application/json', ...options?.headers },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof result.detail === 'string' ? result.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return result as T;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function titleCase(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function Home() {
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<EncounterDetail | null>(null);
  const [clinician, setClinician] = useState<DashboardSession | null>(null);
  const [pageError, setPageError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [view, setView] = useState<'queue' | 'intake'>('queue');
  const [successMessage, setSuccessMessage] = useState('');
  const [patientReference, setPatientReference] = useState('');
  const [complaint, setComplaint] = useState('');
  const [protocolId, setProtocolId] = useState('');
  const [requiredMeasurements, setRequiredMeasurements] = useState<string[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [overrideSummary, setOverrideSummary] = useState('');
  const [overrideReason, setOverrideReason] = useState('');
  const [queueName, setQueueName] = useState('triage-review');

  async function refreshQueue(preferredId?: string) {
    setLoadingQueue(true);
    try {
      const result = await api<QueueItem[]>('dashboard/queue');
      setQueue(result);
      const nextSelected = preferredId ?? selectedId;
      if (nextSelected && result.some((item) => item.encounter_id === nextSelected)) {
        setSelectedId(nextSelected);
      } else if (!selectedId && result.length) {
        setSelectedId(result[0].encounter_id);
      }
      setPageError('');
    } catch (error) {
      setPageError(error instanceof Error ? error.message : 'Queue could not be loaded.');
    } finally {
      setLoadingQueue(false);
    }
  }

  useEffect(() => {
    let active = true;
    api<QueueItem[]>('dashboard/queue')
      .then((result) => {
        if (!active) return;
        setQueue(result);
        setSelectedId((current) => current ?? result[0]?.encounter_id ?? null);
        setPageError('');
      })
      .catch((error: unknown) => {
        if (active) setPageError(error instanceof Error ? error.message : 'Queue could not be loaded.');
      })
      .finally(() => {
        if (active) setLoadingQueue(false);
      });
    api<DashboardSession>('dashboard/session')
      .then((result) => { if (active) setClinician(result); })
      .catch(() => { if (active) setClinician(null); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    api<EncounterDetail>(`dashboard/encounters/${selectedId}`)
      .then((result) => {
        if (active) {
          setDetail(result);
          setDetailError('');
        }
      })
      .catch((error: unknown) => {
        if (active) setDetailError(error instanceof Error ? error.message : 'Encounter could not be loaded.');
      })
      .finally(() => {
        if (active) setLoadingDetail(false);
      });
    return () => {
      active = false;
    };
  }, [selectedId]);

  async function submitIntake(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!clinician?.can_run_workflow) {
      setPageError('An authenticated clinician with workflow permission is required.');
      return;
    }
    setSubmitting(true);
    setPageError('');
    setSuccessMessage('');
    try {
      const observations = measurementFields.flatMap((field) => {
        const value = values[field.measurement]?.trim();
        if (!value) return [];
        return [{
          measurement: field.measurement,
          value,
          unit: field.unit,
          measured_at: new Date().toISOString(),
          source: 'clinician',
          source_reference: `SYN-DASH-${crypto.randomUUID().replaceAll('-', '').slice(0, 16).toUpperCase()}`,
        }];
      });
      const intake = await api<{
        patient_reference: string;
        encounter_id: string;
        assessment_version_id: string;
      }>('dashboard/intakes', {
        method: 'POST',
        body: JSON.stringify({
          patient_reference: patientReference.trim() || undefined,
          complaint_code: 'presenting.concern',
          complaint_summary: complaint,
          observations,
        }),
      });
      const workflow = await api<{ status: string; error_code?: string }>('workflows/triage', {
        method: 'POST',
        body: JSON.stringify({
          encounter_id: intake.encounter_id,
          assessment_version_id: intake.assessment_version_id,
          protocol_id: protocolId.trim(),
          observations,
          required_measurements: requiredMeasurements,
        }),
      });
      setSuccessMessage(
        `Encounter created for ${intake.patient_reference}. Workflow status: ${titleCase(workflow.status)}${workflow.error_code ? ` (${workflow.error_code})` : ''}.`,
      );
      setPatientReference('');
      setComplaint('');
      setValues({});
      await refreshQueue(intake.encounter_id);
      setView('queue');
    } catch (error) {
      setPageError(error instanceof Error ? error.message : 'Intake could not be submitted.');
      await refreshQueue();
    } finally {
      setSubmitting(false);
    }
  }

  const selectedItem = queue.find((item) => item.encounter_id === selectedId);
  const currentWorkflow = detail?.workflows[0];
  const currentAssessment = detail?.assessments[0];
  const outstandingRequests = detail?.review_requests.filter((item) => item.pending) ?? [];
  const canApprove = Boolean(
    clinician?.can_review &&
      clinician.can_commit_queue &&
      selectedItem?.workflow_status === 'pending_review' &&
      currentWorkflow?.status === 'pending_review' &&
      currentWorkflow.validation.valid &&
      currentWorkflow.validation.complete &&
      currentWorkflow.evaluation &&
      currentWorkflow.recommendation &&
      currentWorkflow.reference_retrieval.status === 'succeeded' &&
      !currentWorkflow.error_code &&
      selectedItem.review_request_id,
  );

  async function recordDecision(decision: 'accepted' | 'rejected' | 'overridden') {
    const assessment = detail?.assessments[0];
    if (!assessment || !clinician?.can_review) return;
    setSubmitting(true);
    setPageError('');
    try {
      if (decision === 'accepted') {
        if (!selectedItem || !currentWorkflow || !canApprove) {
          throw new Error('This workflow is not eligible for protected approval.');
        }
        await api(`workflows/${selectedItem.workflow_id}/approve`, {
          method: 'POST',
          body: JSON.stringify({
            assessment_number: assessment.version,
            queue_name: queueName.trim(),
            idempotency_key: `triage-ui:${selectedItem.workflow_id}`,
          }),
        });
        setSuccessMessage(`Recommendation approved and committed to ${queueName.trim()}.`);
      } else {
        await api(`dashboard/assessments/${assessment.id}/review`, {
          method: 'POST',
          body: JSON.stringify(
            decision === 'overridden'
              ? { decision, override_summary: overrideSummary, override_reason_code: overrideReason }
              : { decision },
          ),
        });
        setSuccessMessage(`Clinician decision recorded: ${titleCase(decision)}.`);
      }
      setOverrideSummary('');
      setOverrideReason('');
      await refreshQueue(selectedId ?? undefined);
      if (selectedId) {
        const updated = await api<EncounterDetail>(`dashboard/encounters/${selectedId}`);
        setDetail(updated);
      }
    } catch (error) {
      setPageError(error instanceof Error ? error.message : 'Decision could not be recorded.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Triage Sentinel home">
          <span className="brand-mark" aria-hidden="true">TS</span>
          <span><strong>TRIAGE</strong><small>SENTINEL</small></span>
        </a>
        <div className="topbar-status">
          <span className={`status-dot ${clinician ? 'status-dot--ready' : ''}`} />
          <span>{clinician ? clinician.clinician_reference : 'Clinician session unavailable'}</span>
        </div>
        <div className="topbar-meta"><span>CLINICAL OPERATIONS</span><span>·</span><span>SYNTHETIC DATA</span></div>
      </header>

      <section className="workspace-heading">
        <div>
          <p className="eyebrow">TRIAGE WORKSPACE / 08</p>
          <h1>Review desk</h1>
        </div>
        <div className="heading-actions">
          <span className="protocol-note">Priorities and rules come from the configured backend protocol.</span>
          <button className="button button--primary" type="button" onClick={() => { setView('intake'); setSuccessMessage(''); }}>
            New synthetic intake <span aria-hidden="true">+</span>
          </button>
        </div>
      </section>

      {pageError && <div className="alert alert--error" role="alert">{pageError}</div>}
      {successMessage && <div className="alert alert--success" role="status">{successMessage}</div>}

      {view === 'intake' ? (
        <section className="intake-layout" aria-labelledby="intake-title">
          <div className="section-heading">
            <div><p className="eyebrow">NEW ENCOUNTER</p><h2 id="intake-title">Synthetic intake</h2></div>
            <button className="button button--quiet" type="button" onClick={() => setView('queue')}>Back to queue</button>
          </div>
          {!clinician?.can_run_workflow && (
            <div className="alert alert--warning" role="status">
              Workflow actions are locked until the backend confirms an authenticated clinician with workflow permission.
            </div>
          )}
          <form className="intake-form" onSubmit={submitIntake}>
            <div className="form-columns">
              <label className="field">
                <span>Patient reference <small>Optional; generated if blank</small></span>
                <input value={patientReference} onChange={(event) => setPatientReference(event.target.value)} placeholder="SYN-PATIENT-..." pattern="SYN-[A-Z0-9][A-Z0-9-]{1,63}" />
              </label>
              <label className="field">
                <span>Protocol reference ID</span>
                <input value={protocolId} onChange={(event) => setProtocolId(event.target.value)} required placeholder="Configured protocol ID" />
              </label>
            </div>
            <label className="field">
              <span>Presenting concern</span>
              <textarea value={complaint} onChange={(event) => setComplaint(event.target.value)} required maxLength={500} rows={3} placeholder="Record the synthetic presenting concern" />
            </label>
            <div className="measurements-heading">
              <div><h3>Observations</h3><p>Enter supplied values only. Required measurements are passed to backend validation.</p></div>
              <span>{Object.values(values).filter(Boolean).length} recorded</span>
            </div>
            <div className="measurement-grid">
              {measurementFields.map((field) => (
                <div className="measurement-row" key={field.measurement}>
                  <label className="field field--measurement">
                    <span>{field.label}<small>{field.unit}</small></span>
                    <input
                      inputMode="decimal"
                      value={values[field.measurement] ?? ''}
                      onChange={(event) => setValues((current) => ({ ...current, [field.measurement]: event.target.value }))}
                      placeholder="Not recorded"
                    />
                  </label>
                  <label className="required-toggle">
                    <input
                      type="checkbox"
                      checked={requiredMeasurements.includes(field.measurement)}
                      onChange={(event) => setRequiredMeasurements((current) => event.target.checked
                        ? [...current, field.measurement]
                        : current.filter((item) => item !== field.measurement))}
                    />
                    <span>Required</span>
                  </label>
                </div>
              ))}
            </div>
            <div className="form-footer">
              <p>Intake is persisted by the backend. A protocol evaluation may remain pending if the configured protocol or services are unavailable.</p>
              <button className="button button--primary" type="submit" disabled={submitting || !clinician?.can_run_workflow || requiredMeasurements.length === 0}>
                {submitting ? 'Submitting…' : 'Create encounter and run workflow'}
              </button>
            </div>
          </form>
        </section>
      ) : (
        <div className="desk-layout">
          <aside className="queue-panel" aria-label="Triage queue">
            <div className="queue-heading">
              <div><p className="eyebrow">ACTIVE WORK</p><h2>Queue <span>{queue.length}</span></h2></div>
              <button className="refresh-button" type="button" onClick={() => void refreshQueue()} aria-label="Refresh queue" title="Refresh queue">↻</button>
            </div>
            {loadingQueue ? (
              <div className="empty-state"><span className="spinner" />Loading queue</div>
            ) : queue.length === 0 ? (
              <div className="empty-state"><span className="empty-mark">—</span><strong>No assessments yet</strong><span>New synthetic intakes appear here after a backend workflow run.</span></div>
            ) : (
              <ul className="queue-list">
                {queue.map((item) => {
                  const topFinding = item.proposed_findings[0];
                  return (
                    <li key={item.workflow_id}>
                      <button className={`queue-item ${selectedId === item.encounter_id ? 'queue-item--selected' : ''}`} type="button" onClick={() => setSelectedId(item.encounter_id)}>
                        <span className="queue-item-top"><strong>{item.patient_reference}</strong><span className={`review-badge review-badge--${item.review_status}`}>{titleCase(item.review_status)}</span></span>
                        <span className="queue-complaint">{item.complaint}</span>
                        <span className="queue-item-bottom">
                          <span>{topFinding ? `${titleCase(topFinding.severity)} finding` : item.error_code ? 'Evaluation unavailable' : 'No proposed finding'}</span>
                          <time>{formatDate(item.started_at)}</time>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="queue-legend"><span className="legend-swatch legend-swatch--proposed" /> Proposed by protocol <span className="legend-swatch legend-swatch--decision" /> Clinician decision</div>
          </aside>

          <section className="detail-panel" aria-label="Assessment detail">
            {!selectedId ? (
              <div className="detail-empty"><span className="detail-number">01</span><h2>Select an encounter</h2><p>Assessment evidence and clinician decisions will appear here.</p></div>
            ) : detailError ? (
              <div className="alert alert--error" role="alert">{detailError}</div>
            ) : loadingDetail || detail?.encounter.id !== selectedId ? (
              <div className="empty-state detail-loading"><span className="spinner" />Loading encounter record</div>
            ) : detail && currentAssessment ? (
              <>
                <div className="detail-header">
                  <div><p className="eyebrow">ENCOUNTER / {detail.encounter.id.slice(0, 8)}</p><h2>{detail.encounter.patient_reference}</h2><p className="detail-subtitle">{detail.complaints[0]?.summary ?? 'No presenting concern'} <span>·</span> Opened {formatDate(detail.encounter.started_at)}</p></div>
                  <span className="version-chip">ASSESSMENT V{currentAssessment.version}</span>
                </div>

                {selectedItem?.error_code && <div className="alert alert--warning" role="status">Backend workflow stopped safely: {selectedItem.error_code}. Review the available evidence; no recommendation was approved.</div>}

                <div className="decision-strip">
                  <div className="decision-block decision-block--proposed"><span className="decision-label">PROPOSED / BACKEND</span><strong>{currentWorkflow?.recommendation?.summary ?? 'No proposed recommendation available'}</strong><small>{currentWorkflow?.protocol_reference?.protocol_id ? `${currentWorkflow.protocol_reference.protocol_id} · ${currentWorkflow.protocol_reference.version ?? 'version unavailable'}` : 'Protocol reference unavailable'}</small></div>
                  <div className="decision-block decision-block--approved"><span className="decision-label">CLINICIAN DECISION</span><strong>{currentAssessment.reviews[0] ? titleCase(currentAssessment.reviews[0].decision) : 'Not reviewed'}</strong><small>{currentAssessment.reviews[0] ? `${currentAssessment.reviews[0].clinician_reference} · ${formatDate(currentAssessment.reviews[0].reviewed_at)}` : 'No approval or override recorded'}</small></div>
                </div>

                <div className="detail-grid">
                  <section className="detail-section evidence-section">
                    <div className="section-heading section-heading--compact"><div><p className="eyebrow">BEFORE DECISION</p><h3>Supporting evidence</h3></div><span className="count-label">{detail.observations.length} observations</span></div>
                    {currentWorkflow?.validation?.missing_measurements?.length ? (
                      <div className="observation-alert"><strong>Missing observations</strong><span>{currentWorkflow.validation.missing_measurements.map(titleCase).join(', ')}</span></div>
                    ) : currentWorkflow?.validation?.complete ? (
                      <div className="observation-good">Required observations recorded</div>
                    ) : (
                      <div className="observation-alert"><strong>Completeness unknown</strong><span>Backend validation did not return a complete result.</span></div>
                    )}
                    <div className="observation-list">
                      {detail.observations.map((observation) => (
                        <div className="observation-row" key={observation.id}>
                          <div><strong>{titleCase(observation.measurement)}</strong><small>{observation.source} · {observation.source_reference ?? 'No source reference'}</small></div>
                          <div className="observation-value"><strong>{observation.value}</strong><span>{observation.unit}</span></div>
                          <div className="observation-time"><time>{formatDate(observation.measured_at)}</time><span className="freshness-unknown">Freshness rule not returned</span></div>
                        </div>
                      ))}
                      {detail.observations.length === 0 && <p className="muted-copy">No observations were recorded for this encounter.</p>}
                    </div>
                    {currentWorkflow?.validation?.issues?.map((issue, index) => <p className="validation-issue" key={`${issue.path}-${index}`}>{issue.path}: {issue.message}</p>)}
                  </section>

                  <section className="detail-section findings-section">
                    <div className="section-heading section-heading--compact"><div><p className="eyebrow">PROTOCOL OUTPUT</p><h3>Findings & rule references</h3></div></div>
                    {currentWorkflow?.evaluation?.findings?.length ? currentWorkflow.evaluation.findings.map((finding) => (
                      <article className="finding-row" key={finding.code}>
                        <span className={`severity-mark severity-mark--${finding.severity}`} />
                        <div><div className="finding-title"><strong>{finding.summary}</strong><span>{titleCase(finding.severity)}</span></div><code>{finding.code}</code>{finding.evidence.map((evidence, index) => <p key={`${finding.code}-${index}`}>{evidence}</p>)}</div>
                      </article>
                    )) : <p className="muted-copy">No backend findings are available for this workflow.</p>}
                    <div className="reference-line"><span>Selected protocol</span><strong>{currentWorkflow?.protocol_reference?.protocol_id ?? 'Unavailable'}</strong><small>{currentWorkflow?.evaluation?.protocol_version ?? 'No evaluated version'}</small></div>
                  </section>
                </div>

                {outstandingRequests.length > 0 && (
                  <section className="request-section">
                    <div className="section-heading section-heading--compact"><div><p className="eyebrow">ATTENTION</p><h3>Pending reassessment requests</h3></div><span className="count-label">{outstandingRequests.length} pending</span></div>
                    {outstandingRequests.map((item) => <div className="request-row" key={item.id}><span className="pending-dot" /><p>{item.reason}</p><time>{formatDate(item.created_at)}</time></div>)}
                  </section>
                )}

                <section className="review-controls">
                  <div className="section-heading section-heading--compact"><div><p className="eyebrow">CLINICIAN CONTROL</p><h3>Record a decision</h3></div><span className="auth-note">{clinician?.can_review ? `Verified: ${clinician.clinician_reference}` : 'Authenticated reviewer required'}</span></div>
                  {currentAssessment.reviews.length > 0 && <div className="prior-decision">Latest decision: <strong>{titleCase(currentAssessment.reviews[0].decision)}</strong>{currentAssessment.reviews[0].override_summary && <span> · {currentAssessment.reviews[0].override_summary}</span>}</div>}
                  <div className="queue-approval-row">
                    <label className="field"><span>Queue destination</span><input value={queueName} onChange={(event) => setQueueName(event.target.value)} pattern="[A-Za-z0-9][A-Za-z0-9._:-]*" maxLength={64} /></label>
                    <button className="button button--approve" type="button" disabled={submitting || !canApprove || !queueName.trim()} onClick={() => void recordDecision('accepted')}>Approve & commit</button>
                  </div>
                  <div className="review-actions">
                    <button className="button button--quiet" type="button" disabled={submitting || !clinician?.can_review} onClick={() => void recordDecision('rejected')}>Reject</button>
                  </div>
                  <div className="override-form">
                    <label className="field"><span>Override reason</span><input value={overrideSummary} onChange={(event) => setOverrideSummary(event.target.value)} maxLength={2000} placeholder="Required to record an override" /></label>
                    <label className="field"><span>Reason code</span><input value={overrideReason} onChange={(event) => setOverrideReason(event.target.value)} maxLength={64} pattern="[A-Za-z0-9][A-Za-z0-9._:-]*" placeholder="e.g. clinician.review" /></label>
                    <button className="button button--override" type="button" disabled={submitting || !clinician?.can_review || !overrideSummary.trim() || !overrideReason.trim()} onClick={() => void recordDecision('overridden')}>Record override</button>
                  </div>
                  <p className="review-note">Approval uses the protected backend workflow endpoint and commits only to the named queue. The backend does not derive a new priority. Rejections and overrides are recorded without queue mutation.</p>
                </section>

                <section className="history-section">
                  <div className="section-heading section-heading--compact"><div><p className="eyebrow">RECORD</p><h3>Assessment & audit history</h3></div><span className="count-label">{detail.audit_history.length} events</span></div>
                  <div className="history-list">
                    {detail.assessments.map((assessment) => <div className="history-row" key={assessment.id}><span className="history-marker" /><div><strong>Assessment version {assessment.version}</strong><small>{assessment.recommendation}</small></div><span>{assessment.reviews.length ? `${assessment.reviews.length} clinician decision(s)` : 'No decision'}</span></div>)}
                    {detail.workflows.flatMap((workflow) => workflow.transitions.map((transition, index) => <div className="history-row" key={`${workflow.id}-${index}`}><span className="history-marker history-marker--workflow" /><div><strong>{titleCase(transition.state)}</strong><small>Workflow {workflow.id.slice(0, 8)}</small></div><time>{formatDate(transition.at)}</time></div>))}
                    {detail.audit_history.map((event, index) => <div className="history-row" key={`${event.action}-${event.occurred_at}-${index}`}><span className="history-marker history-marker--audit" /><div><strong>{titleCase(event.action)}</strong><small>{event.actor_reference}</small></div><time>{formatDate(event.occurred_at)}</time></div>)}
                  </div>
                </section>
              </>
            ) : (
              <div className="detail-empty"><h2>Encounter unavailable</h2><p>Refresh the queue or select another record.</p></div>
            )}
          </section>
        </div>
      )}
      <footer className="workspace-footer"><span>TRIAGE SENTINEL</span><span>Recommendations remain proposed until clinician review.</span></footer>
    </main>
  );
}
