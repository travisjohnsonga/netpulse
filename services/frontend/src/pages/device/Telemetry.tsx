import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import TimeRangeSelector, { type TimeRange } from '../../components/TimeRangeSelector'
import {
  fetchTelemetryConfig, saveTelemetryConfig, fetchMonitoredInterfaces,
  discoverInterfaces, saveMonitoredInterfaces,
  generateTelemetryConfig, pushTelemetryConfig, fetchPushHistory, checkHealth, fetchSystemSettings,
  fetchDeviceMetrics, pollDeviceNow,
  type DeviceDetail, type TelemetryConfig, type GeneratedConfig, type ConfigPushRecord,
  type MonitoredInterface, type DeviceMetrics, type MetricPoint, type DeviceReachability,
  type DeviceEnvironmentPoe,
} from '../../api/client'
import Modal from '../../components/Modal'
import DeviceLink from '../../components/DeviceLink'
import { useTemperature } from '../../lib/temperature'
import DeviceAddModal, { type DeviceAddPrefill } from '../../components/DeviceAddModal'
import { CollectionMethodBar } from '../../components/CollectionMethodBadges'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'

// Interface-name abbreviation → full form, so LLDP neighbours reported
// abbreviated (Gi3) and full (GigabitEthernet3) by the two sources dedupe.
const INTERFACE_ABBREV: Record<string, string> = {
  Gi: 'GigabitEthernet',
  Te: 'TenGigabitEthernet',
  Fa: 'FastEthernet',
  Se: 'Serial',
  Lo: 'Loopback',
  Mg: 'Management',
  Et: 'Ethernet',
}

function expandIfName(name: string): string {
  const m = (name || '').trim().match(/^([A-Za-z]+)(\d.*)$/)
  if (!m) return name
  for (const [abbr, full] of Object.entries(INTERFACE_ABBREV)) {
    if (m[1].toLowerCase() === abbr.toLowerCase()) return `${full}${m[2]}`
  }
  return name
}

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
  lldp_neighbor_device_id: number | null
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
  // Prefill for adding an LLDP neighbor (not yet in inventory) to the fleet.
  const [lldpAdd, setLldpAdd] = useState<DeviceAddPrefill | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchMonitoredInterfaces(device.id)
      .then((mi) => {
        setRows(mi.map((m) => ({
          if_name: m.if_name, if_index: m.if_index, if_description: m.if_description,
          if_speed_mbps: m.if_speed_mbps, if_type: m.if_type, status: m.last_status,
          lldp_neighbor_hostname: m.lldp_neighbor_hostname, lldp_neighbor_port: m.lldp_neighbor_port,
          lldp_neighbor_desc: m.lldp_neighbor_desc, lldp_neighbor_device_id: m.lldp_neighbor_device_id,
          collection_method: m.collection_method,
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
        lldp_neighbor_desc: d.lldp_neighbor_desc, lldp_neighbor_device_id: null,
        collection_method: d.collection_method,
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
                    <td className="px-3 py-1.5">{r.lldp_neighbor_hostname
                      ? <span className="inline-flex flex-col">
                          <DeviceLink deviceId={r.lldp_neighbor_device_id} hostname={r.lldp_neighbor_hostname} className="text-blue-600" />
                          {r.lldp_neighbor_device_id
                            ? <span className="text-[10px] text-green-600 dark:text-green-400" title="In inventory — link to its device page">● in inventory</span>
                            : <button
                                onClick={() => setLldpAdd({ hostname: r.lldp_neighbor_hostname!.split('.')[0] })}
                                className="text-[10px] text-blue-600 dark:text-blue-400 hover:underline text-left"
                                title="This neighbor is not in inventory — add it"
                              >
                                + Add to Inventory
                              </button>}
                        </span>
                      : <span className="text-gray-300">—</span>}</td>
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
      {lldpAdd && (
        <DeviceAddModal
          initial={lldpAdd}
          onClose={() => setLldpAdd(null)}
          onCreated={() => { setLldpAdd(null); load() }}
        />
      )}
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
          <Link to="/settings/system?tab=general" className="text-blue-600 hover:text-blue-800">Configure it in Settings → System → General</Link>.
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
        {(tab === 'snmp' || tab === 'all') && gen.snmp_warning && (
          <div className="flex items-start gap-2 mb-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-md text-xs text-amber-800">
            <span>⚠️</span>
            <span>{gen.snmp_warning}</span>
          </div>
        )}
        {(tab === 'snmp' || tab === 'all') && gen.snmpv3 && (
          <p className="text-xs text-green-600 mb-2">🔒 SNMPv3 authPriv — keys are write-only; placeholders shown are filled from OpenBao on push.</p>
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

export default function Telemetry({ device, onConfigure, refreshSignal = 0 }: { device: DeviceDetail; onConfigure?: () => void; refreshSignal?: number }) {
  const [ifaces, setIfaces] = useState<MonitoredInterface[] | null>(null)
  const [cfg, setCfg] = useState<TelemetryConfig | null>(null)
  const [range, setRange] = useState<TimeRange>('1h')
  const [metrics, setMetrics] = useState<DeviceMetrics | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // Monitored interfaces + config. Re-fetched when refreshSignal changes (i.e.
  // after the Telemetry Configuration slide-over saves & closes) so the
  // interface table reflects the new selection without a manual page refresh.
  useEffect(() => {
    let cancelled = false
    if (refreshSignal > 0) setRefreshing(true)
    Promise.all([
      fetchMonitoredInterfaces(device.id).then((r) => { if (!cancelled) setIfaces(r) }).catch(() => { if (!cancelled) setIfaces([]) }),
      fetchTelemetryConfig(device.id).then((c) => { if (!cancelled) setCfg(c) }).catch(() => { if (!cancelled) setCfg(null) }),
    ]).finally(() => { if (!cancelled) setTimeout(() => setRefreshing(false), 800) })
    return () => { cancelled = true }
  }, [device.id, refreshSignal])

  // Metrics: refetch on device/range/refresh change and auto-refresh every 60s.
  useEffect(() => {
    let cancelled = false
    const load = () => fetchDeviceMetrics(device.id, range).then((m) => { if (!cancelled) setMetrics(m) }).catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [device.id, range, refreshSignal])

  const health = metrics?.metrics
  const [expanded, setExpanded] = useState<string | null>(null)
  const [polling, setPolling] = useState(false)
  const [pollToast, setPollToast] = useState<string | null>(null)

  const pollNow = async () => {
    setPolling(true); setPollToast(null)
    try {
      await pollDeviceNow(device.id)
      await new Promise((r) => setTimeout(r, 5000))   // give the poller a cycle
      const m = await fetchDeviceMetrics(device.id, range)
      setMetrics(m)
      setPollToast('Poll complete — metrics refreshed')
    } catch {
      setPollToast('Poll request failed')
    } finally {
      setPolling(false)
      setTimeout(() => setPollToast(null), 4000)
    }
  }

  // Merge selected interfaces with their live stats (matched by name).
  const ifaceRows = useMemo(() => {
    const byName = new Map((metrics?.interfaces ?? []).map((s) => [s.if_name, s]))
    return (ifaces ?? []).map((i) => ({ iface: i, stat: byName.get(i.if_name) ?? null }))
  }, [ifaces, metrics])

  // LLDP neighbours: merge per-interface metadata (MonitoredInterface.lldp_*)
  // with discovered TopologyLink neighbours (either direction, from the metrics
  // endpoint), keyed by local port. This surfaces neighbours for devices that
  // have topology links but no per-interface LLDP fields (e.g. router1).
  const lldp = useMemo(() => {
    // Normalise interface names to canonical full form so the same physical port
    // reported abbreviated (Gi3) and full (GigabitEthernet3) by the two sources
    // collapses to one row. MonitoredInterface wins (inserted first).
    const byPort = new Map<string, { local_port: string; neighbor: string; remote_port: string }>()
    for (const i of ifaces || []) {
      if (i.lldp_neighbor_hostname) {
        const key = expandIfName(i.if_name)
        byPort.set(key, { local_port: key, neighbor: i.lldp_neighbor_hostname, remote_port: expandIfName(i.lldp_neighbor_port || '') })
      }
    }
    for (const n of metrics?.lldp_neighbors ?? []) {
      const key = expandIfName(n.local_port)
      if (!byPort.has(key)) {
        byPort.set(key, { local_port: key, neighbor: n.neighbor_hostname, remote_port: expandIfName(n.remote_port || '') })
      }
    }
    return [...byPort.values()]
  }, [ifaces, metrics])

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
            <TimeRangeSelector value={range} onChange={setRange} />
            <button onClick={pollNow} disabled={polling}
              className="px-2 py-1 text-xs rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
              {polling ? '↻ Polling…' : '↻ Poll Now'}
            </button>
            <button onClick={configure} className="text-xs font-medium text-blue-600 hover:text-blue-800">Configure →</button>
          </div>
        </div>
        <div className="mb-3"><CollectionMethodBar deviceId={device.id} refreshKey={refreshSignal} /></div>
        {pollToast && <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">{pollToast}</p>}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <HealthCard label="CPU" value={health?.cpu_pct != null ? `${health.cpu_pct.toFixed(1)}%` : null}
            series={metrics?.timeseries.cpu_pct} color="#f59e0b" />
          <HealthCard label="Memory" value={health?.memory_used_pct != null ? `${health.memory_used_pct.toFixed(1)}%` : null}
            series={metrics?.timeseries.memory_used_pct} color="#3b82f6" />
          <HealthCard label="Uptime" value={health?.uptime_seconds != null ? formatUptime(health.uptime_seconds) : null}
            subtitle="since last reboot" />
          <HealthCard label="Poll" value={health?.poll_duration_ms != null ? `${health.poll_duration_ms.toFixed(0)} ms` : null} />
        </div>
      </div>

      {/* Section 1b — Ping Latency (full width; physical-sensor environment data
          now lives on its own Environment tab). */}
      <div className={clsx(liveCard, 'p-4')}>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Ping Latency</h3>
        </div>
        <PingLatencyChart reach={metrics?.reachability} />
      </div>

      {/* Section 2 — Interface Traffic */}
      <div className={liveCard}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 flex items-center gap-2">
            Interface Traffic
            {refreshing && (
              <span className="inline-flex items-center gap-1 text-xs font-normal text-blue-600 dark:text-blue-400">
                <span className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                Refreshing…
              </span>
            )}
          </h3>
          <TimeRangeSelector value={range} onChange={setRange} />
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
              {lldp.map((n) => (
                <tr key={n.local_port}>
                  <td className="px-4 py-2 font-mono text-xs text-gray-800 dark:text-gray-200">{n.local_port}</td>
                  <td className="px-4 py-2 text-blue-600 dark:text-blue-400">{n.neighbor}</td>
                  <td className="px-4 py-2 font-mono text-xs text-gray-600 dark:text-gray-300">{n.remote_port || '—'}</td>
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

// ── Environment tab (default export's sibling) ───────────────────────────────
// Physical-sensor view moved off the Telemetry tab: temperature (sensor rows +
// 24h history chart), fans, power supplies and PoE budget. Pulls the same
// /metrics endpoint and only shows sections the device actually reports.
export function Environment({ device }: { device: DeviceDetail }) {
  const [metrics, setMetrics] = useState<DeviceMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [polling, setPolling] = useState(false)
  const [pollToast, setPollToast] = useState<string | null>(null)
  const temp = useTemperature()

  useEffect(() => {
    let cancelled = false
    // 24h range so the temperature-history chart has a full day of context.
    const load = () => fetchDeviceMetrics(device.id, '24h')
      .then((m) => { if (!cancelled) setMetrics(m) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [device.id])

  const pollNow = async () => {
    setPolling(true); setPollToast(null)
    try {
      await pollDeviceNow(device.id)
      await new Promise((r) => setTimeout(r, 5000))   // give the poller a cycle
      setMetrics(await fetchDeviceMetrics(device.id, '24h'))
      setPollToast('Poll complete — environment refreshed')
    } catch {
      setPollToast('Poll request failed')
    } finally {
      setPolling(false)
      setTimeout(() => setPollToast(null), 4000)
    }
  }

  const env = metrics?.environment
  const hasSensors = !!env?.sensors?.length
  const hasFans = !!env?.fans?.length
  const hasPsus = !!env?.psus?.length
  const hasPoe = !!env?.poe && (env.poe.budget_watts ?? 0) > 0
  const hasHistory = !!env?.temperature_history?.length
  const fanCount = env?.fan_count ?? env?.fan_sensors
  const psuCount = env?.psu_count ?? env?.power_sensors
  const tempSensors = env?.sensors?.length ?? env?.temperature_sensors ?? (env?.temperature_c != null ? 1 : 0)
  const hasDetail = hasSensors || hasFans || hasPsus || hasPoe || hasHistory
  const hasAny = !!env && (hasDetail || env.temperature_c != null || !!fanCount || !!psuCount)

  if (loading && !metrics) {
    return <div className="flex items-center justify-center py-24"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  }

  if (!hasAny) {
    return (
      <div className={clsx(liveCard, 'py-16 text-center')}>
        <div className="text-4xl mb-2">🌡</div>
        <p className="text-sm font-medium text-gray-700 dark:text-gray-200">No environment data</p>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-1 max-w-md mx-auto">
          This device doesn't report physical sensors (temperature, fans, power supplies).
          Virtual platforms (e.g. C8000V) expose none.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Summary tiles */}
      <div className={clsx(liveCard, 'p-4')}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Environment</h3>
          <button onClick={pollNow} disabled={polling}
            className="px-2 py-1 text-xs rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
            {polling ? '↻ Polling…' : '↻ Poll Now'}
          </button>
        </div>
        {pollToast && <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">{pollToast}</p>}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <HealthCard label="Temperature" value={env?.temperature_c != null ? temp.format(env.temperature_c) : null}
            subtitle={`${tempSensors} sensor${tempSensors === 1 ? '' : 's'}`} />
          <HealthCard label="Fans" value={fanCount ? `${fanCount}` : null}
            subtitle={`${fanCount === 1 ? 'fan' : 'fans'} present`} />
          <HealthCard label="Power" value={psuCount ? `${psuCount}` : null}
            subtitle={`${psuCount === 1 ? 'supply' : 'supplies'} present`} />
          <HealthCard label="PoE" value={hasPoe ? `${env!.poe!.used_watts ?? 0} W` : null}
            subtitle={hasPoe ? `of ${env!.poe!.budget_watts ?? 0} W budget` : 'no PoE'} />
        </div>
      </div>

      {/* Detail: per-unit temperature / fans / PSUs + PoE bar + history chart */}
      {hasDetail && (
        <div className={clsx(liveCard, 'p-4')}>
          {hasSensors && (
            <EnvSection title="Temperature">
              {env!.sensors!.map((s) => (
                <EnvRow key={s.sensor_name} icon="🌡" name={s.sensor_name}
                  value={s.temperature_c != null ? temp.format(s.temperature_c) : '—'}
                  statusOk={s.status_ok} statusText={s.status_ok ? 'OK' : 'Fault'} />
              ))}
            </EnvSection>
          )}

          {hasFans && (
            <EnvSection title="Fans">
              {env!.fans!.map((f) => (
                <EnvRow key={f.name} icon="🌀" name={f.name}
                  value={f.rpm != null ? `${f.rpm} RPM` : '—'}
                  statusOk={f.status_ok}
                  statusText={f.status_ok == null ? 'Unknown' : f.status_ok ? 'OK' : 'Fault'} />
              ))}
            </EnvSection>
          )}

          {hasPsus && (
            <EnvSection title="Power Supplies">
              {env!.psus!.map((p) => (
                <EnvRow key={p.name} icon="⚡" name={p.name}
                  value={p.watts != null ? `${p.watts} W` : '—'}
                  statusOk={p.status_ok}
                  statusText={p.status_ok == null ? 'Unknown' : p.status_ok ? 'Online' : 'Offline'} />
              ))}
            </EnvSection>
          )}

          {hasPoe && <PoeBar poe={env!.poe!} />}

          {hasHistory && (
            <div className="mt-3">
              <p className="text-xs text-gray-400 dark:text-gray-500 mb-1">Temperature — last 24h (device max)</p>
              <TemperatureHistoryChart series={env!.temperature_history} />
            </div>
          )}
        </div>
      )}
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

// A health metric tile: big current value + either an ECharts sparkline of the
// series or a static subtitle. Single-value metrics (e.g. uptime) pass a
// subtitle and no series — a trend chart for a monotonic counter is noise.
function HealthCard({ label, value, series, color = '#3b82f6', subtitle }: {
  label: string; value: string | null; series?: MetricPoint[]; color?: string; subtitle?: string
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
      {subtitle
        ? <p className="text-[10px] text-gray-400 dark:text-gray-500">{hasData ? subtitle : 'no data'}</p>
        : series && series.length > 1
          ? <MiniSpark series={series} color={color} />
          : <p className="text-[10px] text-gray-300 dark:text-gray-600">{hasData ? '' : 'no data'}</p>}
    </div>
  )
}

// ── Environment detail: per-unit fan/PSU/temperature rows + PoE bar ──────────
// Status dot: green ok/online · red fault/offline · gray unknown (no sensor).
function StatusDot({ ok }: { ok: boolean | null }) {
  const color = ok == null ? 'text-gray-400 dark:text-gray-600'
    : ok ? 'text-green-500' : 'text-red-500'
  return <span className={color}>●</span>
}

function EnvSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-gray-100 dark:border-gray-800 pt-2 mt-2 first:border-t-0 first:pt-0 first:mt-0">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1">{title}</p>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function EnvRow({ icon, name, value, statusOk, statusText }: {
  icon: string; name: string; value: string; statusOk: boolean | null; statusText: string
}) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="flex items-center gap-1.5 text-gray-700 dark:text-gray-300 min-w-0">
        <span className="shrink-0">{icon}</span>
        <span className="truncate">{name}</span>
      </span>
      <span className="flex items-center gap-3 shrink-0">
        <span className="tabular-nums text-gray-500 dark:text-gray-400">{value}</span>
        <span className="flex items-center gap-1 w-16 justify-end">
          <StatusDot ok={statusOk} />
          <span className="text-gray-500 dark:text-gray-400">{statusText}</span>
        </span>
      </span>
    </div>
  )
}

// PoE budget bar: green <50% · amber 50-80% · red >80% (approaching budget).
function PoeBar({ poe }: { poe: DeviceEnvironmentPoe }) {
  const budget = poe.budget_watts ?? 0
  const used = poe.used_watts ?? 0
  const pct = poe.used_pct ?? (budget > 0 ? (used / budget) * 100 : 0)
  const clamped = Math.max(0, Math.min(100, pct))
  const barColor = pct > 80 ? 'bg-red-500' : pct >= 50 ? 'bg-amber-500' : 'bg-green-500'
  const active = poe.status === 'on'
  return (
    <EnvSection title="PoE Budget">
      <div className="h-2.5 w-full rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all', barColor)} style={{ width: `${clamped}%` }} />
      </div>
      <div className="flex items-center justify-between text-xs mt-1">
        <span className="text-gray-500 dark:text-gray-400 tabular-nums">
          {used} W / {budget} W ({Math.round(pct)}%)
        </span>
        <span className="flex items-center gap-1">
          <StatusDot ok={active ? true : poe.status === 'faulty' ? false : null} />
          <span className="text-gray-500 dark:text-gray-400 capitalize">{active ? 'Active' : poe.status}</span>
        </span>
      </div>
    </EnvSection>
  )
}

// Ping/RTT latency over the selected range, colored by latency zone
// (<10ms green · 10-50 blue · 50-100 yellow · >100 red), with a 100ms warning
// threshold line and gaps where the device was unreachable.
function PingLatencyChart({ reach }: { reach?: DeviceReachability }) {
  const data = reach?.data ?? []
  const hasData = data.some((p) => p.rtt_ms != null)
  const fmt = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}ms`)

  const option: EChartsOption = {
    grid: { left: 44, right: 14, top: 14, bottom: 26 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        const v = Array.isArray(p.value) ? p.value[1] : p.value
        const t = new Date(Array.isArray(p.value) ? p.value[0] : p.axisValue).toLocaleString()
        return v == null ? `${t}<br/>unreachable` : `${t}<br/>${Number(v).toFixed(1)} ms`
      },
    },
    xAxis: { type: 'time', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: 'ms', nameTextStyle: { fontSize: 10 }, min: 0, scale: true, axisLabel: { fontSize: 10 } },
    visualMap: {
      show: false, dimension: 1, seriesIndex: 0,
      pieces: [
        { lte: 10, color: '#22c55e' },
        { gt: 10, lte: 50, color: '#3b82f6' },
        { gt: 50, lte: 100, color: '#eab308' },
        { gt: 100, color: '#ef4444' },
      ],
      outOfRange: { color: '#ef4444' },
    },
    series: [{
      type: 'line', showSymbol: false, smooth: true, connectNulls: false,
      data: data.map((p) => [p.time, p.rtt_ms]),
      lineStyle: { width: 1.5 },
      areaStyle: { opacity: 0.08 },
      markLine: {
        silent: true, symbol: 'none',
        lineStyle: { color: '#ef4444', type: 'dashed', width: 1 },
        data: [{ yAxis: 100 }],
        label: { formatter: 'warn 100ms', fontSize: 9, color: '#ef4444', position: 'insideEndTop' },
      },
    }],
  }

  return (
    <div>
      {hasData
        ? <ReactECharts option={option} style={{ height: 180 }} opts={{ renderer: 'svg' }} notMerge />
        : <div className="h-[180px] flex items-center justify-center text-xs text-gray-400 dark:text-gray-500">No latency data yet</div>}
      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
        Current: <span className="font-medium text-gray-700 dark:text-gray-300">{fmt(reach?.rtt_ms)}</span>
        {' · '}Avg: <span className="font-medium text-gray-700 dark:text-gray-300">{fmt(reach?.avg_rtt_ms)}</span>
        {' · '}Max: <span className="font-medium text-gray-700 dark:text-gray-300">{fmt(reach?.max_rtt_ms)}</span>
        {reach?.uptime_pct_24h != null && <>{' · '}Uptime: <span className="font-medium text-gray-700 dark:text-gray-300">{reach.uptime_pct_24h.toFixed(1)}% (24h)</span></>}
      </p>
    </div>
  )
}

// Device max temperature over 24h, colored by zone (<60 green · 60-75 yellow ·
// 75-85 orange · >85 red) with a warning (75°C) and critical (85°C) line.
function TemperatureHistoryChart({ series }: { series?: MetricPoint[] }) {
  const data = series ?? []
  const hasData = data.length > 1
  const temp = useTemperature()
  // Values + thresholds are Celsius; convert both to the active unit so the
  // zone colours and warn/crit lines stay meaningful in °F.
  const cv = (c: number) => Math.round(temp.convert(c) * 10) / 10
  const warn = cv(75)
  const crit = cv(85)
  const suffix = temp.suffix

  const option: EChartsOption = {
    grid: { left: 40, right: 14, top: 14, bottom: 26 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        const v = Array.isArray(p.value) ? p.value[1] : p.value
        const t = new Date(Array.isArray(p.value) ? p.value[0] : p.axisValue).toLocaleString()
        return v == null ? `${t}<br/>no data` : `${t}<br/>${Number(v).toFixed(1)}${suffix}`
      },
    },
    xAxis: { type: 'time', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: suffix, nameTextStyle: { fontSize: 10 }, scale: true, axisLabel: { fontSize: 10 } },
    visualMap: {
      show: false, dimension: 1, seriesIndex: 0,
      pieces: [
        { lte: cv(60), color: '#22c55e' },
        { gt: cv(60), lte: warn, color: '#eab308' },
        { gt: warn, lte: crit, color: '#f97316' },
        { gt: crit, color: '#ef4444' },
      ],
      outOfRange: { color: '#ef4444' },
    },
    series: [{
      type: 'line', showSymbol: false, smooth: true, connectNulls: false,
      data: data.map((p) => [p.time, p.value == null ? null : cv(p.value)]),
      lineStyle: { width: 1.5 },
      areaStyle: { opacity: 0.08 },
      markLine: {
        silent: true, symbol: 'none',
        data: [
          { yAxis: warn, lineStyle: { color: '#eab308', type: 'dashed', width: 1 },
            label: { formatter: `warn ${warn}${suffix}`, fontSize: 9, color: '#eab308', position: 'insideEndTop' } },
          { yAxis: crit, lineStyle: { color: '#ef4444', type: 'dashed', width: 1 },
            label: { formatter: `crit ${crit}${suffix}`, fontSize: 9, color: '#ef4444', position: 'insideEndTop' } },
        ],
      },
    }],
  }

  return (
    <div>
      {hasData
        ? <ReactECharts option={option} style={{ height: 180 }} opts={{ renderer: 'svg' }} notMerge />
        : <div className="h-[180px] flex items-center justify-center text-xs text-gray-400 dark:text-gray-500">No temperature history yet</div>}
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
