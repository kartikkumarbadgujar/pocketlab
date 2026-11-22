import type { ApplicationStatus, EnvironmentRequest, EnvironmentStatus, EnvironmentType, MeasurementStatus, PlanOutcome, ScenarioRunStatus, TimelineStage } from './types'

export const BINDING_NAMES = ['add_to_cart', 'cart_indicator', 'product_list', 'product_image', 'trigger_api_call', 'api_result', 'state_indicator', 'trigger_download', 'scroll_screen'] as const

/** The 65-second unstable-network replay: stable, latency spike, outage, lossy recovery, high jitter. */
export const TIMELINE_PRESET: TimelineStage[] = [
  { start_s: 0, end_s: 30, label: 'Stable connection', condition: { online: true } },
  { start_s: 30, end_s: 35, label: '500 ms latency', condition: { online: true, latency_ms: 500 } },
  { start_s: 35, end_s: 45, label: 'Network offline', condition: { online: false } },
  { start_s: 45, end_s: 55, label: 'Restored, 3% random loss', condition: { online: true, packet_loss_pct: 3 } },
  { start_s: 55, end_s: 65, label: 'High jitter', condition: { online: true, latency_ms: 200, jitter_ms: 150 } },
]

export const SCREEN_PRESETS = {
  '720p': { width_px: 720, height_px: 1600, density_dpi: 320 },
  '1080p': { width_px: 1080, height_px: 2400, density_dpi: 420 },
} as const

export function defaultRequest(environmentType: EnvironmentType = 'emulator'): EnvironmentRequest {
  return {
    environment_type: environmentType,
    device: { api_level: 30, ram_mb: 2048, cpu_cores: 2, data_partition_mb: 4096, sdcard_mb: 0, vm_heap_mb: null, screen: { ...SCREEN_PRESETS['720p'] } },
    storage: { free_target_mb: null },
    memory: { pressure_enabled: false, target_available_mb: null },
    network: { online: true, download_kbps: null, upload_kbps: null, latency_ms: null, jitter_ms: null, packet_loss_pct: null, duplication_pct: null, reorder_pct: null, dns_failure: false, timeline: [], seed: null },
    battery: { level_pct: null, charging: null, ac_connected: null, saver_enabled: null },
    thermal: { observe: true, require_physical_validation: false, sustained_workload_seconds: 0 },
    scenario_bindings: {},
    grant_runtime_permissions: true,
    strict_conditions: true,
  }
}

const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null)

/** Prefill from a saved condition-profile version (same mapping as the backend's request_from_profile). */
export function requestFromProfile(config: Record<string, unknown>, environmentType: EnvironmentType): EnvironmentRequest {
  const request = defaultRequest(environmentType)
  const ram = num(config['device.target_ram_gb'])
  if (ram) request.device.ram_mb = Math.round(ram * 1024)
  request.storage.free_target_mb = num(config['storage.free_target_mb'])
  if (request.storage.free_target_mb && request.storage.free_target_mb >= request.device.data_partition_mb) request.device.data_partition_mb = 8192
  request.network.latency_ms = num(config['network.latency_ms']) || null
  request.network.jitter_ms = num(config['network.jitter_ms']) || null
  request.network.packet_loss_pct = num(config['network.packet_loss_pct']) || null
  const level = num(config['battery.level_pct'])
  if (level !== null) request.battery = { ...request.battery, level_pct: level, charging: false, ac_connected: false }
  if (config['battery.saver_enabled'] === true) request.battery = { ...request.battery, saver_enabled: true, charging: false, ac_connected: false }
  const online = num(config['network.online_seconds']), offline = num(config['network.offline_seconds'])
  if (online && offline) {
    const stages: TimelineStage[] = []
    let t = 0
    for (let cycle = 1; cycle <= 3; cycle += 1) {
      stages.push({ start_s: t, end_s: t + online, label: `online ${cycle}`, condition: { online: true } }); t += online
      stages.push({ start_s: t, end_s: t + offline, label: `offline ${cycle}`, condition: { online: false } }); t += offline
    }
    request.network.timeline = stages
  }
  return request
}

export const PLAN_OUTCOME_LABEL: Record<PlanOutcome, string> = {
  will_apply: 'Will apply & verify', emulated: 'Emulated', configured_only: 'Configured only', unsupported: 'Unsupported',
  requires_physical_device: 'Needs physical device', requires_linux_gateway: 'Needs Linux gateway', requires_rootable_image: 'Needs rootable image', not_requested: 'Not requested',
}
export const PLAN_OUTCOME_TONE: Record<PlanOutcome, string> = {
  will_apply: 'ok', emulated: 'emulated', configured_only: 'warn', unsupported: 'limit', requires_physical_device: 'limit', requires_linux_gateway: 'limit', requires_rootable_image: 'limit', not_requested: 'neutral',
}
export const APPLICATION_LABEL: Record<ApplicationStatus, string> = {
  applied_verified: 'Applied · verified', applied_unverified: 'Applied · not verified', emulated: 'Emulated', configured: 'Configured only', unsupported: 'Unsupported',
  requires_physical_device: 'Needs physical device', requires_linux_gateway: 'Needs Linux gateway', failed: 'Failed verification', reverted: 'Reverted',
}
export const APPLICATION_TONE: Record<ApplicationStatus, string> = {
  applied_verified: 'ok', applied_unverified: 'warn', emulated: 'emulated', configured: 'warn', unsupported: 'limit', requires_physical_device: 'limit', requires_linux_gateway: 'limit', failed: 'bad', reverted: 'neutral',
}
export const LIMITATION_STATUSES = new Set<ApplicationStatus>(['unsupported', 'requires_physical_device', 'requires_linux_gateway', 'configured'])
export const MEASUREMENT_TONE: Record<MeasurementStatus, string> = { ACTUAL: 'ok', EMULATED: 'emulated', UNSUPPORTED: 'limit', UNAVAILABLE: 'neutral', INVALID: 'bad' }
export const SCENARIO_TONE: Record<ScenarioRunStatus, string> = { queued: 'neutral', running: 'active', passed: 'ok', failed: 'bad', blocked: 'limit', error: 'bad', inconclusive: 'warn' }

export const STAGE_COPY: Record<EnvironmentStatus, { title: string; detail: string }> = {
  PREPARED: { title: 'Configuration prepared', detail: 'Preflight passed; waiting for an explicit execute.' },
  QUEUED: { title: 'Queued for an Android worker', detail: 'Waiting for the execution worker to claim the run.' },
  PREFLIGHT: { title: 'Worker preflight', detail: 'Re-checking SDK, system image, acceleration and host resources.' },
  PROVISIONING: { title: 'Provisioning disposable AVD', detail: 'Creating a uniquely named AVD from the selected system image.' },
  BOOTING: { title: 'Booting Android', detail: 'Emulator started; waiting for sys.boot_completed and the package manager.' },
  READY: { title: 'Device ready over ADB', detail: 'Actual device properties recorded and requested hardware verified.' },
  INSTALLING: { title: 'Installing APK', detail: 'SHA-256, manifest, minSdk and storage checks, then adb install.' },
  APPLYING_CONDITIONS: { title: 'Applying conditions', detail: 'Storage, battery and network conditions on the device.' },
  VERIFYING_CONDITIONS: { title: 'Verifying conditions', detail: 'Independent read-back of every applied condition.' },
  EXECUTING: { title: 'Executing scenarios', detail: 'Running app journeys and validating expected behaviour.' },
  COLLECTING_METRICS: { title: 'Collecting measurements', detail: 'Final app, device, battery and thermal readings.' },
  ANALYZING: { title: 'Analysing results', detail: 'Deriving findings from expectations, crashes and ANRs.' },
  COMPLETED: { title: 'Execution completed', detail: 'All stages finished; see scenario outcomes for pass/fail.' },
  FAILED: { title: 'Execution failed', detail: 'The environment stopped before completing; see the event log.' },
  CANCEL_REQUESTED: { title: 'Cancellation requested', detail: 'The worker is stopping the run.' },
  CLEANING: { title: 'Cleaning up', detail: 'Reverting conditions, stopping the emulator, removing the AVD.' },
  CLEANED: { title: 'Resources released', detail: 'Temporary AVD and run files removed.' },
}

export function formatValue(value: unknown, unit?: string | null): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return `${Number.isInteger(value) ? value : value.toFixed(1)}${unit ? ` ${unit}` : ''}`
  if (typeof value === 'string') return `${value}${unit && !value.endsWith(unit) ? ` ${unit}` : ''}`
  if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? '' : 's'}`
  return Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => `${k.replaceAll('_', ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ') || '—'
}

/** Drop incomplete UI bindings before sending (the API rejects empty selectors). */
export function cleanRequest(request: EnvironmentRequest): EnvironmentRequest {
  const bindings = Object.fromEntries(Object.entries(request.scenario_bindings).filter(([, selector]) => Object.values(selector).some((v) => typeof v === 'string' && v.trim() !== '')))
  return { ...request, scenario_bindings: bindings }
}

export const parameterLabel = (key: string) => key.replace(/^[a-z]+\./, '').replaceAll('_', ' ').replaceAll('.', ' ')
