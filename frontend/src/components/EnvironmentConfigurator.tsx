import { useEffect } from 'react'
import { BatteryMedium, Cpu, HardDrive, Info, MemoryStick, MousePointerClick, Plus, Thermometer, Trash2, Wifi } from 'lucide-react'
import type { CapabilitiesResponse, EnvironmentRequest, Selector } from '../types'
import { BINDING_NAMES, SCREEN_PRESETS, TIMELINE_PRESET } from '../environment'
import './Environment.css'

type Section = 'device' | 'storage' | 'memory' | 'network' | 'battery' | 'thermal'
type Props = { value: EnvironmentRequest; onChange: (next: EnvironmentRequest) => void; capabilities?: CapabilitiesResponse }
type SelectorKind = keyof Selector
const SELECTOR_KINDS: { kind: SelectorKind; label: string }[] = [
  { kind: 'resource_id', label: 'Resource id' }, { kind: 'text', label: 'Exact text' }, { kind: 'text_contains', label: 'Text contains' }, { kind: 'content_desc', label: 'Content desc' },
]

function optionalNumber(raw: string): number | null {
  if (raw.trim() === '') return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

export default function EnvironmentConfigurator({ value, onChange, capabilities }: Props) {
  const emulator = value.environment_type === 'emulator'
  const worker = capabilities?.workers[0]
  const apiLevels = [...new Set((worker?.system_images ?? []).map((image) => image.api_level))].sort((a, b) => a - b)
  const apiLevelKey = apiLevels.join(',')
  const levelChoices = apiLevels.length ? apiLevels : [value.device.api_level]
  const minimumRamMb = value.device.api_level >= 37 ? 4096 : 1024
  const minimumDataPartitionMb = value.device.api_level >= 37 ? 6144 : 1024
  const ramChoices = [1024, 1536, 2048, 3072, 4096, 6144, 8192].filter((mb) => mb >= minimumRamMb)
  const dataPartitionChoices = [2048, 4096, 6144, 8192, 16384].filter((mb) => mb >= minimumDataPartitionMb)
  useEffect(() => {
    if (!apiLevelKey || value.environment_type !== 'emulator') return
    const installed = apiLevelKey.split(',').map(Number)
    const apiLevel = installed.includes(value.device.api_level) ? value.device.api_level : installed[installed.length - 1]
    const ramMb = apiLevel >= 37 ? Math.max(value.device.ram_mb, 4096) : value.device.ram_mb
    const dataPartitionMb = apiLevel >= 37 ? Math.max(value.device.data_partition_mb, 6144) : value.device.data_partition_mb
    if (apiLevel === value.device.api_level && ramMb === value.device.ram_mb && dataPartitionMb === value.device.data_partition_mb) return
    onChange({ ...value, device: { ...value.device, api_level: apiLevel, ram_mb: ramMb, data_partition_mb: dataPartitionMb } })
  }, [apiLevelKey, onChange, value])
  const set = <K extends Section>(key: K, patch: Partial<EnvironmentRequest[K]>) => onChange({ ...value, [key]: { ...value[key], ...patch } } as EnvironmentRequest)
  const screenPreset = (Object.entries(SCREEN_PRESETS).find(([, s]) => s.width_px === value.device.screen.width_px && s.height_px === value.device.screen.height_px)?.[0]) ?? '720p'
  const batteryMode = value.battery.charging === true ? 'charging' : value.battery.charging === false ? 'discharging' : ''
  const bindings = Object.entries(value.scenario_bindings)
  const setBindings = (next: [string, Selector][]) => onChange({ ...value, scenario_bindings: Object.fromEntries(next) })

  return <div className="form-stack">
    {emulator ? <>
      <div className="env-subhead"><Cpu size={14} /> Virtual device (applied to a disposable AVD, then verified inside Android)</div>
      <div className="env-grid three">
        <label className="env-field"><span>Android version (API)</span>
          <select value={value.device.api_level} onChange={(e) => set('device', { api_level: Number(e.target.value) })}>
            {levelChoices.map((level) => <option key={level} value={level}>API {level}{apiLevels.includes(level) ? '' : ' · not installed on worker'}</option>)}
          </select>
          <small>{apiLevels.length ? 'Only system images installed on the worker can boot.' : 'No worker image list yet; preflight will check.'}</small>
        </label>
        <label className="env-field"><span>Guest RAM</span>
          <select value={value.device.ram_mb} onChange={(e) => set('device', { ram_mb: Number(e.target.value) })}>
            {ramChoices.map((mb) => <option key={mb} value={mb}>{mb / 1024} GB</option>)}
          </select>
          <small>{value.device.api_level >= 37 ? 'API 37+ requires at least 4 GB RAM.' : 'hw.ramSize + -memory; MemTotal read back after boot.'}</small>
        </label>
        <label className="env-field"><span>CPU cores</span>
          <select value={value.device.cpu_cores} onChange={(e) => set('device', { cpu_cores: Number(e.target.value) })}>
            {[1, 2, 4].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="env-field"><span>Internal data partition</span>
          <select value={value.device.data_partition_mb} onChange={(e) => set('device', { data_partition_mb: Number(e.target.value) })}>
            {dataPartitionChoices.map((mb) => <option key={mb} value={mb}>{mb / 1024} GB</option>)}
          </select>
          {value.device.api_level >= 37 && <small>API 37+ phone images need at least a 6 GB data filesystem.</small>}
        </label>
        <label className="env-field"><span>Screen</span>
          <select value={screenPreset} onChange={(e) => set('device', { screen: { ...SCREEN_PRESETS[e.target.value as keyof typeof SCREEN_PRESETS] } })}>
            <option value="720p">720 × 1600 · 320 dpi</option><option value="1080p">1080 × 2400 · 420 dpi</option>
          </select>
        </label>
        <label className="env-field"><span>VM heap (optional)</span>
          <input type="number" min={64} max={1024} placeholder="image default" value={value.device.vm_heap_mb ?? ''} onChange={(e) => set('device', { vm_heap_mb: optionalNumber(e.target.value) })} />
        </label>
      </div>
    </> : <div className="env-note"><Info size={14} /><span>Physical device hardware (RAM, CPU, Android version) cannot be changed. The worker records the actual values and marks the requested hardware as unsupported.</span></div>}

    <div className="env-subhead"><HardDrive size={14} /> Storage</div>
    <div className="env-grid">
      <label className="env-field"><span>Free storage target (MB)</span>
        <input type="number" min={1} placeholder="not constrained" value={value.storage.free_target_mb ?? ''} onChange={(e) => set('storage', { free_target_mb: optionalNumber(e.target.value) })} />
        <small>Filled with bounded files in /data/local/tmp, verified with statfs, removed afterwards. Never below the worker's safety reserve.</small>
      </label>
    </div>

    <div className="env-subhead"><MemoryStick size={14} /> Memory pressure</div>
    <div className="env-grid">
      <label className="checkbox-row"><input type="checkbox" checked={value.memory.pressure_enabled} onChange={(e) => set('memory', { pressure_enabled: e.target.checked, target_available_mb: e.target.checked ? value.memory.target_available_mb ?? 300 : null })} /> Apply bounded in-guest memory pressure</label>
      {value.memory.pressure_enabled && <label className="env-field"><span>Target available RAM (MB)</span>
        <input type="number" min={64} value={value.memory.target_available_mb ?? ''} onChange={(e) => set('memory', { target_available_mb: optionalNumber(e.target.value) })} />
        <small>Needs a rootable google_apis image. Applied during the memory-pressure scenario.</small>
      </label>}
    </div>

    <div className="env-subhead"><Wifi size={14} /> Network</div>
    <div className="env-grid three">
      <label className="env-field"><span>Download (kbps)</span><input type="number" min={8} placeholder="unshaped" value={value.network.download_kbps ?? ''} onChange={(e) => set('network', { download_kbps: optionalNumber(e.target.value) })} /></label>
      <label className="env-field"><span>Upload (kbps)</span><input type="number" min={8} placeholder="unshaped" value={value.network.upload_kbps ?? ''} onChange={(e) => set('network', { upload_kbps: optionalNumber(e.target.value) })} /></label>
      <label className="env-field"><span>Latency (ms)</span><input type="number" min={0} placeholder="none" value={value.network.latency_ms ?? ''} onChange={(e) => set('network', { latency_ms: optionalNumber(e.target.value) })} /></label>
      <label className="env-field"><span>Jitter (ms)</span><input type="number" min={0} placeholder="none" value={value.network.jitter_ms ?? ''} onChange={(e) => set('network', { jitter_ms: optionalNumber(e.target.value) })} /></label>
      <label className="env-field"><span>Random packet loss (%)</span><input type="number" min={0} max={100} step={0.5} placeholder="none" value={value.network.packet_loss_pct ?? ''} onChange={(e) => set('network', { packet_loss_pct: optionalNumber(e.target.value) })} /><small>Needs the Linux netem gateway worker.</small></label>
      <label className="env-field"><span>Random seed</span><input type="number" min={0} placeholder="random" value={value.network.seed ?? ''} onChange={(e) => set('network', { seed: optionalNumber(e.target.value) })} /><small>Recorded for reproducible replays.</small></label>
    </div>
    <label className="checkbox-row"><input type="checkbox" checked={value.network.dns_failure} onChange={(e) => set('network', { dns_failure: e.target.checked })} /> Simulate DNS failure (Linux gateway only)</label>
    <label className="checkbox-row"><input type="checkbox" checked={value.network.timeline.length > 0} onChange={(e) => set('network', { timeline: e.target.checked ? TIMELINE_PRESET : [] })} /> Replay the 65-second unstable-network timeline during “Product image loading”</label>
    {value.network.timeline.length > 0 && <div className="env-timeline">{value.network.timeline.map((stage) => <div className="env-timeline-row" key={`${stage.start_s}-${stage.label}`}>
      <span>{stage.start_s}–{stage.end_s}s</span><span className={`env-timeline-bar ${stage.condition.online === false ? 'offline' : ''}`} title={stage.label} /><span>{stage.label}</span>
    </div>)}</div>}

    <div className="env-subhead"><BatteryMedium size={14} /> Battery {emulator ? '(simulated emulator battery state)' : '(OS-reported state override)'}</div>
    <div className="env-grid three">
      <label className="env-field"><span>Battery level (%)</span><input type="number" min={0} max={100} placeholder="not simulated" value={value.battery.level_pct ?? ''} onChange={(e) => set('battery', { level_pct: optionalNumber(e.target.value) })} /></label>
      <label className="env-field"><span>Power source</span>
        <select value={batteryMode} onChange={(e) => set('battery', e.target.value === '' ? { charging: null, ac_connected: null } : { charging: e.target.value === 'charging', ac_connected: e.target.value === 'charging', ...(e.target.value === 'charging' ? { saver_enabled: null } : {}) })}>
          <option value="">Not simulated</option><option value="discharging">Unplugged, discharging</option><option value="charging">AC charging</option>
        </select>
      </label>
      <label className="checkbox-row"><input type="checkbox" checked={value.battery.saver_enabled === true} onChange={(e) => set('battery', e.target.checked ? { saver_enabled: true, charging: false, ac_connected: false } : { saver_enabled: null })} /> Battery saver (real OS mode)</label>
    </div>
    <div className="env-note"><Info size={14} /><span>A simulated 20% battery does not mean any energy was consumed. Battery saver, however, is a genuine Android power mode and is verified from dumpsys power.</span></div>

    <div className="env-subhead"><Thermometer size={14} /> Thermal</div>
    <label className="checkbox-row"><input type="checkbox" checked={value.thermal.observe} onChange={(e) => set('thermal', { observe: e.target.checked })} /> Observe thermal status and temperatures</label>
    <label className="checkbox-row"><input type="checkbox" checked={value.thermal.require_physical_validation} onChange={(e) => set('thermal', { require_physical_validation: e.target.checked })} /> Validate real thermal throttling</label>
    <div className={`env-note ${emulator && value.thermal.require_physical_validation ? 'warn' : ''}`}><Info size={14} /><span>
      {emulator ? 'The Android Emulator does not reproduce phone heating or thermal throttling. Emulator thermal status is reported as EMULATED and no temperatures are reported. Throttling validation requires a physical device.'
        : 'Physical devices report thermal status, HAL temperatures and battery temperature separately. CPU temperature is shown only if the device exposes a CPU sensor.'}
    </span></div>

    <div className="env-subhead"><MousePointerClick size={14} /> App UI bindings (for app-specific checks)</div>
    <div className="env-note"><Info size={14} /><span>Scenarios such as cart-state preservation need to know which element is “Add to cart”. Without a binding those checks are reported as blocked — never as passed.</span></div>
    {bindings.map(([name, selector], index) => {
      const kind = (Object.keys(selector)[0] as SelectorKind | undefined) ?? 'text'
      const update = (nextName: string, nextKind: SelectorKind, nextValue: string) =>
        setBindings(bindings.map((entry, i): [string, Selector] => i === index ? [nextName, { [nextKind]: nextValue } as Selector] : entry))
      return <div className="env-binding-row" key={`${name}-${index}`}>
        <select aria-label="Binding name" value={name} onChange={(e) => update(e.target.value, kind, selector[kind] ?? '')}>
          {BINDING_NAMES.map((option) => <option key={option} value={option} disabled={option !== name && option in value.scenario_bindings}>{option}</option>)}
        </select>
        <select aria-label="Selector type" value={kind} onChange={(e) => update(name, e.target.value as SelectorKind, selector[kind] ?? '')}>
          {SELECTOR_KINDS.map((option) => <option key={option.kind} value={option.kind}>{option.label}</option>)}
        </select>
        <input aria-label="Selector value" placeholder={kind === 'resource_id' ? 'cart_badge' : 'Add to cart'} value={selector[kind] ?? ''} onChange={(e) => update(name, kind, e.target.value)} />
        <button type="button" className="icon-button" aria-label={`Remove ${name}`} onClick={() => setBindings(bindings.filter((_, i) => i !== index))}><Trash2 size={14} /></button>
      </div>
    })}
    {bindings.length < BINDING_NAMES.length && <button type="button" className="button button-secondary button-small" onClick={() => {
      const unused = BINDING_NAMES.find((name) => !(name in value.scenario_bindings))
      if (unused) setBindings([...bindings, [unused, { text: '' }] as [string, Selector]])
    }}><Plus size={14} /> Add binding</button>}

    <div className="env-subhead">Execution policy</div>
    <label className="checkbox-row"><input type="checkbox" checked={value.strict_conditions} onChange={(e) => onChange({ ...value, strict_conditions: e.target.checked })} /> Stop before scenarios if a supported condition fails verification</label>
    <label className="checkbox-row"><input type="checkbox" checked={value.grant_runtime_permissions} onChange={(e) => onChange({ ...value, grant_runtime_permissions: e.target.checked })} /> Grant runtime permissions at install (adb install -g)</label>
  </div>
}
