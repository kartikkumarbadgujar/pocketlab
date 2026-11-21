import { useMemo, useRef } from 'react'
import { useQueries, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, Check, Info, Play, Radio, Smartphone, Wifi } from 'lucide-react'
import { IconDeviceMobileCog } from '@tabler/icons-react'
import { buildsApi, dataApi, errorMessage, projectsApi, profilesApi, scenariosApi, testRunsApi } from '../services/api'
import type { Profile, ProfileVersion, Scenario, ScenarioVersion } from '../types'
import PageHeader from '../components/PageHeader'
import { ErrorPanel, LoadingPanel } from '../components/Feedback'

const schema = z.object({ projectId: z.string().min(1, 'Choose a project'), buildId: z.string().min(1, 'Choose an approved APK build'), profileVersionId: z.string().min(1, 'Choose a condition profile'), scenarioVersionIds: z.array(z.string()).min(1, 'Select at least one scenario version'), mode: z.enum(['runner', 'actual_device']), deviceId: z.string().optional(), repetitions: z.number().int().min(1).max(100) }).superRefine((data, context) => { if (data.mode === 'actual_device' && !data.deviceId) context.addIssue({ code: 'custom', message: 'Select a registered device for an actual-device run.', path: ['deviceId'] }) })
type FormValues = z.infer<typeof schema>
type ProfileChoice = { profile: Profile; version: ProfileVersion }
type ScenarioChoice = { scenario: Scenario; version: ScenarioVersion }

export default function TestLabPage() {
  const [search] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => projectsApi.list({ signal }) })
  const devicesQuery = useQuery({ queryKey: ['devices'], queryFn: ({ signal }) => dataApi.devices({ signal }) })
  const profilesQuery = useQuery({ queryKey: ['profiles'], queryFn: ({ signal }) => profilesApi.list({ signal }) })
  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: ({ signal }) => scenariosApi.list({ signal }) })
  const projects = projectsQuery.data ?? [], devices = devicesQuery.data ?? [], profiles = profilesQuery.data ?? [], scenarios = scenariosQuery.data ?? []
  const profileDetails = useQueries({ queries: profiles.map((profile) => ({ queryKey: ['profiles', profile.id], queryFn: ({ signal }) => profilesApi.get(profile.id, { signal }) })) })
  const scenarioDetails = useQueries({ queries: scenarios.map((scenario) => ({ queryKey: ['scenarios', scenario.id], queryFn: ({ signal }) => scenariosApi.get(scenario.id, { signal }) })) })
  const profileChoices: ProfileChoice[] = profileDetails.flatMap((query) => query.data ? query.data.versions.map((version) => ({ profile: query.data!.profile, version })) : [])
  const scenarioChoices: ScenarioChoice[] = scenarioDetails.flatMap((query) => query.data ? query.data.versions.map((version) => ({ scenario: query.data!.scenario, version })) : [])
  const defaults = useMemo<FormValues>(() => ({ projectId: search.get('projectId') ?? '', buildId: '', profileVersionId: search.get('profileVersionId') ?? '', scenarioVersionIds: search.get('scenarioVersionId') ? [search.get('scenarioVersionId')!] : [], mode: 'runner', deviceId: '', repetitions: 1 }), [search])
  const idempotency = useRef<{ fingerprint: string; key: string } | null>(null)
  const { register, handleSubmit, watch, setValue, formState: { errors } } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: defaults })
  const projectId = watch('projectId'), mode = watch('mode'), scenarioVersionIds = watch('scenarioVersionIds') ?? []
  const buildsQuery = useQuery({ queryKey: ['builds', Number(projectId)], queryFn: ({ signal }) => buildsApi.list(Number(projectId), { signal }), enabled: Boolean(projectId) })
  const builds = buildsQuery.data ?? []
  const selectedProfile = profileChoices.find((choice) => String(choice.version.id) === watch('profileVersionId'))
  const createRun = useMutation({ mutationFn: ({ body, key }: { body: Parameters<typeof testRunsApi.create>[0]; key: string }) => testRunsApi.create(body, key), onSuccess: async (run) => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['runs'] }), queryClient.invalidateQueries({ queryKey: ['dashboard'] })]); navigate(`/runs/${run.id}/live`) } })
  const initialErrors = [projectsQuery.error, devicesQuery.error, profilesQuery.error, scenariosQuery.error].filter(Boolean)
  const loading = projectsQuery.isLoading || devicesQuery.isLoading || profilesQuery.isLoading || scenariosQuery.isLoading || profileDetails.some((query) => query.isLoading) || scenarioDetails.some((query) => query.isLoading)
  const submit = handleSubmit(async (values) => {
    const error = createRun.error
    if (error) createRun.reset()
    try {
      const body = { project_id: Number(values.projectId), app_build_id: Number(values.buildId), profile_version_ids: [Number(values.profileVersionId)], scenario_version_ids: values.scenarioVersionIds.map(Number), requested_repetitions: values.repetitions, execution_mode: values.mode, ...(values.mode === 'actual_device' ? { actual_device_id: Number(values.deviceId) } : {}) }
      const fingerprint = JSON.stringify(body)
      if (idempotency.current?.fingerprint !== fingerprint) idempotency.current = { fingerprint, key: crypto.randomUUID() }
      await createRun.mutateAsync({ key: idempotency.current.key, body })
    } catch { /* mutation error is rendered below; success navigation happens after API acceptance */ }
  })
  const toggleScenario = (id: number, checked: boolean) => {
    const current = scenarioVersionIds.filter((value) => Number(value) !== id)
    setValue('scenarioVersionIds', checked ? [...current, String(id)] : current, { shouldValidate: true, shouldDirty: true })
  }

  if (loading) return <div className="page-stack"><PageHeader eyebrow="RUN CONFIGURATION" title="Test lab" description="Choose a project, conditions, and a reproducible scenario." /><LoadingPanel message="Preparing test environment..." /></div>
  if (initialErrors.length) return <div className="page-stack"><PageHeader eyebrow="RUN CONFIGURATION" title="Test lab" description="Choose a project, conditions, and a reproducible scenario." /><ErrorPanel message={errorMessage(initialErrors[0])} retry={() => { void Promise.all([projectsQuery.refetch(), devicesQuery.refetch(), profilesQuery.refetch(), scenariosQuery.refetch()]) }} /></div>
  return <div className="page-stack"><PageHeader eyebrow="RUN CONFIGURATION" title="Configure a test run" description="Review an approved APK, saved conditions, and scenario versions before the backend queues a run." />
    {!projects.length ? <div className="surface-card setup-empty"><div className="empty-mark"><IconDeviceMobileCog size={20} /></div><h2>Create a project first</h2><p>A test run belongs to a project. Add one before configuring the runner.</p><button className="button button-primary" onClick={() => navigate('/projects')}>Open projects <ArrowRight size={15} /></button></div> : !profileChoices.length || !scenarioChoices.length ? <ErrorPanel message="No profile versions or scenario versions are available. Refresh the backend catalog and try again." /> : <form className="lab-layout" onSubmit={submit}>
      <div className="lab-main-column">
        <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">01</span><div><h2>Choose project and APK</h2><p>Only server-approved APKs can be queued.</p></div></div>
          <label className="field-label" htmlFor="run-project">Project</label><select id="run-project" {...register('projectId')}><option value="">Select a project</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>{errors.projectId && <span className="field-error">{errors.projectId.message}</span>}
          <label className="field-label" htmlFor="run-build">Approved APK build</label><select id="run-build" {...register('buildId')} disabled={!projectId || buildsQuery.isLoading}><option value="">{buildsQuery.isLoading ? 'Loading project builds...' : 'Select an approved APK'}</option>{builds.map((build) => <option key={build.id} value={build.id} disabled={build.scan_status !== 'approved'}>Build #{build.id} · {build.version_name || build.package_name} · {build.scan_status}</option>)}</select>{errors.buildId && <span className="field-error">{errors.buildId.message}</span>}
          {buildsQuery.error && <ErrorPanel message={errorMessage(buildsQuery.error)} retry={() => void buildsQuery.refetch()} />}{!buildsQuery.isLoading && builds.length > 0 && !builds.some((build) => build.scan_status === 'approved') && <p className="field-help">Uploads remain quarantined until an external scanner approves them.</p>}
        </section>
        <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">02</span><div><h2>Set test conditions</h2><p>Configured targets stay separate from actual runner observations.</p></div></div>
          <label className="field-label" htmlFor="profile-version">Condition profile version</label><select id="profile-version" {...register('profileVersionId')}><option value="">Select a saved profile version</option>{profileChoices.map(({ profile, version }) => <option key={version.id} value={version.id}>{profile.name.replaceAll('_', ' ')} · v{version.version}</option>)}</select>{errors.profileVersionId && <span className="field-error">{errors.profileVersionId.message}</span>}
          {selectedProfile && <div className="profile-config-preview"><div className="profile-config-head"><span>{selectedProfile.profile.description}</span><span className="status-pill neutral">Configured</span></div><div className="condition-chips">{Object.entries(selectedProfile.version.configuration_json).map(([key, value]) => <span key={key}>{key.split('.').at(-1)?.replaceAll('_', ' ')} <b>{String(value)}</b></span>)}</div><small>Version #{selectedProfile.version.id} · targets only, not measured readings</small></div>}
        </section>
        <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">03</span><div><h2>Select scenario versions</h2><p>Select one or more reproducible backend scenario versions.</p></div></div>
          <div className="scenario-version-select">{scenarioChoices.map(({ scenario, version }) => <label className="checkbox-row" key={version.id}><input type="checkbox" checked={scenarioVersionIds.includes(String(version.id))} onChange={(event) => toggleScenario(version.id, event.target.checked)} /><span>{scenario.name} · v{version.version}</span></label>)}</div>{errors.scenarioVersionIds && <span className="field-error">{errors.scenarioVersionIds.message}</span>}
          {scenarioDetails.some((query) => query.error) && <p className="field-help">Some scenario versions could not be loaded. Refresh the scenario library to retry.</p>}
        </section>
      </div>
      <aside className="lab-side-column"><section className="surface-card form-section"><div className="form-section-title"><span className="step-number">04</span><div><h2>Runner target</h2><p>Choose how the separately authenticated runner receives the job.</p></div></div>
        <div className="mode-choice"><label className={`mode-option ${mode === 'runner' ? 'active' : ''}`}><input type="radio" value="runner" {...register('mode')} /><span className="mode-icon"><Radio size={18} /></span><span><strong>Runner queue</strong><small>Queue for a runner agent to claim.</small></span>{mode === 'runner' && <Check size={16} />}</label><label className={`mode-option ${mode === 'actual_device' ? 'active' : ''}`}><input type="radio" value="actual_device" {...register('mode')} /><span className="mode-icon"><Smartphone size={18} /></span><span><strong>Actual device</strong><small>Bind to a registered physical phone or emulator.</small></span>{mode === 'actual_device' && <Check size={16} />}</label></div>
        {mode === 'actual_device' && <><label className="field-label" htmlFor="actual-device">Registered device</label><select id="actual-device" {...register('deviceId')}><option value="">Select a device</option>{devices.map((device) => <option key={device.id} value={device.id}>{device.device_name} · {device.model || 'Model not set'}</option>)}</select>{errors.deviceId && <span className="field-error">{errors.deviceId.message}</span>}{!devices.length && <p className="field-help">No devices are registered yet. Register one before selecting actual-device mode.</p>}<div className="field-help"><Info size={14} /> Device connectivity is not exposed by the current API. A runner must verify applied conditions and attach observations.</div></>}
      </section>
      <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">05</span><div><h2>Repetitions</h2><p>Repeat the selected scenario set.</p></div></div><label className="field-label" htmlFor="repetitions">Run repetitions (1–100)</label><input id="repetitions" type="number" min="1" max="100" {...register('repetitions', { valueAsNumber: true })} />{errors.repetitions && <span className="field-error">{errors.repetitions.message}</span>}</section>
      <section className="surface-card form-section"><div className="eyebrow">REVIEW</div><h2>Submission summary</h2><p>{projects.find((project) => String(project.id) === projectId)?.name || 'No project selected'}</p><p>{selectedProfile?.profile.name ?? 'No profile selected'}{selectedProfile ? ` · v${selectedProfile.version.version}` : ''}</p><p>{scenarioVersionIds.length} scenario version(s) · {watch('repetitions')} repetition(s)</p><p>{mode === 'actual_device' ? devices.find((device) => String(device.id) === watch('deviceId'))?.device_name || 'Select an actual device' : 'Runner queue'}</p></section>
      <div className="lab-submit-card">{createRun.error && <div className="form-error" role="alert">{errorMessage(createRun.error)}</div>}<button type="submit" className="button button-primary full-width" disabled={createRun.isPending || buildsQuery.isLoading || !builds.some((build) => build.scan_status === 'approved')}><Play size={16} /> {createRun.isPending ? 'Queueing test...' : 'Review and queue test run'}</button>{!buildsQuery.isLoading && !builds.some((build) => build.scan_status === 'approved') && <p>No approved APK is available for this project. Uploads cannot run until a scanner approves them.</p>}<p><Wifi size={14} /> The API must accept the request and return a run ID before navigation.</p></div></aside>
    </form>}
  </div>
}
