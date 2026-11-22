import { AlertTriangle, CheckCircle2, CircleX, ServerCrash } from 'lucide-react'
import type { CapabilitiesResponse, PreflightResult } from '../types'
import { PLAN_OUTCOME_LABEL, PLAN_OUTCOME_TONE, formatValue, parameterLabel } from '../environment'
import './Environment.css'

const KEY_CAPABILITIES = ['android_sdk', 'adb', 'emulator', 'virtualization', 'guest_ram_configuration', 'storage_free_space_control', 'network_latency',
  'network_bandwidth', 'packet_loss', 'network_jitter', 'battery_state_simulation', 'memory_pressure', 'perfetto', 'physical_thermal_validation']
const CAP_TONE: Record<string, string> = { available: 'ok', supported: 'ok', unavailable: 'bad', unsupported: 'limit', requires_physical_device: 'limit', requires_linux_gateway: 'limit', requires_rootable_image: 'limit' }

export function CapabilityPanel({ data, loading, error }: { data?: CapabilitiesResponse; loading: boolean; error?: string }) {
  if (loading) return <p className="field-help">Reading the worker's capability report…</p>
  if (error) return <div className="env-note bad"><CircleX size={14} /><span>{error}</span></div>
  if (!data || data.status !== 'available' || !data.workers.length) {
    return <div className="env-note bad"><ServerCrash size={14} /><span>{data?.message ?? 'No Android execution worker is online. Real execution cannot start until one reports its capabilities.'}</span></div>
  }
  const worker = data.workers[0]
  return <div className="capability-list">
    <small className="field-help">Worker <b>{worker.agent_name}</b> · {String(worker.host.os ?? '')} {String(worker.host.arch ?? '')} · {String(worker.host.ram_available_mb ?? '?')} MB RAM free · reported {new Date(worker.reported_at).toLocaleTimeString()}</small>
    {KEY_CAPABILITIES.filter((key) => worker.summary[key]).map((key) => <div className="capability-row" key={key} title={worker.details[key]?.reason ?? undefined}>
      <span>{key.replaceAll('_', ' ')}</span><span className={`tone-pill ${CAP_TONE[worker.summary[key]] ?? 'neutral'}`}>{worker.summary[key].replaceAll('_', ' ')}</span>
    </div>)}
    <small className="field-help">Installed images: {worker.system_images.length ? worker.system_images.map((i) => `API ${i.api_level} ${i.tag}`).join(', ') : 'none'}</small>
  </div>
}

export function PreflightSummary({ result }: { result: PreflightResult }) {
  const limitations = result.items.filter((item) => PLAN_OUTCOME_TONE[item.outcome] === 'limit')
  return <div className="preflight-card">
    <div className="preflight-head">
      <h2>{result.ok ? 'Preflight passed' : 'Preflight blocked'}</h2>
      <span className={`tone-pill ${result.ok ? 'ok' : 'bad'}`}>{result.ok ? <CheckCircle2 size={12} /> : <CircleX size={12} />}{result.environment_type === 'emulator' ? 'Emulator test' : 'Physical Android test'}</span>
    </div>
    {result.blocking_errors.map((error) => <div className="env-note bad" key={error}><CircleX size={14} /><span>{error}</span></div>)}
    {result.warnings.map((warning) => <div className="env-note warn" key={warning}><AlertTriangle size={14} /><span>{warning}</span></div>)}
    {result.selected_system_image && <small className="field-help">System image: {result.selected_system_image.package}{result.selected_system_image.rootable ? '' : ' (not rootable)'}</small>}
    {limitations.length > 0 && <div className="limitations"><h3><AlertTriangle size={14} /> Will NOT be applied in this run</h3><ul>
      {limitations.map((item) => <li key={item.parameter_key}><b>{parameterLabel(item.parameter_key)}</b> ({formatValue(item.requested_value, item.unit)}) — {PLAN_OUTCOME_LABEL[item.outcome]}{item.reason ? `: ${item.reason}` : ''}</li>)}
    </ul></div>}
    {result.items.length > 0 && <div className="metric-table-wrap"><table className="data-table plan-table"><thead><tr><th>Condition</th><th>Requested</th><th>Plan</th><th>How / why</th></tr></thead><tbody>
      {result.items.map((item) => <tr key={item.parameter_key}>
        <td>{parameterLabel(item.parameter_key)}</td><td>{formatValue(item.requested_value, item.unit)}</td>
        <td><span className={`tone-pill ${PLAN_OUTCOME_TONE[item.outcome]}`}>{PLAN_OUTCOME_LABEL[item.outcome]}</span></td>
        <td>{[item.mechanism, item.reason].filter(Boolean).join(' — ') || '—'}</td>
      </tr>)}
    </tbody></table></div>}
    {result.capability_report_at && <small className="field-help">Based on the worker capability report from {new Date(result.capability_report_at).toLocaleString()}. The worker re-checks before provisioning.</small>}
  </div>
}
