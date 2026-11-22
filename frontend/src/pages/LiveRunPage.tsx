import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, CircleAlert, Cpu, FileSearch, RefreshCw, Smartphone, StopCircle } from 'lucide-react'
import { environmentApi, errorMessage, findingsApi, testRunsApi } from '../services/api'
import type { Metric, TestRun } from '../types'
import { MEASUREMENT_TONE } from '../environment'
import PageHeader from '../components/PageHeader'
import ThoughtLine from '../components/ThoughtLine'
import EnvironmentReport from '../components/EnvironmentReport'
import { ErrorPanel, LoadingPanel } from '../components/Feedback'
import '../components/Environment.css'

const terminalStatuses = new Set(['completed', 'failed', 'canceled'])

export default function LiveRunPage() {
  const { runId } = useParams()
  const id = Number(runId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const runQuery = useQuery({
    queryKey: ['runs', id],
    queryFn: ({ signal }) => testRunsApi.get(id, { signal }),
    refetchInterval: (query) => query.state.data && terminalStatuses.has(query.state.data.status) ? false : 3000,
    refetchIntervalInBackground: false,
  })
  const run = runQuery.data
  const environmentQuery = useQuery({
    queryKey: ['runs', id, 'environment'],
    queryFn: ({ signal }) => environmentApi.get(id, { signal }),
    refetchInterval: (query) => query.state.data === null || query.state.data?.status === 'CLEANED' ? false : 3000,
    refetchIntervalInBackground: false,
  })
  const environment = environmentQuery.data
  const active = Boolean(run && (!terminalStatuses.has(run.status) || (environment && environment.status !== 'CLEANED')))
  const eventsQuery = useQuery({
    queryKey: ['runs', id, 'environment', 'events'],
    queryFn: ({ signal }) => environmentApi.events(id, { signal }),
    enabled: Boolean(environment),
    refetchInterval: active ? 3000 : false,
    refetchIntervalInBackground: false,
  })
  const metricsQuery = useQuery({
    queryKey: ['runs', id, 'metrics'],
    queryFn: ({ signal }) => testRunsApi.metrics(id, { signal, limit: 500 }),
    refetchInterval: active ? 5000 : false,
    refetchIntervalInBackground: false,
    refetchOnMount: 'always',
  })
  const artifactsQuery = useQuery({ queryKey: ['runs', id, 'artifacts'], queryFn: ({ signal }) => testRunsApi.artifacts(id, { signal }), refetchInterval: active ? 8000 : false, refetchIntervalInBackground: false })
  const findingsQuery = useQuery({ queryKey: ['runs', id, 'findings'], queryFn: ({ signal }) => findingsApi.listForRun(id, { signal }), refetchInterval: active ? 8000 : false, refetchIntervalInBackground: false })
  const latestMetrics = (metricsQuery.data ?? []).reduce<Map<string, Metric>>((latest, metric) => {
    const previous = latest.get(metric.parameter_key)
    if (!previous || metric.observed_at > previous.observed_at) latest.set(metric.parameter_key, metric)
    return latest
  }, new Map()).values()
  const metricRows = [...latestMetrics].sort((a, b) => a.parameter_key.localeCompare(b.parameter_key))
  const cancelRun = useMutation({
    mutationFn: () => testRunsApi.cancel(id),
    onSuccess: async (updated) => {
      queryClient.setQueryData<TestRun>(['runs', id], updated)
      await queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })
  const terminal = run && terminalStatuses.has(run.status)
  const cancelPending = environment?.status === 'CANCEL_REQUESTED' || Boolean(environment?.cancel_requested_at && environment.status !== 'CLEANED')
  const modeLabel = environment ? (environment.environment_type === 'emulator' ? 'Emulator test' : 'Physical Android test') : run?.execution_mode === 'actual_device' ? 'Actual device (legacy runner)' : 'Legacy runner queue'

  return <div className="page-stack"><PageHeader eyebrow={`TEST RUN · #${runId}`} title="Live test monitoring" description={environment ? 'Every stage below is confirmed by the Android execution worker; nothing is animated ahead of the backend.' : 'Status is polled from the backend.'} action={<button className="button button-secondary" onClick={() => { void Promise.all([runQuery.refetch(), environmentQuery.refetch(), eventsQuery.refetch()]) }} disabled={runQuery.isFetching}><RefreshCw size={15} /> Refresh</button>} />
    {runQuery.isLoading ? <LoadingPanel message="Fetching test status..." /> : runQuery.error && !run ? <ErrorPanel message={errorMessage(runQuery.error)} retry={() => void runQuery.refetch()} /> : run ? <>
      {runQuery.error && <div className="info-banner" role="status"><CircleAlert size={16} /><span>{errorMessage(runQuery.error)} Showing the last status confirmed by the backend; polling will retry.</span></div>}
      {cancelRun.error && <ErrorPanel message={errorMessage(cancelRun.error)} retry={() => cancelRun.reset()} />}
      <section className="surface-card live-run-banner"><div className={`run-status-large ${run.status}`}><span className={`run-status-dot ${run.status}`} /></div><div className="live-run-main"><div className="live-run-title-row"><h2>Run #{run.id}</h2><span className={`status-pill ${run.status}`}>{run.status}</span>
        <span className={`env-mode-badge ${environment ? environment.environment_type : 'legacy'}`}>{environment?.environment_type === 'physical_device' ? <Smartphone size={13} /> : <Cpu size={13} />}{modeLabel}</span></div>
        <p>Project #{run.project_id}{run.app_build_id ? ` · Build #${run.app_build_id}` : ' · No build selected'}{environment ? ` · environment ${environment.status.replaceAll('_', ' ').toLowerCase()}` : ''}</p>
        <div className="run-meta"><span>Created {new Date(run.created_at).toLocaleString()}</span><span>{run.retry_count} worker attempt{run.retry_count === 1 ? '' : 's'}</span><span>{runQuery.isFetching ? 'Refreshing status…' : `Confirmed ${new Date(runQuery.dataUpdatedAt).toLocaleTimeString()}`}</span></div></div>
        <div className="live-run-actions">{!terminal && <button className="button button-secondary button-danger" onClick={() => cancelRun.mutate()} disabled={cancelRun.isPending || cancelPending}><StopCircle size={15} /> {cancelPending ? 'Stopping & cleaning up…' : cancelRun.isPending ? 'Canceling...' : 'Cancel run'}</button>}<Link className="button button-primary" to={`/runs/${run.id}`}><FileSearch size={15} /> Run details</Link></div></section>
      {environmentQuery.error && <div className="form-error" role="status">{errorMessage(environmentQuery.error)} · Environment details could not be refreshed.</div>}
      <QueueWaitNotice environment={environment} />
      <ThoughtLine run={run} environment={environment} events={eventsQuery.data ?? []} />
      {environment && <EnvironmentReport runId={run.id} environment={environment} artifacts={artifactsQuery.data ?? []} findings={findingsQuery.data ?? []} />}
      <section className="surface-card results-card"><div className="card-heading"><div><div className="eyebrow">PHONE AND NETWORK READINGS</div><h2>Latest measurements</h2></div><span className="status-pill neutral">{metricsQuery.isFetching ? 'Refreshing' : `${metricsQuery.data?.length ?? 0} samples`}</span></div>
        {metricsQuery.error ? <div className="form-error" role="status">{errorMessage(metricsQuery.error)} · Showing measurements from the last successful response.</div> : metricsQuery.isLoading ? <LoadingPanel message="Collecting measurements..." /> : !metricRows.length ? <div className="compact-empty">No measurement collected yet. Values appear only after the worker measures them.</div> : <div className="metric-table-wrap"><table className="data-table"><thead><tr><th>Measurement</th><th>Value</th><th>Unit</th><th>Status</th><th>Source</th><th>Method</th><th>Observed at</th></tr></thead><tbody>{metricRows.map((metric) => <tr key={metric.id}><td>{metric.parameter_key}</td><td>{metricValue(metric)}</td><td>{metric.unit || '—'}</td><td>{metric.measurement_status ? <span className={`tone-pill ${MEASUREMENT_TONE[metric.measurement_status]}`}>{metric.measurement_status}</span> : <span className={`status-pill ${metric.validity_status}`}>{metric.validity_status}</span>}</td><td>{metric.source}</td><td title={metric.collection_method ?? undefined}>{metric.collection_method ? metric.collection_method.slice(0, 48) : '—'}</td><td>{new Date(metric.observed_at).toLocaleTimeString()}</td></tr>)}</tbody></table></div>}
      </section>
      {run.status === 'completed' && <div className="run-result-note success"><CheckCircle2 size={17} /><span>The worker completed execution and cleanup. Completion does not mean every scenario passed — see the scenario outcomes above.</span></div>}{run.status === 'failed' && <div className="run-result-note failure"><CircleAlert size={17} /><span>{run.error_message || 'The backend marked this run as failed.'}</span></div>}
      <div className="live-next-actions"><span>When this run finishes, inspect the collected evidence.</span><Link to={`/runs/${run.id}`}>Open run report <ArrowLeft size={15} /></Link></div>
    </> : <ErrorPanel message="This test run could not be found." retry={() => navigate('/test-lab')} />}
  </div>
}

function QueueWaitNotice({ environment }: { environment?: { status: string; created_at: string; updated_at: string } | null }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (environment?.status !== 'QUEUED') return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [environment?.status])
  if (environment?.status !== 'QUEUED') return null
  const queuedAt = new Date(environment.updated_at || environment.created_at).getTime()
  const seconds = Math.max(0, Math.floor((now - queuedAt) / 1000))
  if (!Number.isFinite(queuedAt) || seconds < 60) return null
  const waited = `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`
  return <div className="info-banner" role="status" aria-live="polite"><CircleAlert size={16} /><span>This run has waited {waited} for an Android worker. Keep <code>python -m app.workers.emulator_runner</code> running on the machine with the SDK; the run will begin when that worker claims it. You can cancel this queued run if the worker is offline.</span></div>
}

function metricValue(metric: Metric) {
  const reason = typeof metric.metadata_json?.reason === 'string' ? metric.metadata_json.reason : null
  if (metric.validity_status === 'unsupported') return reason ? `Not supported — ${reason}` : 'Not supported by this device'
  if (metric.validity_status === 'unavailable') return reason ? `Unavailable — ${reason}` : 'No measurement collected'
  if (metric.validity_status === 'invalid') return 'Invalid measurement'
  return metric.numeric_value ?? metric.text_value ?? (metric.boolean_value == null ? 'No measurement collected' : String(metric.boolean_value))
}
