import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchTelemetryConfig, saveTelemetryConfig, fetchMonitoredInterfaces,
  discoverInterfaces, saveMonitoredInterfaces,
  generateTelemetryConfig, pushTelemetryConfig, fetchPushHistory, checkHealth,
  type DeviceDetail, type TelemetryConfig, type GeneratedConfig, type ConfigPushRecord,
} from '../../api/client'
import Modal from '../../components/Modal'

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
      <GeneratedConfigSection device={device} />
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

// ── Section 3: generated configuration + push ─────────────────────────────────

const CONFIG_TABS = ['snmp', 'syslog', 'gnmi', 'netflow', 'all'] as const
const TAB_LABELS: Record<string, string> = { snmp: 'SNMP', syslog: 'Syslog', gnmi: 'gNMI', netflow: 'NetFlow', all: 'All' }

function GeneratedConfigSection({ device }: { device: DeviceDetail }) {
  const [gen, setGen] = useState<GeneratedConfig | null>(null)
  const [tab, setTab] = useState<string>('snmp')
  const [collectorIp, setCollectorIp] = useState<string>('')
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
          <button onClick={openPush} disabled={!currentConfig || pushing} className="px-3 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
            {pushing ? 'Pushing…' : 'Push to Device ▶'}
          </button>
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
