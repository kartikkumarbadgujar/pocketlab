import axios, { type AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import type { Artifact, Build, BuildUpload, Device, Finding, ImpactEstimate, Metric, Profile, ProfileVersion, Project, Scenario, ScenarioDetail, TestRun, TokenPair, User } from '../types'

const baseURL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').replace(/\/$/, '')
const ACCESS_KEY = 'pocketlab.access'
const REFRESH_KEY = 'pocketlab.refresh'

export const tokens = {
  get access() { return localStorage.getItem(ACCESS_KEY) },
  get refresh() { return localStorage.getItem(REFRESH_KEY) },
  save(pair: TokenPair) { localStorage.setItem(ACCESS_KEY, pair.access_token); localStorage.setItem(REFRESH_KEY, pair.refresh_token) },
  clear() { localStorage.removeItem(ACCESS_KEY); localStorage.removeItem(REFRESH_KEY) },
}

export const api = axios.create({ baseURL, timeout: 20_000, withCredentials: true, headers: { Accept: 'application/json' } })
api.interceptors.request.use((config) => {
  const access = tokens.access
  if (access) config.headers.Authorization = `Bearer ${access}`
  return config
})

let refreshPromise: Promise<string> | null = null
type RetryConfig = InternalAxiosRequestConfig & { _pocketlabRetried?: boolean }
api.interceptors.response.use((response) => response, async (error: AxiosError) => {
  const config = error.config as RetryConfig | undefined
  if (error.response?.status !== 401 || !config || config._pocketlabRetried || config.url?.includes('/auth/')) throw error
  const refresh = tokens.refresh
  if (!refresh) { expireSession(); throw error }
  config._pocketlabRetried = true
  try {
    refreshPromise ??= axios.post<TokenPair>(`${baseURL}/auth/refresh`, { refresh_token: refresh }, { timeout: 20_000, withCredentials: true }).then(({ data }) => { tokens.save(data); return data.access_token }).finally(() => { refreshPromise = null })
    config.headers.Authorization = `Bearer ${await refreshPromise}`
    return api(config)
  } catch (refreshError) { expireSession(); throw refreshError }
})

function expireSession() {
  tokens.clear()
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('pocketlab:session-expired'))
}

export function errorMessage(error: unknown, fallback = 'Something went wrong. Please try again.') {
  if (axios.isAxiosError(error)) {
    const detail: unknown = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(' ')
    if (error.response?.status === 401) return 'Your session expired. Sign in again to continue.'
    if (!error.response) return 'Could not reach the PocketLab API. Check the backend connection.'
  }
  return fallback
}

type RequestOptions = { signal?: AbortSignal }
type MetricRequestOptions = RequestOptions & { limit?: number }
async function fetchPages<T>(path: string, options: RequestOptions = {}) {
  const all: T[] = []
  let offset = 0
  while (true) {
    const { data } = await api.get<T[]>(path, { params: { limit: 100, offset }, signal: options.signal })
    all.push(...data)
    if (data.length < 100) return all
    offset += data.length
  }
}

export type ProjectInput = Pick<Project, 'name'> & Partial<Pick<Project, 'description' | 'application_package'>>
export type DeviceInput = Pick<Device, 'device_name'> & Partial<Omit<Device, 'id' | 'owner_user_id' | 'device_name' | 'last_seen_at' | 'created_at'>>
export type ProfileInput = { name: string; description?: string | null; profile_type?: string; configuration: Record<string, string | number | boolean> }
export type RunInput = { project_id: number; app_build_id?: number; profile_version_ids: number[]; scenario_version_ids: number[]; requested_repetitions: number; execution_mode: 'runner' | 'actual_device'; actual_device_id?: number }
export type ImpactInput = { monthly_active_users: number; condition_group: string; population_share: number; observed_failure_count: number; total_test_attempts: number; data_source: string; assumptions: string[]; is_synthetic: boolean; mutually_exclusive: boolean }

export const authApi = {
  async login(email: string, password: string) { const { data } = await api.post<TokenPair>('/auth/login', { email, password }); tokens.save(data); return data },
  async register(email: string, password: string, options: RequestOptions = {}) { return (await api.post<User>('/auth/register', { email, password }, { signal: options.signal })).data },
  async refresh() {
    const refresh = tokens.refresh
    if (!refresh) throw new Error('No refresh token is available.')
    const { data } = await axios.post<TokenPair>(`${baseURL}/auth/refresh`, { refresh_token: refresh }, { timeout: 20_000, withCredentials: true })
    tokens.save(data); return data
  },
  async logout() {
    const refresh = tokens.refresh
    try { if (refresh) await api.post('/auth/logout', { refresh_token: refresh }) }
    finally { tokens.clear() }
  },
}

export const projectsApi = {
  list: (options?: RequestOptions) => fetchPages<Project>('/projects', options),
  get: async (id: number, options?: RequestOptions) => (await api.get<Project>(`/projects/${id}`, options)).data,
  create: async (body: ProjectInput, options?: RequestOptions) => (await api.post<Project>('/projects', body, options)).data,
  update: async (id: number, body: ProjectInput, options?: RequestOptions) => (await api.put<Project>(`/projects/${id}`, body, options)).data,
  delete: async (id: number) => { await api.delete(`/projects/${id}`) },
}

export const buildsApi = {
  list: async (projectId: number, options?: RequestOptions) => (await api.get<Build[]>(`/projects/${projectId}/builds`, options)).data,
  upload: async (projectId: number, file: File, packageName: string, versionName?: string, versionCode?: number, onProgress?: (value: number) => void, options?: RequestOptions) => {
    const form = new FormData(); form.append('file', file); form.append('package_name', packageName)
    if (versionName) form.append('version_name', versionName)
    if (versionCode !== undefined) form.append('version_code', String(versionCode))
    return (await api.post<BuildUpload>(`/projects/${projectId}/builds`, form, { signal: options?.signal, onUploadProgress: (event) => { if (event.total) onProgress?.(Math.round(event.loaded / event.total * 100)) } })).data
  },
}

export const devicesApi = {
  list: async (options?: RequestOptions) => (await api.get<Device[]>('/devices', options)).data,
  get: async (id: number, options?: RequestOptions) => (await api.get<Device>(`/devices/${id}`, options)).data,
  register: async (body: DeviceInput, options?: RequestOptions) => (await api.post<Device>('/devices/register', body, options)).data,
}

export const profilesApi = {
  list: async (options?: RequestOptions) => (await api.get<Profile[]>('/profiles', options)).data,
  get: async (id: number, options?: RequestOptions) => (await api.get<{ profile: Profile; versions: ProfileVersion[] }>(`/profiles/${id}`, options)).data,
  create: async (body: ProfileInput, options?: RequestOptions) => (await api.post<Profile>('/profiles', body, options)).data,
  createVersion: async (id: number, body: ProfileInput, options?: RequestOptions) => (await api.post<ProfileVersion>(`/profiles/${id}/versions`, body, options)).data,
}

export const scenariosApi = {
  list: async (options?: RequestOptions) => (await api.get<Scenario[]>('/scenarios', options)).data,
  get: async (id: number, options?: RequestOptions) => (await api.get<ScenarioDetail>(`/scenarios/${id}`, options)).data,
}

export const metricsApi = {
  listForRun: async (id: number, options?: MetricRequestOptions) => (await api.get<Metric[]>(`/test-runs/${id}/metrics`, { params: { limit: options?.limit ?? 5000 }, signal: options?.signal })).data,
}

export const findingsApi = {
  listForRun: async (id: number, options?: RequestOptions) => (await api.get<Finding[]>(`/test-runs/${id}/findings`, options)).data,
}

export const evidenceArtifactsApi = {
  listForRun: async (id: number, options?: RequestOptions) => (await api.get<Artifact[]>(`/test-runs/${id}/artifacts`, options)).data,
}

export const testRunsApi = {
  list: (options?: RequestOptions) => fetchPages<TestRun>('/test-runs', options),
  get: async (id: number, options?: RequestOptions) => (await api.get<TestRun>(`/test-runs/${id}`, options)).data,
  create: async (body: RunInput, idempotencyKey: string) => (await api.post<TestRun>('/test-runs', body, { headers: { 'Idempotency-Key': idempotencyKey } })).data,
  cancel: async (id: number) => (await api.post<TestRun>(`/test-runs/${id}/cancel`)).data,
  metrics: metricsApi.listForRun,
  findings: findingsApi.listForRun,
  artifacts: evidenceArtifactsApi.listForRun,
}

export const impactApi = {
  create: async (runId: number, body: ImpactInput) => (await api.post<ImpactEstimate>(`/test-runs/${runId}/impact-estimates`, body)).data,
}

// Compatibility facade for existing components while they move to query-backed reads.
export const dataApi = {
  projects: projectsApi.list, createProject: projectsApi.create, builds: buildsApi.list, uploadBuild: buildsApi.upload,
  devices: devicesApi.list, registerDevice: devicesApi.register, profiles: profilesApi.list, profile: profilesApi.get,
  createProfile: profilesApi.create, scenarios: scenariosApi.list, scenario: scenariosApi.get, runs: testRunsApi.list,
  run: testRunsApi.get, createRun: testRunsApi.create, cancelRun: testRunsApi.cancel, metrics: testRunsApi.metrics,
  findings: testRunsApi.findings, artifacts: testRunsApi.artifacts, impact: impactApi.create,
}

export type { AxiosRequestConfig }
