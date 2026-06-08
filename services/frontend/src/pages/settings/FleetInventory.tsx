import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import Modal from '../../components/Modal'
import OSStatusBadge, { OS_STATUS_META } from '../../components/OSStatusBadge'
import { SectionHeader } from '../Settings'
import { useIsDark, chartColors } from '../../lib/useIsDark'
import { parseApiErrors } from '../../api/errors'
import {
  fetchDiscoveredPlatforms, refreshDiscoveredPlatforms, fetchOSComplianceSummary,
  fetchDiscoveredPlatformDevices, createApprovedOSVersion,
  type DiscoveredPlatformModel, type OSComplianceSummary, type OSInventoryStatus,
  type OSPolicyStatus, type Device,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

// Donut slice colours, aligned with the OS_STATUS_META legend.
const DONUT_COLORS: Record<OSInventoryStatus, string> = {
  preferred: '#22c55e', approved: '#eab308', deprecated: '#f97316',
  prohibited: '#ef4444', unknown: '#94a3b8',
}
const ORDER: OSInventoryStatus[] = ['preferred', 'approved', 'deprecated', 'prohibited', 'unknown']

function donutOption(summary: OSComplianceSummary, isDark: boolean): EChartsOption {
  const data = ORDER
    .map((s) => ({ name: OS_STATUS_META[s].label, value: summary[s] ?? 0, itemStyle: { color: DONUT_COLORS[s] } }))
    .filter((d) => d.value > 0)
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11, color: chartColors(isDark).text } },
    series: [{
      name: 'OS Compliance', type: 'pie', radius: ['45%', '70%'], center: ['50%', '45%'],
      avoidLabelOverlap: true, label: { show: false },
      itemStyle: { borderRadius: 4, borderColor: 'transparent', borderWidth: 2 },
      data,
    }],
  }
}

export default function FleetInventory() {
  const isDark = useIsDark()
  const [rows, setRows] = useState<DiscoveredPlatformModel[]>([])
  const [summary, setSummary] = useState<OSComplianceSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [drillDevices, setDrillDevices] = useState<Record<number, Device[]>>({})
  const [policyFor, setPolicyFor] = useState<DiscoveredPlatformModel | null>(null)

  const load = () => {
    setLoading(true)
    Promise.all([fetchDiscoveredPlatforms(), fetchOSComplianceSummary()])
      .then(([r, s]) => { setRows(r); setSummary(s); setError(null) })
      .catch(() => setError('Failed to load fleet inventory.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const refresh = async () => {
    setRefreshing(true)
    try { await refreshDiscoveredPlatforms(); load() }
    catch { setError('Refresh failed.') }
    finally { setRefreshing(false) }
  }

  const toggleRow = async (r: DiscoveredPlatformModel) => {
    if (expanded === r.id) { setExpanded(null); return }
    setExpanded(r.id)
    if (!drillDevices[r.id]) {
      try {
        const devs = await fetchDiscoveredPlatformDevices(r.id)
        setDrillDevices((prev) => ({ ...prev, [r.id]: devs }))
      } catch { /* leave empty */ }
    }
  }

  const exportCsv = () => {
    const headers = ['Platform', 'Model', 'OS Version', 'Devices', 'Status']
    const esc = (v: string) => `"${(v ?? '').replace(/"/g, '""')}"`
    const lines = [headers.join(',')]
    rows.forEach((r) => lines.push([r.platform, r.model, r.os_version, String(r.device_count), r.os_status].map((v) => esc(String(v))).join(',')))
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'fleet-inventory.csv'; a.click()
    URL.revokeObjectURL(url)
  }

  const summaryStats = useMemo(() => {
    if (!summary) return null
    const compliant = (summary.approved ?? 0) + (summary.preferred ?? 0)
    return { compliant, deprecated: summary.deprecated ?? 0, prohibited: summary.prohibited ?? 0, unknown: summary.unknown ?? 0 }
  }, [summary])

  return (
    <div>
      <SectionHeader
        title="Discovered Platforms & Models"
        description="Every platform / model / OS version combination across your fleet, scored against your OS version policies."
        action={
          <div className="flex gap-2">
            <button onClick={exportCsv} disabled={rows.length === 0} className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-40">Export</button>
            <button onClick={refresh} disabled={refreshing} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        }
      />

      {error && <div className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</div>}

      {/* Summary bar + donut */}
      {summaryStats && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-5">
          <div className="lg:col-span-2 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 flex flex-wrap items-center gap-x-8 gap-y-3">
            <Stat icon="✅" label="Compliant" value={summaryStats.compliant} color="text-green-600 dark:text-green-400" />
            <Stat icon="🟠" label="Deprecated" value={summaryStats.deprecated} color="text-orange-600 dark:text-orange-400" />
            <Stat icon="❌" label="Prohibited" value={summaryStats.prohibited} color="text-red-600 dark:text-red-400" />
            <Stat icon="❓" label="Unknown policy" value={summaryStats.unknown} color="text-gray-500 dark:text-gray-400" />
            <div className="ml-auto text-xs text-gray-400">{summary?.total_devices ?? 0} devices total</div>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-2">
            {summary && (summary.total_devices ?? 0) > 0
              ? <ReactECharts option={donutOption(summary, isDark)} style={{ height: 200 }} opts={{ renderer: 'svg' }} notMerge />
              : <div className="h-[200px] flex items-center justify-center text-xs text-gray-400">No devices</div>}
          </div>
        </div>
      )}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-sm text-gray-400">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500 dark:text-gray-400">
            No platforms discovered yet. Click Refresh to scan current inventory.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Platform</th>
                <th className="px-5 py-3 font-medium">Model</th>
                <th className="px-5 py-3 font-medium">OS Version</th>
                <th className="px-5 py-3 font-medium text-center">Devices</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {rows.map((r) => (
                <FleetRow
                  key={r.id}
                  r={r}
                  expanded={expanded === r.id}
                  devices={drillDevices[r.id]}
                  onToggle={() => toggleRow(r)}
                  onAddPolicy={() => setPolicyFor(r)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
        {ORDER.map((s) => (
          <span key={s} className="inline-flex items-center gap-1">
            <span aria-hidden>{OS_STATUS_META[s].icon}</span>{OS_STATUS_META[s].label}
          </span>
        ))}
      </div>

      {policyFor && (
        <AddPolicyModal combo={policyFor} onClose={() => setPolicyFor(null)} onSaved={() => { setPolicyFor(null); load() }} />
      )}
    </div>
  )
}

function Stat({ icon, label, value, color }: { icon: string; label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-lg" aria-hidden>{icon}</span>
      <div>
        <div className={`text-xl font-bold ${color}`}>{value}</div>
        <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
      </div>
    </div>
  )
}

function FleetRow({ r, expanded, devices, onToggle, onAddPolicy }: {
  r: DiscoveredPlatformModel
  expanded: boolean
  devices: Device[] | undefined
  onToggle: () => void
  onAddPolicy: () => void
}) {
  return (
    <>
      <tr className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer" onClick={onToggle}>
        <td className="px-5 py-3 font-mono text-xs text-gray-700 dark:text-gray-200">{r.platform}</td>
        <td className="px-5 py-3 text-gray-700 dark:text-gray-200">{r.model || <span className="text-gray-400">—</span>}</td>
        <td className="px-5 py-3 font-mono text-xs">{r.os_version || <span className="text-gray-400">—</span>}</td>
        <td className="px-5 py-3 text-center">{r.device_count}</td>
        <td className="px-5 py-3"><OSStatusBadge status={r.os_status} /></td>
        <td className="px-5 py-3">
          <div className="flex gap-2 justify-end" onClick={(e) => e.stopPropagation()}>
            {r.os_status === 'unknown' && (
              <button onClick={onAddPolicy} className="px-2.5 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-md font-medium">Add to Policy</button>
            )}
            <button onClick={onToggle} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50">
              {expanded ? 'Hide' : 'Devices'}
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50 dark:bg-gray-900/40">
          <td colSpan={6} className="px-6 py-3">
            {devices === undefined ? (
              <div className="text-xs text-gray-400">Loading devices…</div>
            ) : devices.length === 0 ? (
              <div className="text-xs text-gray-400">No devices.</div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {devices.map((d) => (
                  <Link key={d.id} to={`/devices/${d.id}`}
                    className="px-2.5 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30">
                    {d.display_hostname || d.hostname}
                  </Link>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

function AddPolicyModal({ combo, onClose, onSaved }: {
  combo: DiscoveredPlatformModel
  onClose: () => void
  onSaved: () => void
}) {
  const [status, setStatus] = useState<OSPolicyStatus>('approved')
  const [pattern, setPattern] = useState(combo.os_version)
  const [isRegex, setIsRegex] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const save = async () => {
    setSaving(true)
    try {
      await createApprovedOSVersion({ platform: combo.platform, version_pattern: pattern, is_regex: isRegex, status, notes: '' })
      onSaved()
    } catch (e) {
      setErr(parseApiErrors(e) || 'Failed to add policy.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="Add to Policy"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700">Cancel</button>
          <button onClick={save} disabled={saving} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Add Policy'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Create a policy for <span className="font-mono text-gray-700 dark:text-gray-200">{combo.platform}</span>.
        </p>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Version Pattern</label>
          <input className={inputCls} value={pattern} onChange={(e) => setPattern(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={isRegex} onChange={(e) => setIsRegex(e.target.checked)} />
          Treat pattern as a regular expression
        </label>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Status</label>
          <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value as OSPolicyStatus)}>
            <option value="preferred">🟢 Preferred</option>
            <option value="approved">🟡 Approved</option>
            <option value="deprecated">🟠 Deprecated</option>
            <option value="prohibited">🔴 Prohibited</option>
          </select>
        </div>
      </div>
    </Modal>
  )
}
