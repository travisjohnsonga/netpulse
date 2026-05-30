import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchTelemetryConfig, saveTelemetryConfig, fetchMonitoredInterfaces,
  discoverInterfaces, saveMonitoredInterfaces,
  type DeviceDetail, type TelemetryConfig,
} from '../../api/client'

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

export default function Telemetry({ device }: { device: DeviceDetail }) {
  return (
    <div className="space-y-4">
      <DevicePolling device={device} />
      <InterfacePolling device={device} />
    </div>
  )
}

// ── Section 1: device-level metrics ───────────────────────────────────────────

function DevicePolling({ device }: { device: DeviceDetail }) {
  const [cfg, setCfg] = useState<TelemetryConfig | null>(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchTelemetryConfig(device.id).then(setCfg).catch(() => setError('Failed to load telemetry config.'))
  }, [device.id])

  if (error) return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
  if (!cfg) return <div className="bg-white rounded-lg border border-gray-200 p-4"><div className="w-5 h-5 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>

  const set = (patch: Partial<TelemetryConfig>) => setCfg((c) => (c ? { ...c, ...patch } : c))

  const save = async () => {
    setSaving(true); setError(null)
    try {
      setCfg(await saveTelemetryConfig(device.id, cfg))
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    } catch { setError('Failed to save settings.') } finally { setSaving(false) }
  }

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
    </div>
  )
}

// ── Section 2: interface selection ────────────────────────────────────────────

function InterfacePolling({ device }: { device: DeviceDetail }) {
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

  const save = async () => {
    setSaving(true); setError(null); setSavedMsg(null)
    const payload = rows.filter((r) => selected.has(r.if_name)).map((r) => ({
      if_name: r.if_name, if_index: r.if_index, if_description: r.if_description,
      if_speed_mbps: r.if_speed_mbps, if_type: r.if_type,
      lldp_neighbor_hostname: r.lldp_neighbor_hostname, lldp_neighbor_port: r.lldp_neighbor_port,
      lldp_neighbor_desc: r.lldp_neighbor_desc, oper_status: r.status,
      poll_traffic: true, poll_errors: true, poll_status: true,
      collection_method: r.collection_method || 'auto',
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
                    <td className="px-3 py-1.5"><span className="px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600 uppercase">{r.collection_method === 'gnmi' ? 'gNMI' : 'SNMP'}</span></td>
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
