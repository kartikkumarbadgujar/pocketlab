import { useMemo, useRef, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, Check, Cpu, Info, Play, Radio, ShieldCheck, Smartphone, Wifi } from 'lucide-react'
import { IconDeviceMobileCog } from '@tabler/icons-react'
import { buildsApi, dataApi, environmentApi, errorMessage, preflightFromError, projectsApi, profilesApi, scenariosApi, testRunsApi } from '../services/api'
import type { EnvironmentRequest, PreflightResult, Profile, ProfileVersion, Scenario, ScenarioVersion } from '../types'
import { cleanRequest, defaultRequest, requestFromProfile } from '../environment'
import PageHeader from '../components/PageHeader'
import BuildApproval from '../components/BuildApproval'
import EnvironmentConfigurator from '../components/EnvironmentConfigurator'
import { CapabilityPanel, PreflightSummary } from '../components/EnvironmentPanels'
import { ErrorPanel, LoadingPanel } from '../components/Feedback'

const schema = z.object({ projectId: z.string().min(1, 'Choose a project'), buildId: z.string().min(1, 'Choose an approved APK build'), profileVersionId: z.string().min(1, 'Choose a condition profile'), scenarioVersionIds: z.array(z.string()).min(1, 'Select at least one scenario version'), mode: z.enum(['emulator', 'actual_device', 'runner']), deviceId: z.string().optional(), repetitions: z.number().int().min(1).max(100) }).superRefine((data, context) => {
  if (data.mode === 'actual_device' && !data.deviceId) context.addIssue({ code: 'custom', message: 'Select a registered device for a physical Android test.', path: ['deviceId'] })
  if (data.mode !== 'runner' && data.repetitions !== 1) context.addIssue({ code: 'custom', message: 'Android environment runs execute once; queue separate runs to repeat.', path: ['repetitions'] })
})
type FormValues = z.infer<typeof schema>
type Mode = FormValues['mode']
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
  const capabilitiesQuery = useQuery({ queryKey: ['environment', 'capabilities'], queryFn: ({ signal }) => environmentApi.capabilities({ signal }), refetchInterval: 30_000 })
  const projects = projectsQuery.data ?? [], devices = devicesQuery.data ?? [], profiles = profilesQuery.data ?? [], scenarios = scenariosQuery.data ?? []
  const profileDetails = useQueries({ queries: profiles.map((profile) => ({ queryKey: ['profiles', profile.id], queryFn: ({ signal }) => profilesApi.get(profile.id, { signal }) })) })
  const scenarioDetails = useQueries({ queries: scenarios.map((scenario) => ({ queryKey: ['scenarios', scenario.id], queryFn: ({ signal }) => scenariosApi.get(scenario.id, { signal }) })) })
  const profileChoices: ProfileChoice[] = profileDetails.flatMap((query) => query.data ? query.data.versions.map((version) => ({ profile: query.data!.profile, version })) : [])
  const allScenarioChoices: ScenarioChoice[] = scenarioDetails.flatMap((query) => query.data ? query.data.versions.map((version) => ({ scenario: query.data!.scenario, version })) : [])
  const defaults = useMemo<FormValues>(() => ({ projectId: search.get('projectId') ?? '', buildId: '', profileVersionId: search.get('profileVersionId') ?? '', scenarioVersionIds: search.get('scenarioVersionId') ? [search.get('scenarioVersionId')!] : [], mode: 'emulator', deviceId: '', repetitions: 1 }), [search])
  const idempotency = useRef<{ fingerprint: string; key: string } | null>(null)
  const [envRequest, setEnvRequest] = useState<EnvironmentRequest>(() => defaultRequest('emulator'))
  const [preflight, setPreflight] = useState<{ fingerprint: string; result: PreflightResult } | null>(null)
  const [confirmedFingerprint, setConfirmedFingerprint] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const { register, handleSubmit, watch, setValue, formState: { errors } } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: defaults })
  const projectId = watch('projectId'), mode = watch('mode'), scenarioVersionIds = watch('scenarioVersionIds') ?? [], deviceId = watch('deviceId')
  const androidMode = mode !== 'runner'
  // Android environments run executable (latest) scenario versions; v1 catalog entries are abstract descriptions.
  const latestVersion = new Map<number, number>()
  allScenarioChoices.forEach(({ scenario, version }) => latestVersion.set(scenario.id, Math.max(latestVersion.get(scenario.id) ?? 0, version.version)))
  const scenarioChoices = androidMode ? allScenarioChoices.filter(({ scenario, version }) => latestVersion.get(scenario.id) === version.version) : allScenarioChoices
  const buildsQuery = useQuery({ queryKey: ['builds', Number(projectId)], queryFn: ({ signal }) => buildsApi.list(Number(projectId), { signal }), enabled: Boolean(projectId) })
  const builds = buildsQuery.data ?? []
  const selectedProfile = profileChoices.find((choice) => String(choice.version.id) === watch('profileVersionId'))
  const cleaned = cleanRequest(envRequest)
  const fingerprint = JSON.stringify({ cleaned, deviceId: mode === 'actual_device' ? deviceId : null })
  const freshPreflight = preflight && preflight.fingerprint === fingerprint ? preflight.result : null
  const confirmed = confirmedFingerprint === fingerprint
  // The result is tied to the exact configuration that was checked, even if the form changes while it is in flight.
  const preflightMutation = useMutation({
    mutationFn: async (vars: { request: EnvironmentRequest; device?: number; fingerprint: string }) => ({ fingerprint: vars.fingerprint, result: await environmentApi.preflight(vars.request, vars.device) }),
    onSuccess: (checked) => setPreflight(checked),
  })
  const createRun = useMutation({ mutationFn: ({ body, key }: { body: Parameters<typeof testRunsApi.create>[0]; key: string }) => testRunsApi.create(body, key) })
  const initialErrors = [projectsQuery.error, devicesQuery.error, profilesQuery.error, scenariosQuery.error].filter(Boolean)
  const loading = projectsQuery.isLoading || devicesQuery.isLoading || profilesQuery.isLoading || scenariosQuery.isLoading || profileDetails.some((query) => query.isLoading) || scenarioDetails.some((query) => query.isLoading)
  const applyProfile = (versionId: string, nextMode: Mode = mode) => {
    const choice = profileChoices.find((c) => String(c.version.id) === versionId)
    const type = nextMode === 'actual_device' ? 'physical_device' : 'emulator'
    setEnvRequest(choice ? requestFromProfile(choice.version.configuration_json, type) : defaultRequest(type))
  }
  const [submitting, setSubmitting] = useState(false)
  const submit = handleSubmit(async (values) => {
    setSubmitError(null)
    if (androidMode && (!freshPreflight?.ok || !confirmed)) { setSubmitError('Run a passing preflight for the current configuration and confirm it before starting.'); return }
    setSubmitting(true)
    let runId: number | null = null
    try {
      const body = { project_id: Number(values.projectId), app_build_id: Number(values.buildId), profile_version_ids: [Number(values.profileVersionId)], scenario_version_ids: values.scenarioVersionIds.map(Number), requested_repetitions: values.repetitions, execution_mode: values.mode === 'actual_device' ? 'actual_device' as const : 'runner' as const, ...(values.mode === 'actual_device' ? { actual_device_id: Number(values.deviceId) } : {}) }
      const runFingerprint = JSON.stringify({ body, env: androidMode ? cleaned : null })
      if (idempotency.current?.fingerprint !== runFingerprint) idempotency.current = { fingerprint: runFingerprint, key: crypto.randomUUID() }
      const run = await createRun.mutateAsync({ key: idempotency.current.key, body })
      runId = run.id
      if (androidMode) {
        await environmentApi.prepare(run.id, cleaned, values.mode === 'actual_device' ? Number(values.deviceId) : undefined)
        await environmentApi.execute(run.id)
      }
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['runs'] }), queryClient.invalidateQueries({ queryKey: ['dashboard'] })])
      navigate(`/runs/${run.id}/live`)
    } catch (error) {
      const plan = preflightFromError(error)
      if (plan) setPreflight({ fingerprint, result: plan })
      setSubmitError(errorMessage(error, 'The run could not be started.'))
      if (androidMode && runId !== null) {
        // Never leave an Android run without an environment in the queue: cancel it and use a fresh key next time.
        idempotency.current = null
        try { await testRunsApi.cancel(runId) } catch { /* the error above is what the user needs to act on */ }
      }
    } finally { setSubmitting(false) }
  })
  const toggleScenario = (id: number, checked: boolean) => {
    const current = scenarioVersionIds.filter((value) => Number(value) !== id)
    setValue('scenarioVersionIds', checked ? [...current, String(id)] : current, { shouldValidate: true, shouldDirty: true })
  }
  const capabilityError = capabilitiesQuery.error ? errorMessage(capabilitiesQuery.error) : undefined
  const noApproved = !buildsQuery.isLoading && !builds.some((build) => build.scan_status === 'approved')

  if (loading) return <div className="page-stack"><PageHeader eyebrow="RUN CONFIGURATION" title="Test lab" description="Choose a project, conditions, and a reproducible scenario." /><LoadingPanel message="Preparing test environment..." /></div>
  if (initialErrors.length) return <div className="page-stack"><PageHeader eyebrow="RUN CONFIGURATION" title="Test lab" description="Choose a project, conditions, and a reproducible scenario." /><ErrorPanel message={errorMessage(initialErrors[0])} retry={() => { void Promise.all([projectsQuery.refetch(), devicesQuery.refetch(), profilesQuery.refetch(), scenariosQuery.refetch()]) }} /></div>
  return <div className="page-stack"><PageHeader eyebrow="RUN CONFIGURATION" title="Configure a test run" description="Pick an approved APK, the conditions to apply on a real Android environment, and the journeys to execute." />
    {!projects.length ? <div className="surface-card setup-empty"><div className="empty-mark"><IconDeviceMobileCog size={20} /></div><h2>Create a project first</h2><p>A test run belongs to a project. Add one before configuring the runner.</p><button className="button button-primary" onClick={() => navigate('/projects')}>Open projects <ArrowRight size={15} /></button></div> : !profileChoices.length || !scenarioChoices.length ? <ErrorPanel message="No profile versions or scenario versions are available. Refresh the backend catalog and try again." /> : <form className="lab-layout" onSubmit={submit}>
      <div className="lab-main-column">
        <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">01</span><div><h2>Choose project and APK</h2><p>Only approved APKs can execute. The worker re-verifies the SHA-256 before installing.</p></div></div>
          <label className="field-label" htmlFor="run-project">Project</label><select id="run-project" {...register('projectId')}><option value="">Select a project</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>{errors.projectId && <span className="field-error">{errors.projectId.message}</span>}
          <label className="field-label" htmlFor="run-build">Approved APK build</label><select id="run-build" {...register('buildId')} disabled={!projectId || buildsQuery.isLoading}><option value="">{buildsQuery.isLoading ? 'Loading project builds...' : 'Select an approved APK'}</option>{builds.map((build) => <option key={build.id} value={build.id} disabled={build.scan_status !== 'approved'}>Build #{build.id} · {build.version_name || build.package_name} · {build.scan_status}</option>)}</select>{errors.buildId && <span className="field-error">{errors.buildId.message}</span>}
          {buildsQuery.error && <ErrorPanel message={errorMessage(buildsQuery.error)} retry={() => void buildsQuery.refetch()} />}{!buildsQuery.isLoading && projectId && <BuildApproval projectId={Number(projectId)} builds={builds} onApproved={(id) => setValue('buildId', String(id), { shouldValidate: true, shouldDirty: true })} />}
        </section>
        <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">02</span><div><h2>Start from a condition profile</h2><p>The profile pre-fills the environment below. Requested values stay separate from what the device actually reports.</p></div></div>
          <label className="field-label" htmlFor="profile-version">Condition profile version</label><select id="profile-version" {...register('profileVersionId', { onChange: (event) => applyProfile(event.target.value) })}><option value="">Select a saved profile version</option>{profileChoices.map(({ profile, version }) => <option key={version.id} value={version.id}>{profile.name.replaceAll('_', ' ')} · v{version.version}</option>)}</select>{errors.profileVersionId && <span className="field-error">{errors.profileVersionId.message}</span>}
          {selectedProfile && <div className="profile-config-preview"><div className="profile-config-head"><span>{selectedProfile.profile.description}</span><button type="button" className="button button-secondary button-small" onClick={() => applyProfile(String(selectedProfile.version.id))}>Reset from profile</button></div><div className="condition-chips">{Object.entries(selectedProfile.version.configuration_json).map(([key, value]) => <span key={key}>{key.split('.').at(-1)?.replaceAll('_', ' ')} <b>{String(value)}</b></span>)}</div><small>Version #{selectedProfile.version.id} · targets only, not measured readings</small></div>}
        </section>
        {androidMode && <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">03</span><div><h2>Android environment</h2><p>Applied to the {mode === 'emulator' ? 'disposable emulator' : 'physical device'} and verified by read-back. Unsupported settings are reported, never faked.</p></div></div>
          <EnvironmentConfigurator value={envRequest} onChange={setEnvRequest} capabilities={capabilitiesQuery.data} />
        </section>}
        <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">{androidMode ? '04' : '03'}</span><div><h2>Select scenarios</h2><p>{androidMode ? 'Executable scenario versions; each validates expected app behaviour, not just that steps ran.' : 'Select one or more reproducible backend scenario versions.'}</p></div></div>
          <div className="scenario-version-select">{scenarioChoices.map(({ scenario, version }) => <label className="checkbox-row" key={version.id}><input type="checkbox" checked={scenarioVersionIds.includes(String(version.id))} onChange={(event) => toggleScenario(version.id, event.target.checked)} /><span>{scenario.name} · v{version.version}</span></label>)}</div>{errors.scenarioVersionIds && <span className="field-error">{errors.scenarioVersionIds.message}</span>}
          {scenarioDetails.some((query) => query.error) && <p className="field-help">Some scenario versions could not be loaded. Refresh the scenario library to retry.</p>}
        </section>
      </div>
      <aside className="lab-side-column"><section className="surface-card form-section"><div className="form-section-title"><span className="step-number">{androidMode ? '05' : '04'}</span><div><h2>Execution target</h2><p>Where the separately authenticated worker runs the APK.</p></div></div>
        <div className="mode-choice">
          {([['emulator', Cpu, 'Android emulator (real AVD)', 'Disposable AVD created, booted and destroyed per run.'], ['actual_device', Smartphone, 'Physical Android device', 'An authorized phone attached to the worker over ADB.'], ['runner', Radio, 'Legacy runner queue', 'No managed Android environment; for custom runners.']] as const).map(([value, Icon, title, hint]) =>
            <label key={value} className={`mode-option ${mode === value ? 'active' : ''}`}><input type="radio" value={value} {...register('mode', { onChange: (event) => { const next = event.target.value as Mode; if (next !== 'runner') { setValue('repetitions', 1); applyProfile(watch('profileVersionId'), next) } } })} /><span className="mode-icon"><Icon size={18} /></span><span><strong>{title}</strong><small>{hint}</small></span>{mode === value && <Check size={16} />}</label>)}
        </div>
        {mode === 'actual_device' && <><label className="field-label" htmlFor="actual-device">Registered device</label><select id="actual-device" {...register('deviceId')}><option value="">Select a device</option>{devices.map((device) => <option key={device.id} value={device.id}>{device.device_name} · {device.model || 'Model not set'}</option>)}</select>{errors.deviceId && <span className="field-error">{errors.deviceId.message}</span>}{!devices.length && <p className="field-help">No devices are registered yet. Register one, then map it to its ADB serial on the worker (POCKETLAB_PHYSICAL_DEVICES).</p>}</>}
      </section>
      {androidMode && <section className="surface-card form-section"><div className="eyebrow">WORKER CAPABILITIES</div><CapabilityPanel data={capabilitiesQuery.data} loading={capabilitiesQuery.isLoading} error={capabilityError} /></section>}
      {androidMode && <section className="surface-card form-section"><div className="eyebrow">PREFLIGHT</div>
        <button type="button" className="button button-secondary full-width" disabled={preflightMutation.isPending} onClick={() => preflightMutation.mutate({ request: cleaned, device: mode === 'actual_device' && deviceId ? Number(deviceId) : undefined, fingerprint })}><ShieldCheck size={15} /> {preflightMutation.isPending ? 'Checking…' : freshPreflight ? 'Re-run preflight' : 'Run environment preflight'}</button>
        {preflightMutation.error && <div className="form-error" role="alert">{errorMessage(preflightMutation.error)}</div>}
        {preflight && !freshPreflight && <p className="field-help">The configuration changed since the last preflight. Run it again.</p>}
        {freshPreflight && <PreflightSummary result={freshPreflight} />}
        {freshPreflight?.ok && <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmedFingerprint(event.target.checked ? fingerprint : null)} /> I confirm this configuration. Conditions marked as not applied will be reported as such.</label>}
      </section>}
      {!androidMode && <section className="surface-card form-section"><div className="form-section-title"><span className="step-number">05</span><div><h2>Repetitions</h2><p>Repeat the selected scenario set.</p></div></div><label className="field-label" htmlFor="repetitions">Run repetitions (1–100)</label><input id="repetitions" type="number" min="1" max="100" {...register('repetitions', { valueAsNumber: true })} />{errors.repetitions && <span className="field-error">{errors.repetitions.message}</span>}</section>}
      <section className="surface-card form-section"><div className="eyebrow">REVIEW</div><h2>Submission summary</h2><p>{projects.find((project) => String(project.id) === projectId)?.name || 'No project selected'}</p><p>{selectedProfile?.profile.name ?? 'No profile selected'}{selectedProfile ? ` · v${selectedProfile.version.version}` : ''}</p><p>{scenarioVersionIds.length} scenario version(s){androidMode ? ' · executed once on a fresh environment' : ` · ${watch('repetitions')} repetition(s)`}</p><p>{mode === 'emulator' ? `Emulator test · API ${envRequest.device.api_level} · ${envRequest.device.ram_mb / 1024} GB RAM` : mode === 'actual_device' ? `Physical Android test · ${devices.find((device) => String(device.id) === deviceId)?.device_name || 'select a device'}` : 'Legacy runner queue'}</p></section>
      <div className="lab-submit-card">{(submitError || createRun.error) && <div className="form-error" role="alert">{submitError ?? errorMessage(createRun.error)}</div>}
        <button type="submit" className="button button-primary full-width" disabled={submitting || buildsQuery.isLoading || noApproved || (androidMode && (!freshPreflight?.ok || !confirmed))}><Play size={16} /> {submitting ? 'Starting test...' : androidMode ? 'Start real Android test' : 'Queue test run'}</button>
        {noApproved && <p>No approved APK is available for this project.</p>}
        {androidMode && !(freshPreflight?.ok && confirmed) && <p><Info size={14} /> Run a passing preflight and confirm it to enable the start button.</p>}
        <p><Wifi size={14} /> The API accepts the job (HTTP 202); the worker performs all execution outside the web server.</p></div></aside>
    </form>}
  </div>
}
