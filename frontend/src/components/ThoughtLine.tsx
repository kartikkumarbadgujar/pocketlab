import { useEffect, useState } from 'react'
import { Activity, Check, ChevronDown, ChevronUp, Circle, CircleX, LoaderCircle, MinusCircle } from 'lucide-react'
import type { EnvironmentEvent, EnvironmentStatus, ExecutionEnvironment, TestRun } from '../types'
import { useReducedMotion } from 'motion/react'
import { STAGE_COPY } from '../environment'
import './ThoughtLine.css'
import './Environment.css'

function elapsed(start?: string | null, end?: string | null, now = Date.now()) {
  if (!start) return null
  const seconds = Math.max(0, Math.floor(((end ? new Date(end).getTime() : now) - new Date(start).getTime()) / 1000))
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`
}
const stages: Record<TestRun['status'], { title: string; detail: string; state: string }> = {
  queued: { title: 'Waiting for runner', detail: 'The run is queued in the backend job store.', state: 'active' },
  claimed: { title: 'Runner claimed this test', detail: 'The runner has a lease and should heartbeat while it works.', state: 'active' },
  running: { title: 'Test is running', detail: 'The backend confirms execution; detailed stage events are not available.', state: 'active' },
  completed: { title: 'Test run completed', detail: 'The runner reported completion to the backend.', state: 'completed' },
  failed: { title: 'Test run failed', detail: 'The run ended with an error.', state: 'failed' },
  canceled: { title: 'Test run canceled', detail: 'The run was canceled.', state: 'failed' },
}
const LIFECYCLE: EnvironmentStatus[] = ['QUEUED', 'PREFLIGHT', 'PROVISIONING', 'BOOTING', 'READY', 'INSTALLING', 'APPLYING_CONDITIONS', 'VERIFYING_CONDITIONS', 'EXECUTING', 'COLLECTING_METRICS', 'ANALYZING', 'COMPLETED', 'CLEANING', 'CLEANED']
const EMULATOR_ONLY = new Set<EnvironmentStatus>(['PROVISIONING', 'BOOTING'])
type StageState = 'completed' | 'active' | 'failed' | 'pending' | 'skipped'

type Props = { run: TestRun; environment?: ExecutionEnvironment | null; events?: EnvironmentEvent[] }

export default function ThoughtLine({ run, environment, events = [] }: Props) {
  const [open, setOpen] = useState(true), [now, setNow] = useState(Date.now())
  const reduce = useReducedMotion()
  const finished = environment ? environment.status === 'CLEANED' : ['completed', 'failed', 'canceled'].includes(run.status)
  useEffect(() => { if (finished) return; const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [finished])

  if (!environment) {
    const stage = stages[run.status]
    const Icon = stage.state === 'completed' ? Check : stage.state === 'failed' ? CircleX : reduce ? Activity : LoaderCircle
    return <section className="surface-card thought-card" aria-label="Confirmed test progress">
      <div className="thought-head"><div><div className="thought-title">Test progress</div><div className="thought-subtitle">Latest status confirmed by the PocketLab API</div></div><button type="button" className="thought-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Collapse' : 'Show details'}{open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</button></div>
      {open && <><div className="thought-row"><span className={`thought-marker ${stage.state}`}>{stage.state === 'active' && !reduce ? <Icon size={16} className="spin-slow" /> : <Icon size={16} />}</span><div><div className="thought-name">{stage.title}</div><div className="thought-detail">{stage.detail}</div></div><span className="thought-duration">{run.status === 'completed' || run.status === 'failed' || run.status === 'canceled' ? elapsed(run.started_at, run.completed_at) : elapsed(run.started_at || run.created_at, null, now) ?? '—'}</span></div>
        {run.error_message && <div className="thought-note" role="alert">{run.error_message}</div>}
        <div className="thought-note">This run has no managed Android environment, so only the coarse run status is available. No progress is inferred from a timer.</div></>}
    </section>
  }

  // A stage counts as reached only when the backend recorded an event carrying that status.
  // Only the current attempt counts, so a retried run does not show stages from a previous worker attempt as done.
  const attempt = Math.max(environment.attempt, 1)
  const reached = new Map<EnvironmentStatus, string>()
  for (const event of events) {
    if (!event.status || reached.has(event.status)) continue
    if (event.attempt === attempt || event.status === 'QUEUED') reached.set(event.status, event.event_timestamp)
  }
  const lifecycle = LIFECYCLE.filter((s) => environment.environment_type === 'emulator' || !EMULATOR_ONLY.has(s))
  const failedAt = reached.get('FAILED'), cancelAt = reached.get('CANCEL_REQUESTED')
  const rows: { status: EnvironmentStatus; state: StageState; at?: string; until?: string }[] = []
  const ordered = lifecycle.flatMap((s) => {
    const extra: EnvironmentStatus[] = []
    if (s === 'CLEANING' && failedAt) extra.push('FAILED')
    if (s === 'CLEANING' && cancelAt) extra.push('CANCEL_REQUESTED')
    return [...extra, s]
  })
  const reachedTimes = ordered.map((s) => reached.get(s)).filter((t): t is string => Boolean(t)).sort()
  for (const status of ordered) {
    const at = reached.get(status)
    let state: StageState
    if (status === environment.status) state = status === 'FAILED' ? 'failed' : status === 'CLEANED' || status === 'COMPLETED' ? 'completed' : 'active'
    else if (at) state = status === 'FAILED' ? 'failed' : 'completed'
    else state = finished || failedAt || cancelAt ? 'skipped' : 'pending'
    const until = at ? reachedTimes.find((t) => t > at) : undefined
    rows.push({ status, state, at, until })
  }
  const latest = events.at(-1)
  const recent = events.slice(-10).reverse()
  const icon = (state: StageState) => state === 'completed' ? <Check size={14} /> : state === 'failed' ? <CircleX size={14} /> : state === 'skipped' ? <MinusCircle size={14} />
    : state === 'active' ? (reduce ? <Activity size={14} /> : <LoaderCircle size={14} className="spin-slow" />) : <Circle size={10} />

  return <section className="surface-card thought-card" aria-label="Confirmed Android environment lifecycle">
    <div className="thought-head"><div><div className="thought-title">Android environment lifecycle</div><div className="thought-subtitle">Each step is marked only after the worker reports it to the API · attempt {Math.max(environment.attempt, 1)}</div></div><button type="button" className="thought-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Collapse' : 'Show details'}{open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</button></div>
    {open && <>
      <div className="thought-stages">{rows.map((row) => <div className={`thought-stage ${row.state}`} key={row.status}>
        <span className={`thought-marker ${row.state}`}>{icon(row.state)}</span>
        <div><div className="thought-name">{STAGE_COPY[row.status].title}</div>{row.state === 'active' && <div className="thought-detail">{environment.status_message || STAGE_COPY[row.status].detail}</div>}{row.state === 'failed' && <div className="thought-detail">{environment.status_message}</div>}</div>
        <span className="thought-duration">{row.at ? (row.state === 'active' ? elapsed(row.at, null, now) : row.until ? elapsed(row.at, row.until) : new Date(row.at).toLocaleTimeString()) : row.state === 'skipped' ? 'skipped' : ''}</span>
      </div>)}</div>
      {latest && <div className="thought-note">Latest worker event: {latest.message}</div>}
      {recent.length > 0 && <div className="event-log" aria-label="Recent environment events">{recent.map((event) => <div className={`event-line ${event.severity}`} key={event.id}><time>{new Date(event.event_timestamp).toLocaleTimeString()}</time><span>{event.message}</span></div>)}</div>}
    </>}
  </section>
}
