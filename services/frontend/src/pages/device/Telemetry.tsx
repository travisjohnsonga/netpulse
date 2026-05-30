import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchTelemetryConfig, saveTelemetryConfig, fetchMonitoredInterfaces,
  discoverInterfaces, saveMonitoredInterfaces,
  generateTelemetryConfig, pushTelemetryConfig, fetchPushHistory, checkHealth, fetchSystemSettings,
  fetchDeviceMetrics,
  type DeviceDetail, type TelemetryConfig, type GeneratedConfig, type ConfigPushRecord,
  type MonitoredInterface, type DeviceMetrics, type MetricPoint,
} from '../../api/client'
import Modal from '../../components/Modal'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'

const inputCls =
  'px-3 py-2 text-sm border border-gray-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500'

const METRIC_TOGGLES: [keyof TelemetryConfig, string][] = [
  ['collect_cpu', 'CPU utilization'],
  ['collect_memory', 'Memory usage'],
  ['collect_temperature', 'Temperature sensors'],
  ['collect_power', 'Power supplies'],
  ['collect_fans', 'Fan status'],
  ['collect_bgp', 'BGP neighbors'],
  ['collect_inventory', 'Hardware inventory'],
  ['collect_lldp', 'LLDP neighbors'],
]

interface Row {
  if_name: string
  if_index: number | null
  if_description: string
  if_speed_mbps: number | null
  if_type: string
  status: string
  lldp_neighbor_hostname: string | null
  lldp_neighbor_port: string | null
  lldp_neighbor_desc: string | null
  collection_method: string
  alert_on_down: boolean
  alert_on_up: boolean
  alert_severity: 'critical' | 'high' | 'medium' | 'low'
}

function formatSpeed(mbps: number | null): string {
  if (!mbps) return '—'
  if (mbps >= 1000 && mbps % 1000 === 0) return `${mbps / 1000}G`
  if (mbps >= 1000) return `${(mbps / 1000).toFixed(1)}G`
  return `${mbps}M`
}

const STATUS_BADGE: Record<string, string> = {
  up: 'bg-green-100 text-green-700',
  down: 'bg-red-100 text-red-700',
  unknown: 'bg-gray-100 text-gray-500',
}

/**
 * Telemetry configuration UI — device metrics, interval overrides, the
 * monitored-interface table and the generated config. Rendered inside the
 * Settings → Telemetry Configuration slide-over (not the Telemetry tab).
 */
function TelemetryConfigInner({ device }: { device: DeviceDetail }) {
  const [cfg, setCfg] = useState<TelemetryConfig | null>(null)
  const [cfgError, setCfgError] = useState<string | null>(null)

  useEffect(() => {
    fetchTelemetryConfig(device.id).then(setCfg).catch(() => setCfgError('Failed to load telemetry config.'))
  }, [device.id])

  return (
    <div className="space-y-4">
      <DevicePolling device={device} cfg={cfg} setCfg={setCfg} error={cfgError} setError={setCfgError} />
      <InterfacePolling device={device} cfg={cfg} />
      <GeneratedConfigSection device={device} />
    </div>
  )
}

/** Right-side slide-over hosting the full telemetry configuration. */
export function TelemetryConfigPanel({ device, onClose }: { device: DeviceDetail; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div className="bg-gray-50 dark:bg-gray-950 w-full max-w-3xl h-full shadow-xl flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
          <div>
            <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">Telemetry Configuration</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">{device.hostname}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none">×</button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          <TelemetryConfigInner device={device} />
        </div>
        <div className="px-5 py-3 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 flex justify-end">
          <button onClick={onClose} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium">Save & Close</button>
        </div>
      </div>
    </div>
  )
}

// ── Section 1: device-level metrics + polling intervals ──────────────────────

const PRESETS: Record<string, { label: string; desc: string }> = {
  normal:          { label: 'Normal',          desc: 'Use global defaults' },
  troubleshooting: { label: 'Troubleshooting', desc: '30s — high-resolution' },
  reduced:         { label: 'Reduced',         desc: '600s — light load' },
  custom:          { label: 'Custom',          desc: 'Set each interval' },
}

const INTERVAL_KEYS: [keyof TelemetryConfig, keyof TelemetryConfig['effective_intervals'], string][] = [
  ['device_metrics_interval', 'device_metrics', 'Device metrics'],
  ['interface_traffic_interval', 'interface_traffic', 'Interface traffic'],
  ['interface_status_interval', 'interface_status', 'Interface status'],
  ['bgp_interval', 'bgp', 'BGP peers'],
]

function DevicePolling({ device, cfg, setCfg, error, setError }: {
  device: DeviceDetail
  cfg: TelemetryConfig | null
  setCfg: (c: TelemetryConfig) => void
  error: string | null
  setError: (e: string | null) => void
}) {
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showIntervals, setShowIntervals] = useState(false)
  const [preset, setPreset] = useState<string>('custom')

  // Derive the preset from the current values once the config loads.
  useEffect(() => {
    if (!cfg) return
    if (!cfg.override_intervals) { setPreset('normal'); return }
    const vals = INTERVAL_KEYS.map(([k]) => cfg[k] as number | null)
    if (vals.every((v) => v === 30)) setPreset('troubleshooting')
    else if (vals.every((v) => v === 600)) setPreset('reduced')
    else setPreset('custom')
  }, [cfg])

  if (error && !cfg) return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
  if (!cfg) return <div className="bg-white rounded-lg border border-gray-200 p-4"><div className="w-5 h-5 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>

  const set = (patch: Partial<TelemetryConfig>) => setCfg({ ...cfg, ...patch })

  const applyPreset = (p: string) => {
    setPreset(p)
    if (p === 'normal') {
      set({ override_intervals: false })
    } else if (p === 'troubleshooting' || p === 'reduced') {
      const v = p === 'troubleshooting' ? 30 : 600
      set({
        override_intervals: true,
        device_metrics_interval: v, interface_traffic_interval: v,
        interface_status_interval: v, bgp_interval: v,
      })
    } else {
      set({ override_intervals: true })
    }
  }

  const save = async () => {
    setSaving(true); setError(null)
    try {
      setCfg(await saveTelemetryConfig(device.id, cfg))
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    } catch { setError('Failed to save settings.') } finally { setSaving(false) }
  }

  const eff = cfg.effective_intervals

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-800 mb-3">Device-Level Metrics</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
        {METRIC_TOGGLES.map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={cfg[key] as boolean} onChange={(e) => set({ [key]: e.target.checked } as Partial<TelemetryConfig>)} />
            {label}
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Collection method</label>
          <select className={inputCls} value={cfg.primary_method} onChange={(e) => set({ primary_method: e.target.value as TelemetryConfig['primary_method'] })}>
            <option value="snmp">SNMP</option>
            <option value="gnmi">gNMI</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">SNMP interval (s)</label>
          <input type="number" className={`${inputCls} w-28`} value={cfg.snmp_interval} onChange={(e) => set({ snmp_interval: Number(e.target.value) })} />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">gNMI interval (s)</label>
          <input type="number" className={`${inputCls} w-28`} value={cfg.gnmi_interval} onChange={(e) => set({ gnmi_interval: Number(e.target.value) })} />
        </div>
        <button onClick={save} disabled={saving} className={clsx('px-4 py-2 text-sm rounded-lg font-medium text-white', saved ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700')}>
          {saved ? 'Saved!' : saving ? 'Saving…' : 'Save Settings'}
        </button>
      </div>

      {/* Polling intervals (collapsible per-device override) */}
      <div className="mt-4 border-t border-gray-100 pt-3">
        <button onClick={() => setShowIntervals((v) => !v)} className="flex items-center gap-2 text-sm font-medium text-gray-700 hover:text-gray-900">
          <span className={clsx('transition-transform', showIntervals && 'rotate-90')}>▶</span>
          Polling Intervals
          <span className="text-xs font-normal text-gray-400">
            {cfg.override_intervals ? `overridden · ${PRESETS[preset].label}` : 'using global defaults'}
          </span>
        </button>
        {showIntervals && (
          <div className="mt-3 space-y-3">
            <p className="text-xs text-gray-400">
              These intervals come from <Link to="/settings/polling" className="text-blue-600 hover:text-blue-800">Settings → Polling</Link> unless you override them for this device.
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Preset</label>
                <select className={inputCls} value={preset} onChange={(e) => applyPreset(e.target.value)}>
                  {Object.entries(PRESETS).map(([k, p]) => <option key={k} value={k}>{p.label} — {p.desc}</option>)}
                </select>
              </div>
              <label className="flex items-center gap-2 text-sm text-gray-700 pb-2">
                <input type="checkbox" checked={cfg.override_intervals} onChange={(e) => { set({ override_intervals: e.target.checked }); setPreset(e.target.checked ? 'custom' : 'normal') }} />
                Override global intervals for this device
              </label>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {INTERVAL_KEYS.map(([k, ek, label]) => (
                <div key={k}>
                  <label className="block text-xs text-gray-500 mb-1">{label}</label>
                  <div className="flex items-center gap-1">
                    <input
                      type="number" min={5}
                      disabled={!cfg.override_intervals}
                      className={`${inputCls} w-24 disabled:bg-gray-50 disabled:text-gray-400`}
                      value={(cfg[k] as number | null) ?? eff[ek]}
                      onChange={(e) => { set({ [k]: Number(e.target.value) } as Partial<TelemetryConfig>); setPreset('custom') }}
                    />
                    <span className="text-xs text-gray-400">s</span>
                  </div>
                  <p className="text-[11px] text-gray-400 mt-0.5">effective {eff[ek]}s</p>
                </div>
              ))}
            </div>
            <p className="text-xs text-gray-400">
              ℹ️ gNMI subscription intervals are configured on the device, not by these timers. Use Generate Config below to update and push a new gNMI interval.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Section 2: interface selection ────────────────────────────────────────────

function InterfacePolling({ device, cfg }: { device: DeviceDetail; cfg: TelemetryConfig | null }) {
  const [rows, setRows] = useState<Row[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [discovering, setDiscovering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [discoverMsg, setDiscoverMsg] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchMonitoredInterfaces(device.id)
      .then((mi) => {
        setRows(mi.map((m) => ({
          if_name: m.if_name, if_index: m.if_index, if_description: m.if_description,
          if_speed_mbps: m.if_speed_mbps, if_type: m.if_type, status: m.last_status,
          lldp_neighbor_hostname: m.lldp_neighbor_hostname, lldp_neighbor_port: m.lldp_neighbor_port,
          lldp_neighbor_desc: m.lldp_neighbor_desc, collection_method: m.collection_method,
          alert_on_down: m.alert_on_down, alert_on_up: m.alert_on_up, alert_severity: m.alert_severity,
        })))
        setSelected(new Set(mi.map((m) => m.if_name)))
      })
      .catch(() => setError('Failed to load interfaces.'))
      .finally(() => setLoading(false))
  }, [device.id])

  useEffect(() => { load() }, [load])

  const discover = async () => {
    setDiscovering(true); setError(null); setDiscoverMsg(null)
    try {
      const res = await discoverInterfaces(device.id)
      if (res.error && !res.interfaces?.length) { setError(res.error); return }
      const prevSelected = selected
      setRows(res.interfaces.map((d) => ({
        if_name: d.if_name, if_index: d.if_index, if_description: d.if_description,
        if_speed_mbps: d.if_speed_mbps, if_type: d.if_type, status: d.oper_status,
        lldp_neighbor_hostname: d.lldp_neighbor_hostname, lldp_neighbor_port: d.lldp_neighbor_port,
        lldp_neighbor_desc: d.lldp_neighbor_desc, collection_method: d.collection_method,
        alert_on_down: true, alert_on_up: true, alert_severity: 'high' as const,
      })))
      // Pre-check auto-selected interfaces (and anything already monitored).
      const next = new Set<string>()
      for (const d of res.interfaces) if (d.auto_select || prevSelected.has(d.if_name)) next.add(d.if_name)
      setSelected(next)
      setDiscoverMsg(`Found ${res.count} interface${res.count !== 1 ? 's' : ''}, ${res.auto_selected} auto-selected`)
    } catch { setError('Discovery request failed.') } finally { setDiscovering(false) }
  }

  const filtered = useMemo(() => {
    if (!filter.trim()) return rows
    let test: (s: string) => boolean
    try { const re = new RegExp(filter, 'i'); test = (s) => re.test(s) }
    catch { const f = filter.toLowerCase(); test = (s) => s.toLowerCase().includes(f) }
    return rows.filter((r) => test(r.if_name) || test(r.if_description))
  }, [rows, filter])

  const toggle = (name: string) => setSelected((s) => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n })
  const selectAll = () => setSelected(new Set(filtered.map((r) => r.if_name)))
  const selectNone = () => setSelected(new Set())
  const selectUp = () => setSelected(new Set(filtered.filter((r) => r.status === 'up').map((r) => r.if_name)))

  const toggleAlert = (name: string) =>
    setRows((rs) => rs.map((r) => (r.if_name === name ? { ...r, alert_on_down: !r.alert_on_down } : r)))
  const bulkAlert = (on: boolean) =>
    setRows((rs) => rs.map((r) => (selected.has(r.if_name) ? { ...r, alert_on_down: on, alert_on_up: on } : r)))

  const save = async () => {
    setSaving(true); setError(null); setSavedMsg(null)
    const payload = rows.filter((r) => selected.has(r.if_name)).map((r) => ({
      if_name: r.if_name, if_index: r.if_index, if_description: r.if_description,
      if_speed_mbps: r.if_speed_mbps, if_type: r.if_type,
      lldp_neighbor_hostname: r.lldp_neighbor_hostname, lldp_neighbor_port: r.lldp_neighbor_port,
      lldp_neighbor_desc: r.lldp_neighbor_desc, oper_status: r.status,
      poll_traffic: true, poll_errors: true, poll_status: true,
      collection_method: r.collection_method || 'auto',
      alert_on_down: r.alert_on_down, alert_on_up: r.alert_on_up, alert_severity: r.alert_severity,
    }))
    try {
      await saveMonitoredInterfaces(device.id, payload)
      setSavedMsg(`Saved ${payload.length} interface${payload.length !== 1 ? 's' : ''}`)
      load()
    } catch { setError('Failed to save selection.') } finally { setSaving(false) }
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-gray-200">
        <h3 className="text-sm font-semibold text-gray-800 mr-auto">Monitored Interfaces</h3>
        <button onClick={discover} disabled={discovering} className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
          {discovering ? 'Discovering…' : '🔍 Discover Interfaces'}
        </button>
      </div>

      {error && <div className="bg-yellow-50 border-b border-yellow-200 px-4 py-2 text-sm text-yellow-800">{error}</div>}
      {discoverMsg && <div className="bg-blue-50 border-b border-blue-200 px-4 py-2 text-sm text-blue-800">{discoverMsg}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-12"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
      ) : rows.length === 0 ? (
        <div className="py-14 text-center">
          <div className="text-4xl mb-2">🔌</div>
          <p className="text-sm text-gray-600 font-medium">No interfaces configured for monitoring</p>
          <p className="text-xs text-gray-400 mt-1 mb-4">Click Discover to find interfaces on this device.</p>
          <button onClick={discover} disabled={discovering} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
            {discovering ? 'Discovering…' : 'Discover Interfaces'}
          </button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-gray-100">
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter (regex)…" className={`${inputCls} flex-1 min-w-[10rem]`} />
            <button onClick={selectAll} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Select All</button>
            <button onClick={selectNone} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Select None</button>
            <button onClick={selectUp} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Select Up Only</button>
            <button onClick={() => bulkAlert(true)} disabled={!selected.size} title="Enable down/up alerts for selected" className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50">🔔 Enable Alerts</button>
            <button onClick={() => bulkAlert(false)} disabled={!selected.size} title="Disable alerts for selected" className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50">🔕 Disable Alerts</button>
          </div>
          <div className="overflow-x-auto max-h-[26rem]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-50">
                <tr className="text-gray-500 text-left border-b border-gray-200">
                  <th className="px-3 py-2 w-8"></th>
                  <th className="px-3 py-2 font-medium">Interface</th>
                  <th className="px-3 py-2 font-medium">Description</th>
                  <th className="px-3 py-2 font-medium">LLDP Neighbor</th>
                  <th className="px-3 py-2 font-medium">Speed</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Method</th>
                  <th className="px-3 py-2 font-medium">Alert</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filtered.map((r) => (
                  <tr key={r.if_name} className="hover:bg-gray-50">
                    <td className="px-3 py-1.5"><input type="checkbox" checked={selected.has(r.if_name)} onChange={() => toggle(r.if_name)} /></td>
                    <td className="px-3 py-1.5 font-mono text-xs text-gray-800">{r.if_name}</td>
                    <td className={clsx('px-3 py-1.5', r.if_description ? 'text-gray-700' : 'text-gray-300')}>{r.if_description || '—'}</td>
                    <td className="px-3 py-1.5">{r.lldp_neighbor_hostname ? <span className="text-blue-600">{r.lldp_neighbor_hostname}</span> : <span className="text-gray-300">—</span>}</td>
                    <td className="px-3 py-1.5 text-gray-600">{formatSpeed(r.if_speed_mbps)}</td>
                    <td className="px-3 py-1.5"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_BADGE[r.status] ?? STATUS_BADGE.unknown)}>{r.status}</span></td>
                    <td className="px-3 py-1.5">
                      <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">
                        {r.collection_method === 'gnmi'
                          ? `gNMI ${cfg?.gnmi_interval ?? '—'}s`
                          : `SNMP ${cfg?.effective_intervals.interface_traffic ?? '—'}s`}
                      </span>
                    </td>
                    <td className="px-3 py-1.5">
                      <button
                        onClick={() => toggleAlert(r.if_name)}
                        title={r.alert_on_down ? `Alerting on (severity: ${r.alert_severity}) — click to mute` : 'Alerts muted — click to enable'}
                        className="text-base leading-none"
                      >
                        {r.alert_on_down ? '🔔' : '🔕'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-3 px-4 py-3 border-t border-gray-200">
            <span className="text-xs text-gray-500 mr-auto">{selected.size} of {rows.length} selected{savedMsg && ` · ${savedMsg}`}</span>
            <button onClick={save} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
              {saving ? 'Saving…' : 'Save Selected'}
            </button>
          </div>
        </>
      )}
      <p className="px-4 pb-3 text-xs text-gray-400">
        Discovery uses this device's credential profile. Manage it under{' '}
        <Link to="/settings/credentials" className="text-blue-600 hover:text-blue-800">Settings → Credentials</Link>.
      </p>
    </div>
  )
}

// ── Section 3: generated configuration + push ─────────────────────────────────

const CONFIG_TABS = ['snmp', 'syslog', 'gnmi', 'netflow', 'all'] as const
const TAB_LABELS: Record<string, string> = { snmp: 'SNMP', syslog: 'Syslog', gnmi: 'gNMI', netflow: 'NetFlow', all: 'All' }

function GeneratedConfigSection({ device }: { device: DeviceDetail }) {
  const [gen, setGen] = useState<GeneratedConfig | null>(null)
  const [tab, setTab] = useState<string>('snmp')
  const [collectorIp, setCollectorIp] = useState<string>('')
  const [pushAllowed, setPushAllowed] = useState<boolean>(true)
  const [history, setHistory] = useState<ConfigPushRecord[]>([])
  const [copied, setCopied] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [confirm, setConfirm] = useState<{ sections: string[]; config: string } | null>(null)
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadHistory = useCallback(() => { fetchPushHistory(device.id).then(setHistory).catch(() => {}) }, [device.id])

  useEffect(() => {
    generateTelemetryConfig(device.id).then(setGen).catch(() => setError('Failed to generate config.'))
    checkHealth().then((h) => setCollectorIp(h.collector_ip ?? '')).catch(() => {})
    fetchSystemSettings().then((s) => setPushAllowed(s.allow_config_push)).catch(() => {})
    loadHistory()
  }, [device.id, loadHistory])

  const currentConfig = useMemo(() => {
    if (!gen) return ''
    if (tab === 'all') return gen.full_config
    return gen.sections[tab]?.config ?? ''
  }, [gen, tab])

  const copy = async () => {
    try { await navigator.clipboard.writeText(currentConfig); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ }
  }

  const doPush = async (sections: string[]) => {
    setPushing(true); setConfirm(null); setToast(null)
    try {
      const res = await pushTelemetryConfig(device.id, sections)
      setToast(res.success
        ? { ok: true, msg: `Config pushed successfully (${res.pushed_sections.join(', ')})` }
        : { ok: false, msg: res.errors.join('; ') || 'Push failed' })
      loadHistory()
    } catch {
      setToast({ ok: false, msg: 'Push request failed' })
    } finally { setPushing(false) }
  }

  const openPush = () => {
    if (!gen) return
    const sections = tab === 'all'
      ? Object.keys(gen.sections).filter((s) => gen.sections[s].config)
      : [tab]
    setConfirm({ sections, config: currentConfig })
  }

  if (error) return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
  if (!gen) return <div className="bg-white rounded-lg border border-gray-200 p-4 text-sm text-gray-400">Generating configuration…</div>

  const platformLabel = gen.platform || device.platform

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200">
      <div className="px-4 py-3 border-b border-gray-200">
        <h3 className="text-sm font-semibold text-gray-800">Generated Configuration</h3>
      </div>

      {!collectorIp && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-sm text-amber-800">
          ⚠️ Collector IP not configured — snippets use a blank target.{' '}
          <Link to="/settings/general" className="text-blue-600 hover:text-blue-800">Configure it in Settings → General</Link>.
        </div>
      )}
      {toast && (
        <div className={clsx('border-b px-4 py-2 text-sm', toast.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
          {toast.ok ? '✅' : '❌'} {toast.msg}
        </div>
      )}

      <div className="flex gap-1 px-4 pt-3 border-b border-gray-100">
        {CONFIG_TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx('px-3 py-1.5 text-sm font-medium border-b-2 -mb-px', tab === t ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-800')}>
            {TAB_LABELS[t]}
            {t !== 'all' && gen.sections[t]?.enabled && <span className="ml-1 w-1.5 h-1.5 inline-block rounded-full bg-green-500" />}
          </button>
        ))}
      </div>

      <div className="p-4">
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs text-gray-500">{TAB_LABELS[tab]} configuration for {platformLabel}{tab !== 'all' && !gen.sections[tab]?.enabled && ' (not enabled by default)'}</p>
          <button onClick={copy} disabled={!currentConfig} className="px-2 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50">{copied ? 'Copied!' : '📋 Copy'}</button>
        </div>
        {tab === 'gnmi' && (
          <p className="text-xs text-gray-400 mb-2" title="gNMI subscription interval is configured on the device. Use Generate Config to update and push a new interval.">
            ℹ️ gNMI subscription interval is set on the device — change it here, then push.
          </p>
        )}
        {currentConfig ? (
          <pre className="bg-gray-900 text-gray-100 text-xs font-mono rounded-md p-3 overflow-x-auto max-h-72 whitespace-pre-wrap">{currentConfig}</pre>
        ) : (
          <p className="text-sm text-gray-400 py-6 text-center">No {TAB_LABELS[tab]} configuration available for this platform.</p>
        )}
        <div className="flex gap-2 mt-3">
          <button onClick={copy} disabled={!currentConfig} className="px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50">Copy to Clipboard</button>
          <button onClick={openPush} disabled={!currentConfig || pushing || !pushAllowed}
            title={!pushAllowed ? 'Config push is disabled by administrator. Contact your network team to enable.' : undefined}
            className="px-3 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg font-medium">
            {pushing ? 'Pushing…' : 'Push to Device ▶'}
          </button>
          {!pushAllowed && (
            <span className="self-center text-xs text-gray-400">Push disabled — read-only mode</span>
          )}
        </div>
      </div>

      {/* Push history */}
      {history.length > 0 && (
        <div className="border-t border-gray-100 px-4 py-3">
          <h4 className="text-xs font-semibold text-gray-600 mb-2">Recent pushes</h4>
          <div className="space-y-1">
            {history.map((h) => (
              <div key={h.id} className="flex items-center gap-2 text-xs">
                <span>{h.success ? '✅' : '❌'}</span>
                <span className="text-gray-700">{h.sections.join(', ') || '—'}</span>
                <span className="text-gray-400">by {h.pushed_by_username ?? 'system'}</span>
                <span className="ml-auto text-gray-400">{new Date(h.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {confirm && (
        <Modal title={`Push config to ${device.hostname}?`} onClose={() => setConfirm(null)} size="lg"
          footer={
            <>
              <button onClick={() => setConfirm(null)} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
              <button onClick={() => doPush(confirm.sections)} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Push Config</button>
            </>
          }>
          <p className="text-sm text-gray-700 mb-2">This will push the following config ({confirm.sections.join(', ')}) to <span className="font-medium">{device.hostname}</span>. This operation cannot be undone automatically.</p>
          <pre className="bg-gray-900 text-gray-100 text-xs font-mono rounded-md p-3 overflow-x-auto max-h-60 whitespace-pre-wrap">{confirm.config}</pre>
        </Modal>
      )}
    </div>
  )
}

// ── Live telemetry view (default export = the Telemetry tab) ──────────────────

const liveCard = 'bg-white dark:bg-gray-900 rounded-lg shadow-sm border border-gray-200 dark:border-gray-800'
const TRAFFIC_RANGES = ['1h', '6h', '24h', '7d'] as const
// A static, faded bar pattern standing in for a sparkline until InfluxDB queries land.
const FLAT_SPARK = [3, 4, 3, 5, 4, 3, 4, 5, 6, 5, 4, 3, 4, 5, 4]

function formatUptime(seconds: number | null): string {
  if (seconds == null) return '—'
  const s = Math.floor(seconds)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m` : `${m}m`
}

function formatBps(bps: number | null): string {
  if (bps == null) return '—'
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbps`
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(0)} Kbps`
  return `${Math.round(bps)} bps`
}

// Bytes total from a bps over the window — kept simple for the expanded view.
function formatRate(v: number | null): string {
  if (v == null) return '—'
  return v === 0 ? '0' : v < 1 ? v.toFixed(2) : v.toFixed(0)
}

function utilColor(pct: number | null): string {
  if (pct == null) return 'text-gray-400 dark:text-gray-500'
  if (pct >= 90) return 'text-red-600 dark:text-red-400'
  if (pct >= 80) return 'text-orange-600 dark:text-orange-400'
  if (pct >= 60) return 'text-yellow-600 dark:text-yellow-500'
  return 'text-green-600 dark:text-green-400'
}

export default function Telemetry({ device, onConfigure }: { device: DeviceDetail; onConfigure?: () => void }) {
  const [ifaces, setIfaces] = useState<MonitoredInterface[] | null>(null)
  const [cfg, setCfg] = useState<TelemetryConfig | null>(null)
  const [range, setRange] = useState<(typeof TRAFFIC_RANGES)[number]>('1h')
  const [metrics, setMetrics] = useState<DeviceMetrics | null>(null)

  useEffect(() => {
    fetchMonitoredInterfaces(device.id).then(setIfaces).catch(() => setIfaces([]))
    fetchTelemetryConfig(device.id).then(setCfg).catch(() => setCfg(null))
  }, [device.id])

  // Metrics: refetch on device/range change and auto-refresh every 60s.
  useEffect(() => {
    let cancelled = false
    const load = () => fetchDeviceMetrics(device.id, range).then((m) => { if (!cancelled) setMetrics(m) }).catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [device.id, range])

  const health = metrics?.metrics
  const [expanded, setExpanded] = useState<string | null>(null)

  // Merge selected interfaces with their live stats (matched by name).
  const ifaceRows = useMemo(() => {
    const byName = new Map((metrics?.interfaces ?? []).map((s) => [s.if_name, s]))
    return (ifaces ?? []).map((i) => ({ iface: i, stat: byName.get(i.if_name) ?? null }))
  }, [ifaces, metrics])

  const lldp = useMemo(() => (ifaces || []).filter((i) => i.lldp_neighbor_hostname), [ifaces])

  const configure = () => onConfigure?.()

  // Nothing configured yet → guide the user to the config slide-over.
  if (ifaces !== null && ifaces.length === 0 && cfg && !cfg.collect_cpu && !cfg.collect_memory) {
    return (
      <div className={clsx(liveCard, 'py-16 text-center')}>
        <div className="text-4xl mb-2">📈</div>
        <p className="text-sm font-medium text-gray-700 dark:text-gray-200">No telemetry data collected yet</p>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-1 mb-4 max-w-md mx-auto">
          Configure telemetry in ⚙ Settings → Telemetry Configuration to select metrics and interfaces to start collecting.
        </p>
        <button onClick={configure} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium">
          Open Telemetry Configuration →
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Section 1 — Device Health */}
      <div className={clsx(liveCard, 'p-4')}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Device Health</h3>
          <div className="flex items-center gap-2">
            <div className="flex gap-1">
              {TRAFFIC_RANGES.map((r) => (
                <button key={r} onClick={() => setRange(r)}
                  className={clsx('px-2 py-1 text-xs rounded-md border',
                    range === r ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
                      : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800')}>
                  {r}
                </button>
              ))}
            </div>
            <button onClick={configure} className="text-xs font-medium text-blue-600 hover:text-blue-800">Configure →</button>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <HealthCard label="CPU" value={health?.cpu_pct != null ? `${health.cpu_pct.toFixed(1)}%` : null}
            series={metrics?.timeseries.cpu_pct} color="#f59e0b" />
          <HealthCard label="Memory" value={health?.memory_used_pct != null ? `${health.memory_used_pct.toFixed(1)}%` : null}
            series={metrics?.timeseries.memory_used_pct} color="#3b82f6" />
          <HealthCard label="Uptime" value={health?.uptime_seconds != null ? formatUptime(health.uptime_seconds) : null}
            series={metrics?.timeseries.uptime} color="#10b981" />
          <HealthCard label="Poll" value={health?.poll_duration_ms != null ? `${health.poll_duration_ms.toFixed(0)} ms` : null} />
        </div>
      </div>

      {/* Section 2 — Interface Traffic */}
      <div className={liveCard}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Interface Traffic</h3>
          <div className="flex gap-1">
            {TRAFFIC_RANGES.map((r) => (
              <button key={r} onClick={() => setRange(r)}
                className={clsx('px-2 py-1 text-xs rounded-md border',
                  range === r ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
                    : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800')}>
                {r}
              </button>
            ))}
          </div>
        </div>
        {ifaces === null ? (
          <div className="py-10 text-center text-sm text-gray-400">Loading…</div>
        ) : ifaces.length === 0 ? (
          <div className="py-10 text-center text-sm text-gray-400 dark:text-gray-500">
            No monitored interfaces. <button onClick={configure} className="text-blue-600 hover:text-blue-800 font-medium">Select interfaces →</button>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 dark:text-gray-400 text-left border-b border-gray-100 dark:border-gray-800">
                <th className="px-4 py-2 font-medium">Interface</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">In</th>
                <th className="px-4 py-2 font-medium">Out</th>
                <th className="px-4 py-2 font-medium">Util</th>
                <th className="px-4 py-2 font-medium">Errors</th>
                <th className="px-4 py-2 font-medium">Drops</th>
                <th className="px-4 py-2 font-medium">{range}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {ifaceRows.map(({ iface: i, stat }) => {
                const down = stat?.oper_status === 'down'
                const errs = (stat?.in_errors_rate ?? 0) + (stat?.out_errors_rate ?? 0)
                const drops = (stat?.in_discards_rate ?? 0) + (stat?.out_discards_rate ?? 0)
                const util = Math.max(stat?.in_util_pct ?? 0, stat?.out_util_pct ?? 0)
                const isOpen = expanded === i.if_name
                return (
                <Fragment key={i.id}>
                  <tr className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50" onClick={() => setExpanded(isOpen ? null : i.if_name)}>
                    <td className="px-4 py-2 font-mono text-xs text-gray-800 dark:text-gray-200">{i.if_name}</td>
                    <td className="px-4 py-2">
                      {stat?.oper_status
                        ? <span className={clsx('inline-flex items-center gap-1.5 text-xs', down ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400')}>
                            <span className={clsx('w-1.5 h-1.5 rounded-full', down ? 'bg-red-500' : 'bg-green-500')} />{stat.oper_status}
                          </span>
                        : <span className="text-gray-300 dark:text-gray-600">—</span>}
                    </td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">{formatBps(stat?.in_bps ?? null)}</td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">{formatBps(stat?.out_bps ?? null)}</td>
                    <td className={clsx('px-4 py-2', utilColor(stat ? util : null))}>{stat ? `${util.toFixed(1)}%` : '—'}</td>
                    <td className={clsx('px-4 py-2', errs > 0 ? 'text-red-600 dark:text-red-400 font-medium' : 'text-gray-400 dark:text-gray-500')}>{stat ? formatRate(errs) : '—'}</td>
                    <td className={clsx('px-4 py-2', drops > 0 ? 'text-orange-600 dark:text-orange-400 font-medium' : 'text-gray-400 dark:text-gray-500')}>{stat ? formatRate(drops) : '—'}</td>
                    <td className="px-4 py-2">{stat && stat.series.in_bps.length > 1 ? <MiniSpark series={stat.series.in_bps} color="#3b82f6" /> : <Sparkline />}</td>
                  </tr>
                  {isOpen && stat && (
                    <tr className="bg-gray-50 dark:bg-gray-900/40">
                      <td colSpan={8} className="px-4 py-3">
                        <div className="text-xs font-mono space-y-1 text-gray-700 dark:text-gray-300">
                          <div className="font-semibold text-gray-800 dark:text-gray-100">{i.if_name} · <span className={down ? 'text-red-500' : 'text-green-500'}>{stat.oper_status ?? '?'}</span>{i.if_speed_mbps ? ` · ${i.if_speed_mbps >= 1000 ? `${i.if_speed_mbps / 1000}Gbps` : `${i.if_speed_mbps}Mbps`}` : ''}</div>
                          <div>In:&nbsp; {formatBps(stat.in_bps)}&nbsp;&nbsp;{formatRate(stat.in_pps)} pps&nbsp;&nbsp;{(stat.in_util_pct ?? 0).toFixed(2)}% util</div>
                          <div>Out: {formatBps(stat.out_bps)}&nbsp;&nbsp;{formatRate(stat.out_pps)} pps&nbsp;&nbsp;{(stat.out_util_pct ?? 0).toFixed(2)}% util</div>
                          <div>Input errors: {formatRate(stat.in_errors_rate)}/s&nbsp;&nbsp; Output errors: {formatRate(stat.out_errors_rate)}/s</div>
                          <div>Input drops:&nbsp; {formatRate(stat.in_discards_rate)}/s&nbsp;&nbsp; Output drops:&nbsp; {formatRate(stat.out_discards_rate)}/s</div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )})}
            </tbody>
          </table>
        )}
      </div>

      {/* Section 3 — BGP (only when BGP collection is enabled) */}
      {cfg?.collect_bgp && (
        <div className={clsx(liveCard, 'p-4')}>
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">BGP Neighbors</h3>
          <p className="text-sm text-gray-400 dark:text-gray-500">No BGP data collected yet.</p>
        </div>
      )}

      {/* Section 4 — LLDP Neighbors */}
      <div className={liveCard}>
        <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">LLDP Neighbors</h3>
        </div>
        {lldp.length === 0 ? (
          <div className="py-8 text-center text-sm text-gray-400 dark:text-gray-500">No LLDP neighbors discovered.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 dark:text-gray-400 text-left border-b border-gray-100 dark:border-gray-800">
                <th className="px-4 py-2 font-medium">Local Port</th>
                <th className="px-4 py-2 font-medium">Neighbor</th>
                <th className="px-4 py-2 font-medium">Remote Port</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {lldp.map((i) => (
                <tr key={i.id}>
                  <td className="px-4 py-2 font-mono text-xs text-gray-800 dark:text-gray-200">{i.if_name}</td>
                  <td className="px-4 py-2 text-blue-600 dark:text-blue-400">{i.lldp_neighbor_hostname}</td>
                  <td className="px-4 py-2 font-mono text-xs text-gray-600 dark:text-gray-300">{i.lldp_neighbor_port || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="text-xs text-gray-400 dark:text-gray-500">
        Live metrics populate once SNMP/gNMI polling reports for this device. Manage what's collected in ⚙ Settings → Telemetry Configuration.
      </p>
    </div>
  )
}

function Sparkline() {
  return (
    <span className="inline-flex items-end gap-0.5 h-5" title="No data yet">
      {FLAT_SPARK.map((h, i) => (
        <span key={i} className="w-1 bg-gray-200 dark:bg-gray-700 rounded-sm" style={{ height: `${h * 3}px` }} />
      ))}
    </span>
  )
}

// A health metric tile: big current value + an ECharts sparkline of the series.
function HealthCard({ label, value, series, color = '#3b82f6' }: {
  label: string; value: string | null; series?: MetricPoint[]; color?: string
}) {
  const hasData = value != null
  return (
    <div className="rounded-lg border border-gray-100 dark:border-gray-800 p-3">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-gray-400 dark:text-gray-500">{label}</p>
          <p className={clsx('text-lg font-bold mt-1', hasData ? 'text-gray-800 dark:text-gray-100' : 'text-gray-300 dark:text-gray-600')}>
            {value ?? '—'}
          </p>
        </div>
      </div>
      {series && series.length > 1
        ? <MiniSpark series={series} color={color} />
        : <p className="text-[10px] text-gray-300 dark:text-gray-600">{hasData ? '' : 'no data'}</p>}
    </div>
  )
}

function MiniSpark({ series, color }: { series: MetricPoint[]; color: string }) {
  const option: EChartsOption = {
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: { type: 'category', show: false, data: series.map((p) => p.time) },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        return `${new Date(p.axisValue).toLocaleString()}<br/>${Number(p.value).toLocaleString()}`
      },
    },
    series: [{
      type: 'line', data: series.map((p) => p.value), showSymbol: false, smooth: true,
      lineStyle: { color, width: 1.5 },
      areaStyle: { color, opacity: 0.12 },
    }],
  }
  return <ReactECharts option={option} style={{ height: 28, marginTop: 4 }} opts={{ renderer: 'svg' }} notMerge />
}
