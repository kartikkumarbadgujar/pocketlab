import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, CircleAlert, FileSearch, RefreshCw, StopCircle } from 'lucide-react'
import { errorMessage, testRunsApi } from '../services/api'
import type { Metric, TestRun } from '../types'
import PageHeader from '../components/PageHeader'
import ThoughtLine from '../components/ThoughtLine'
import { ErrorPanel, LoadingPanel } from '../components/Feedback'

const terminalStatuses = new Set(['completed', 'failed', 'canceled'])

export default function LiveRunPage() {
  const { runId } = useParams()
  const id = Number(runId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const runQuery = useQuery({
    queryKey: ['runs', id],
    queryFn: ({ signal }) => testRunsApi.get(id, { signal }),
    refetchInterval: (query) => query.state.data && terminalStatuses.has(query.state.data.status) ? false : 2500,
    refetchIntervalInBackground: false,
  })
  const run = runQuery.data
  const metricsQuery = useQuery({
    queryKey: ['runs', id, 'metrics'],
    queryFn: ({ signal }) => testRunsApi.metrics(id, { signal, limit: 500 }),
    refetchInterval: run?.status === 'running' ? 2500 : false,
    refetchIntervalInBackground: false,
    refetchOnMount: 'always',
  })
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

  return <div className="page-stack"><PageHeader eyebrow={`TEST RUN · #${runId}`} title="Live test monitoring" description="Status is polled from the backend; detailed device-stage events are not exposed by the current runner API." action={<button className="button button-secondary" onClick={() => void runQuery.refetch()} disabled={runQuery.isFetching}><RefreshCw size={15} /> Refresh</button>} />
    {runQuery.isLoading ? <LoadingPanel message="Fetching test status..." /> : runQuery.error && !run ? <ErrorPanel message={errorMessage(runQuery.error)} retry={() => void runQuery.refetch()} /> : run ? <>
      {runQuery.error && <div className="info-banner" role="status"><CircleAlert size={16} /><span>{errorMessage(runQuery.error)} Showing the last status confirmed by the backend; polling will retry.</span></div>}
      {cancelRun.error && <ErrorPanel message={errorMessage(cancelRun.error)} retry={() => cancelRun.reset()} />}
      <section className="surface-card live-run-banner"><div className={`run-status-large ${run.status}`}><span className={`run-status-dot ${run.status}`} /></div><div className="live-run-main"><div className="live-run-title-row"><h2>Run #{run.id}</h2><span className={`status-pill ${run.status}`}>{run.status}</span></div><p>Project #{run.project_id}{run.app_build_id ? ` · Build #${run.app_build_id}` : ' · No build selected'} · {run.execution_mode === 'actual_device' ? 'Actual device' : 'Runner queue'}</p><div className="run-meta"><span>Created {new Date(run.created_at).toLocaleString()}</span><span>{run.retry_count} runner attempt{run.retry_count === 1 ? '' : 's'}</span><span>{runQuery.isFetching ? 'Refreshing status…' : `Confirmed ${new Date(runQuery.dataUpdatedAt).toLocaleTimeString()}`}</span></div></div><div className="live-run-actions">{!terminal && <button className="button button-secondary button-danger" onClick={() => cancelRun.mutate()} disabled={cancelRun.isPending}><StopCircle size={15} /> {cancelRun.isPending ? 'Canceling...' : 'Cancel run'}</button>}<Link className="button button-primary" to={`/runs/${run.id}`}><FileSearch size={15} /> Run details</Link></div></section>
      <ThoughtLine run={run} />
      <section className="surface-card results-card"><div className="card-heading"><div><div className="eyebrow">PHONE AND NETWORK READINGS</div><h2>Latest runner measurements</h2></div><span className="status-pill neutral">{metricsQuery.isFetching ? 'Refreshing' : `${metricsQuery.data?.length ?? 0} samples`}</span></div>
        {metricsQuery.error ? <div className="form-error" role="status">{errorMessage(metricsQuery.error)} · Showing measurements from the last successful response.</div> : metricsQuery.isLoading ? <LoadingPanel message="Collecting measurements..." /> : !metricRows.length ? <div className="compact-empty">No measurement collected. The runner has not uploaded metric samples for this run.</div> : <div className="metric-table-wrap"><table className="data-table"><thead><tr><th>Measurement</th><th>Observed value</th><th>Unit</th><th>Source</th><th>Device</th><th>Observed at</th></tr></thead><tbody>{metricRows.map((metric) => <tr key={metric.id}><td>{metric.parameter_key}</td><td>{metricValue(metric)}</td><td>{metric.unit || '—'}</td><td>{metric.source}</td><td>{metric.device_id == null ? 'Unavailable' : `#${metric.device_id}`}</td><td>{new Date(metric.observed_at).toLocaleString()}</td></tr>)}</tbody></table></div>}
      </section>
      {run.status === 'completed' && <div className="run-result-note success"><CheckCircle2 size={17} /><span>The runner reported completion. Review the run’s measurements and findings for outcomes; completion does not itself mean every scenario passed.</span></div>}{run.status === 'failed' && <div className="run-result-note failure"><CircleAlert size={17} /><span>{run.error_message || 'The backend marked this run as failed.'}</span></div>}
      <div className="live-next-actions"><span>When this run finishes, inspect the collected evidence.</span><Link to={`/runs/${run.id}`}>Open run report <ArrowLeft size={15} /></Link></div>
    </> : <ErrorPanel message="This test run could not be found." retry={() => navigate('/test-lab')} />}
  </div>
}

function metricValue(metric: Metric) {
  if (metric.validity_status === 'unsupported') return 'Not supported by this device'
  if (metric.validity_status === 'unavailable') return 'No measurement collected'
  if (metric.validity_status === 'invalid') return 'Invalid measurement'
  return metric.numeric_value ?? metric.text_value ?? (metric.boolean_value == null ? 'No measurement collected' : String(metric.boolean_value))
}
