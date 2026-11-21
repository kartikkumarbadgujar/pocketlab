import { useEffect, useState } from 'react'
import { Activity, Check, ChevronDown, ChevronUp, CircleX, LoaderCircle } from 'lucide-react'
import type { TestRun } from '../types'
import { useReducedMotion } from 'motion/react'
import './ThoughtLine.css'

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

export default function ThoughtLine({ run }: { run: TestRun }) {
  const [open, setOpen] = useState(true), [now, setNow] = useState(Date.now())
  const reduce = useReducedMotion()
  useEffect(() => { if (['completed', 'failed', 'canceled'].includes(run.status)) return; const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [run.status])
  const stage = stages[run.status]
  const Icon = stage.state === 'completed' ? Check : stage.state === 'failed' ? CircleX : reduce ? Activity : LoaderCircle
  return <section className="surface-card thought-card" aria-label="Confirmed test progress">
    <div className="thought-head"><div><div className="thought-title">Test progress</div><div className="thought-subtitle">Latest status confirmed by the PocketLab API</div></div><button type="button" className="thought-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Collapse' : 'Show details'}{open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</button></div>
    {open && <><div className="thought-row"><span className={`thought-marker ${stage.state}`}>{stage.state === 'active' && !reduce ? <Icon size={16} className="spin-slow" /> : <Icon size={16} />}</span><div><div className="thought-name">{stage.title}</div><div className="thought-detail">{stage.detail}</div></div><span className="thought-duration">{run.status === 'completed' || run.status === 'failed' || run.status === 'canceled' ? elapsed(run.started_at, run.completed_at) : elapsed(run.started_at || run.created_at, null, now) ?? '—'}</span></div>
      {run.error_message && <div className="thought-note" role="alert">{run.error_message}</div>}
      <div className="thought-note">This backend reports run status, not individual device stages. PocketLab shows only confirmed status and elapsed time; no progress is inferred from a timer.</div></>}
  </section>
}
