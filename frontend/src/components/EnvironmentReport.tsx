import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, CircleX, Cpu, Download, FileText, ShieldAlert, Smartphone } from 'lucide-react'
import type { Artifact, ExecutionEnvironment, Finding } from '../types'
import { environmentApi, errorMessage } from '../services/api'
import { APPLICATION_LABEL, APPLICATION_TONE, LIMITATION_STATUSES, SCENARIO_TONE, formatValue, parameterLabel } from '../environment'
import './Environment.css'

function ScreenshotTile({ runId, artifact }: { runId: number; artifact: Artifact }) {
  const query = useQuery({ queryKey: ['artifact-blob', runId, artifact.id], queryFn: ({ signal }) => environmentApi.artifactBlob(runId, artifact.id, { signal }), staleTime: Infinity })
  const url = useMemo(() => (query.data ? URL.createObjectURL(query.data) : null), [query.data])
  useEffect(() => () => { if (url) URL.revokeObjectURL(url) }, [url])
  return <figure className="screenshot-tile">
    {url ? <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={`Screenshot ${artifact.filename}`} loading="lazy" /></a> : <div className="skeleton-shot" aria-label={query.error ? 'Screenshot unavailable' : 'Loading screenshot'} />}
    <figcaption title={artifact.filename}>{artifact.filename.replace(/^[0-9a-f]{32}_/, '')}</figcaption>
  </figure>
}

async function downloadArtifact(runId: number, artifact: Artifact) {
  const blob = await environmentApi.artifactBlob(runId, artifact.id)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = artifact.filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 2000)
}

type Props = { runId: number; environment: ExecutionEnvironment; artifacts: Artifact[]; findings?: Finding[] }

export default function EnvironmentReport({ runId, environment: env, artifacts, findings = [] }: Props) {
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const device = (env.effective_configuration?.device ?? {}) as Record<string, unknown>
  const emulator = env.environment_type === 'emulator'
  const limitations = env.conditions.filter((c) => LIMITATION_STATUSES.has(c.application_status))
  const failedConditions = env.conditions.filter((c) => c.application_status === 'failed')
  const screenshots = artifacts.filter((a) => a.artifact_type === 'screenshot')
  const files = artifacts.filter((a) => a.artifact_type !== 'screenshot')
  const finishedCleanly = env.status === 'CLEANED'

  return <div className="page-stack">
    <section className="surface-card results-card">
      <div className="card-heading"><div><div className="eyebrow">EXECUTION ENVIRONMENT</div><h2>{emulator ? 'Disposable Android emulator' : 'Physical Android device'}</h2></div>
        <span className={`env-mode-badge ${env.environment_type}`}>{emulator ? <Cpu size={13} /> : <Smartphone size={13} />}{emulator ? 'Emulator test' : 'Physical Android test'}</span></div>
      <div className="env-facts">
        <div className="env-fact"><span>Lifecycle</span><strong>{env.status.replaceAll('_', ' ').toLowerCase()}</strong></div>
        <div className="env-fact"><span>Outcome</span><strong>{env.outcome ?? 'in progress'}</strong></div>
        <div className="env-fact"><span>{emulator ? 'AVD' : 'Device'}</span><strong title={env.emulator_name ?? undefined}>{env.emulator_name ?? (env.device_id ? `#${env.device_id}` : '—')}</strong></div>
        <div className="env-fact"><span>ADB serial</span><strong>{env.emulator_serial ?? (emulator ? 'not assigned yet' : 'kept on worker')}</strong></div>
        <div className="env-fact"><span>System image</span><strong title={env.system_image ?? undefined}>{env.system_image ?? '—'}</strong></div>
        <div className="env-fact"><span>Actual Android API</span><strong>{String(device.api_level ?? env.android_api_level ?? '—')}</strong></div>
        <div className="env-fact"><span>Actual RAM (MemTotal)</span><strong>{device.mem_total_mb ? `${device.mem_total_mb} MB` : '—'}</strong></div>
        <div className="env-fact"><span>Actual /data capacity</span><strong>{typeof device.storage_total_bytes === 'number' ? `${Math.round(device.storage_total_bytes / 1048576)} MB` : '—'}</strong></div>
      </div>
      {env.status === 'FAILED' || env.outcome === 'failed' ? <div className="env-note bad"><CircleX size={14} /><span>{env.status_message ?? 'The environment failed; see the lifecycle events.'}</span></div> : null}
    </section>

    {limitations.length > 0 && <div className="limitations"><h3><AlertTriangle size={14} /> Requested but NOT applied on this device</h3><ul>
      {limitations.map((c) => <li key={c.id}><b>{parameterLabel(c.parameter_key)}</b> ({formatValue(c.requested_value, c.unit)}) — {APPLICATION_LABEL[c.application_status]}{typeof c.evidence.reason === 'string' ? `: ${c.evidence.reason}` : ''}</li>)}
    </ul></div>}
    {failedConditions.length > 0 && <div className="env-note bad"><CircleX size={14} /><span>Failed verification: {failedConditions.map((c) => parameterLabel(c.parameter_key)).join(', ')}. Results under these conditions are not representative.</span></div>}

    <section className="surface-card results-card">
      <div className="card-heading"><div><div className="eyebrow">REQUESTED VS ACTUAL</div><h2>Applied conditions</h2></div><span className="status-pill neutral">{env.conditions.length} records</span></div>
      {!env.conditions.length ? <div className="compact-empty">No conditions have been applied yet. They appear once the worker reports them.</div> : <div className="metric-table-wrap"><table className="data-table condition-grid"><thead><tr><th>Condition</th><th>Requested</th><th>Applied</th><th>Observed on device</th><th>Status</th><th>Mechanism / reason</th></tr></thead><tbody>
        {env.conditions.map((c) => <tr key={c.id}>
          <td>{parameterLabel(c.parameter_key)}</td><td>{formatValue(c.requested_value, c.unit)}</td><td>{formatValue(c.applied_value, c.unit)}</td><td>{formatValue(c.observed_value, c.unit)}</td>
          <td><span className={`tone-pill ${APPLICATION_TONE[c.application_status]}`}>{APPLICATION_LABEL[c.application_status]}</span></td>
          <td className="reason">{[c.mechanism, typeof c.evidence.reason === 'string' ? c.evidence.reason : null].filter(Boolean).join(' — ') || '—'}</td>
        </tr>)}
      </tbody></table></div>}
    </section>

    <section className="surface-card results-card">
      <div className="card-heading"><div><div className="eyebrow">APP JOURNEYS</div><h2>Scenario outcomes</h2></div><span className="status-pill neutral">{env.scenarios.filter((s) => s.status === 'passed').length}/{env.scenarios.length} passed</span></div>
      <div className="scenario-results">{env.scenarios.map((s) => <details className={`scenario-result ${env.active_run_scenario_id === s.run_scenario_id && s.status === 'running' ? 'active' : ''}`} key={s.run_scenario_id} open={s.status === 'failed'}>
        <summary><span className={`tone-pill ${SCENARIO_TONE[s.status] ?? 'neutral'}`}>{s.status}</span><span>{s.name} · v{s.version}</span>{s.completed_at && s.started_at && <small className="muted">{Math.round((new Date(s.completed_at).getTime() - new Date(s.started_at).getTime()) / 1000)}s</small>}</summary>
        {s.result?.reason && <p>{s.result.reason}</p>}
        {s.result?.steps && <div className="step-list">{s.result.steps.map((step) => <div className="step-row" key={step.index}>
          <span>{step.index + 1}</span><code>{step.action}</code>
          <span className={`expect ${step.expectations.some((e) => !e.passed) ? 'fail' : ''}`}>{step.error ?? (step.expectations.length ? step.expectations.map((e) => `${e.passed ? '✓' : '✗'} ${e.name}: expected ${JSON.stringify(e.expected)}, got ${JSON.stringify(e.actual)}`).join(' · ') : '')}</span>
          <span className="muted">{step.status}</span>
        </div>)}</div>}
      </details>)}{!env.scenarios.length && <div className="compact-empty">No scenarios selected.</div>}</div>
    </section>

    {findings.length > 0 && <section className="surface-card results-card"><div className="card-heading"><div><div className="eyebrow">FAILURES</div><h2>Detected problems</h2></div><span className="status-pill failed">{findings.length}</span></div>
      <div className="finding-list">{findings.map((finding) => <article className="finding-row" key={finding.id}><span className={`severity-icon ${finding.severity}`}><ShieldAlert size={17} /></span><div className="finding-copy"><div className="finding-title-row"><strong>{finding.title}</strong><span className={`severity-pill ${finding.severity}`}>{finding.severity}</span></div><p>{finding.actual_behavior || finding.description || 'No description provided.'}</p><small>Expected: {finding.expected_behavior || '—'}</small></div></article>)}</div>
    </section>}

    <section className="surface-card results-card">
      <div className="card-heading"><div><div className="eyebrow">EVIDENCE</div><h2>Screenshots and logs</h2></div><span className="status-pill neutral">{artifacts.length} files</span></div>
      {screenshots.length ? <div className="screenshot-grid">{screenshots.map((a) => <ScreenshotTile key={a.id} runId={runId} artifact={a} />)}</div> : <div className="compact-empty">No screenshots uploaded yet.</div>}
      {files.length > 0 && <div className="artifact-list">{files.map((a) => <div className="artifact-row" key={a.id}><FileText size={16} /><div><strong>{a.filename.replace(/^[0-9a-f]{32}_/, '')}</strong><small>{a.artifact_type.replaceAll('_', ' ')} · {Math.max(1, Math.round(a.size_bytes / 1024))} KB · SHA-256 {a.sha256.slice(0, 12)}…</small></div>
        <button type="button" className="button button-secondary button-small" onClick={() => { setDownloadError(null); downloadArtifact(runId, a).catch((error: unknown) => setDownloadError(errorMessage(error))) }}><Download size={13} /> Download</button></div>)}</div>}
      {downloadError && <div className="form-error" role="alert">{downloadError}</div>}
    </section>

    <div className={`env-note ${finishedCleanly ? (env.cleanup_verified ? '' : 'warn') : ''}`}>{finishedCleanly && env.cleanup_verified ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}<span>
      {finishedCleanly ? (env.cleanup_verified ? 'Cleanup verified: conditions reverted, emulator stopped, temporary AVD and run files removed.' : 'Cleanup finished but could not be fully verified; check the cleanup event. A restarted worker sweeps orphaned PocketLab AVDs.')
        : env.status === 'CLEANING' ? 'Cleaning up temporary resources…' : 'Temporary resources are released automatically when the run ends, fails or is canceled.'}
    </span></div>
  </div>
}
