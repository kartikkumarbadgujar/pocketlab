import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { useReducedMotion } from 'motion/react'
import { Activity, ArrowRight, CircleAlert, CircleCheck, Cpu, FlaskConical, FolderKanban, Gauge, Plus, Smartphone, TriangleAlert } from 'lucide-react'
import { IconDeviceAnalytics } from '@tabler/icons-react'
import { HugeiconsIcon } from '@hugeicons/react'
import { AnalyticsUpIcon } from '@hugeicons/core-free-icons'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import PageHeader from '../components/PageHeader'
import StatsCounter from '../components/StatsCounter'
import VerseCards from '../components/VerseCards'
import { ErrorPanel } from '../components/Feedback'
import { dataApi, errorMessage } from '../services/api'
import type { TestRun } from '../types'

function chartData(runs: TestRun[]) {
  return Array.from({ length: 10 }, (_, index) => {
    const day = new Date(); day.setDate(day.getDate() - 9 + index); const key = day.toISOString().slice(0, 10)
    return { day: day.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), value: runs.filter((run) => run.created_at.slice(0, 10) === key).length }
  })
}
export default function DashboardPage() {
  const navigate = useNavigate()
  const reduceMotion = useReducedMotion()
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => dataApi.projects({ signal }) })
  const devicesQuery = useQuery({ queryKey: ['devices'], queryFn: ({ signal }) => dataApi.devices({ signal }) })
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: ({ signal }) => dataApi.runs({ signal }) })
  const profilesQuery = useQuery({ queryKey: ['profiles'], queryFn: ({ signal }) => dataApi.profiles({ signal }) })
  const runs = runsQuery.data ?? []
  const latestRunIds = [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 25).map((run) => run.id)
  const criticalQuery = useQuery({
    queryKey: ['dashboard', 'critical-findings', latestRunIds],
    enabled: runsQuery.isSuccess,
    queryFn: async ({ signal }) => {
      const findings = await Promise.all(latestRunIds.map((id) => dataApi.findings(id, { signal })))
      return findings.flat().filter((finding) => finding.severity === 'critical').length
    },
  })
  const loadError = projectsQuery.error || devicesQuery.error || runsQuery.error
  const loading = projectsQuery.isLoading || devicesQuery.isLoading || runsQuery.isLoading
  const refreshDashboard = () => { void Promise.all([projectsQuery.refetch(), devicesQuery.refetch(), runsQuery.refetch()]) }
  const goConfigure = (profileVersionId: number) => navigate(`/test-lab?profileVersionId=${profileVersionId}`)
  return <div className="page-stack"><PageHeader eyebrow="POCKETLAB OVERVIEW" title="Good testing starts here." description="See whatâ€™s happening across your Android reliability runs." action={<Link to="/test-lab" className="button button-primary"><Plus size={17} /> New test run</Link>} />
    {loadError ? <ErrorPanel message={errorMessage(loadError)} retry={refreshDashboard} /> : <>
      <div className="stats-grid">
        <StatsCounter label="Total projects" value={projectsQuery.data?.length ?? null} loading={loading} icon={<FolderKanban size={18} />} hint="Across your workspace" />
        <StatsCounter label="Total test runs" value={runsQuery.data?.length ?? null} loading={loading} icon={<Activity size={18} />} tone="violet" hint="All runs returned by the API" />
        <StatsCounter label="Passed tests" value={null} icon={<CircleCheck size={18} />} tone="green" hint="The API reports run completion, not pass/fail totals" />
        <StatsCounter label="Failed test runs" value={runsQuery.data?.filter((run) => run.status === 'failed').length ?? null} loading={runsQuery.isLoading} icon={<CircleAlert size={18} />} tone="rose" hint="Runner-reported failed runs" />
        <StatsCounter label="Critical findings" value={criticalQuery.data ?? null} loading={criticalQuery.isLoading} icon={<TriangleAlert size={18} />} tone="amber" hint={criticalQuery.error ? 'Finding data unavailable' : 'From the latest 25 runs'} />
        <StatsCounter label="Connected devices" value={null} loading={devicesQuery.isLoading} icon={<Smartphone size={18} />} tone="cyan" hint={devicesQuery.isLoading ? 'Loading registered device count' : `${devicesQuery.data?.length ?? 0} registered Â· live connectivity is not exposed`} />
        <StatsCounter label="Estimated affected users" value={null} icon={<HugeiconsIcon icon={AnalyticsUpIcon} size={19} />} tone="violet" hint="No read endpoint for saved estimates" />
      </div>
      <div className="dashboard-grid">
        <section className="surface-card chart-card"><div className="card-heading"><div><div className="eyebrow">ACTIVITY</div><h2>Test runs</h2></div><Link to="/test-lab" className="text-link">Open test lab <ArrowRight size={14} /></Link></div><div className="chart-legend"><span><i /> Runs created</span><span className="chart-range">Last 10 days Â· API data</span></div><div className="dashboard-chart">{loading && !runsQuery.data ? <div className="chart-loading"><span /></div> : <ResponsiveContainer width="100%" height="100%"><LineChart data={chartData(runs)} margin={{ top: 8, right: 8, left: -22, bottom: 0 }}><CartesianGrid vertical={false} stroke="#E4E4E7" strokeDasharray="4 4" /><XAxis dataKey="day" axisLine={false} tickLine={false} tick={{ fill: '#71717A', fontSize: 11 }} /><YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#71717A', fontSize: 11 }} /><Tooltip cursor={{ stroke: '#E4E4E7', strokeDasharray: '4 4' }} contentStyle={{ borderRadius: 8, borderColor: '#E4E4E7', boxShadow: 'none', fontSize: 12 }} /><Line type="monotone" dataKey="value" name="Runs" stroke="#0F766E" strokeWidth={2} dot={{ r: 3.5, fill: '#fff', stroke: '#0F766E', strokeWidth: 2 }} activeDot={{ r: 3.5, fill: '#fff', stroke: '#0F766E', strokeWidth: 2 }} isAnimationActive={!reduceMotion} /></LineChart></ResponsiveContainer>}</div></section>
        <section className="surface-card recent-card"><div className="card-heading"><div><div className="eyebrow">LATEST</div><h2>Recent test runs</h2></div><Link to="/test-lab" className="icon-link" aria-label="Go to test lab"><ArrowRight size={17} /></Link></div>{!runs.length && !loading ? <div className="recent-empty"><div className="empty-mark"><FlaskConical size={18} /></div><strong>No test runs yet</strong><p>Choose a profile and create your first run.</p><Link to="/test-lab" className="text-link">Create a test <ArrowRight size={14} /></Link></div> : <div className="recent-list">{[...runs].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5).map((run) => <Link to={`/runs/${run.id}`} className="recent-run" key={run.id}><span className={`run-status-dot ${run.status}`} /><span className="recent-run-copy"><strong>Run #{run.id}</strong><small>{new Date(run.created_at).toLocaleString()}</small></span><span className={`status-pill ${run.status}`}>{run.status}</span></Link>)}</div>}</section>
      </div>
      {profilesQuery.error && <ErrorPanel message={errorMessage(profilesQuery.error)} retry={() => void profilesQuery.refetch()} />}
      <VerseCards profiles={profilesQuery.data ?? []} loading={profilesQuery.isLoading} onConfigure={goConfigure} />
      <div className="dashboard-shortcuts"><Link to="/devices" className="shortcut-card surface-card"><span className="shortcut-icon"><IconDeviceAnalytics size={19} /></span><span><strong>Device inventory</strong><small>Register and inspect your test hardware</small></span><ArrowRight size={16} /></Link><Link to="/scenarios" className="shortcut-card surface-card"><span className="shortcut-icon violet"><Cpu size={18} /></span><span><strong>Scenario library</strong><small>Explore reproducible test scenarios</small></span><ArrowRight size={16} /></Link><Link to="/reports" className="shortcut-card surface-card"><span className="shortcut-icon cyan"><Gauge size={18} /></span><span><strong>Impact reports</strong><small>Estimate user impact with assumptions</small></span></Link></div>
    </>}
  </div>
}

