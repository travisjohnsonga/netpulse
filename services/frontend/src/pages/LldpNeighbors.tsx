import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import {
  fetchUndiscoveredLldp,
  fetchDevices,
  type UndiscoveredLldpNeighbor,
} from '../api/client'
import DeviceAddModal, { type DeviceAddPrefill } from '../components/DeviceAddModal'
import EmptyState from '../components/EmptyState'
import { CAP_META, CAP_OPTIONS } from '../lib/lldpCapabilities'
import { useSite } from '../store/siteStore'

// Infrastructure-ish capabilities visible by default; phones/PCs/cable modems
// are hidden until the user opts in (they're rarely worth adding to inventory).
const DEFAULT_SHOW_CAPS = ['router', 'bridge', 'wlan-ap', 'repeater', 'other']

type IpFilter = 'all' | 'has-ip' | 'no-ip'

type LldpFilters = {
  search: string
  showCaps: string[]
  showNoCaps: boolean
  ipFilter: IpFilter
}

const DEFAULT_FILTERS: LldpFilters = {
  search: '',
  showCaps: DEFAULT_SHOW_CAPS,
  showNoCaps: true,
  ipFilter: 'all',
}

const FILTERS_KEY = 'lldp_filters'

function loadFilters(): LldpFilters {
  try {
    const saved = localStorage.getItem(FILTERS_KEY)
    if (saved) return { ...DEFAULT_FILTERS, ...JSON.parse(saved) }
  } catch {
    /* corrupt/unavailable storage → fall back to defaults */
  }
  return DEFAULT_FILTERS
}

function capIcons(caps: string[]) {
  if (!caps.length) return <span className="text-gray-300 dark:text-gray-600">—</span>
  return (
    <span className="inline-flex gap-1">
      {caps.map((c) => {
        const m = CAP_META[c]
        return (
          <span key={c} title={m?.label ?? c} className="text-sm">
            {m?.icon ?? '•'}
          </span>
        )
      })}
    </span>
  )
}

function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function LldpNeighbors() {
  const [rows, setRows] = useState<UndiscoveredLldpNeighbor[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [filters, setFilters] = useState<LldpFilters>(loadFilters)
  const [seenBy, setSeenBy] = useState<string>('all')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [addPrefill, setAddPrefill] = useState<DeviceAddPrefill | null>(null)
  // Neighbours are scoped to the active site by the device that observed them
  // (seen_by_device_id ∈ devices at the selected site).
  const { selectedSite } = useSite()
  const [siteDeviceIds, setSiteDeviceIds] = useState<Set<number> | null>(null)

  useEffect(() => {
    if (!selectedSite) { setSiteDeviceIds(null); return }
    let cancelled = false
    fetchDevices({ site: selectedSite, page_size: '1000' })
      .then((d) => { if (!cancelled) setSiteDeviceIds(new Set(d.results.map((x) => x.id))) })
      .catch(() => { if (!cancelled) setSiteDeviceIds(new Set()) })
    return () => { cancelled = true }
  }, [selectedSite])

  const { search, showCaps, showNoCaps, ipFilter } = filters
  const patch = useCallback(
    (p: Partial<LldpFilters>) => setFilters((f) => ({ ...f, ...p })), [])
  const toggleCap = useCallback((cap: string) => setFilters((f) => ({
    ...f,
    showCaps: f.showCaps.includes(cap)
      ? f.showCaps.filter((c) => c !== cap)
      : [...f.showCaps, cap],
  })), [])

  // Persist filter selection across page loads.
  useEffect(() => {
    try { localStorage.setItem(FILTERS_KEY, JSON.stringify(filters)) } catch { /* ignore */ }
  }, [filters])

  const load = useCallback(() => {
    setLoading(true)
    // Fetch the full set (override the server-side default exclusion) and apply
    // the user's capability filters client-side so toggling never refetches.
    fetchUndiscoveredLldp({ exclude_capabilities: '' })
      .then((data) => { setRows(data.results); setError(null) })
      .catch(() => setError('Failed to load LLDP neighbors.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  // Distinct "seen by" devices for the dropdown.
  const seenByOptions = useMemo(() => {
    const map = new Map<number, string>()
    rows.forEach((r) => map.set(r.seen_by_device_id, r.seen_by_device_hostname))
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [rows])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    const show = new Set(showCaps)
    return rows.filter((r) => {
      if (siteDeviceIds && !siteDeviceIds.has(r.seen_by_device_id)) return false
      if (q) {
        const hay = `${r.system_name} ${r.management_address ?? ''} ${r.chassis_id} ${r.seen_by_device_hostname}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      if (seenBy !== 'all' && String(r.seen_by_device_id) !== seenBy) return false
      // Capability filter: a row with no capabilities is governed by showNoCaps
      // (unknown could be anything); otherwise at least one of its capabilities
      // must be in the selected set.
      if (r.capabilities.length === 0) {
        if (!showNoCaps) return false
      } else if (!r.capabilities.some((c) => show.has(c))) {
        return false
      }
      if (ipFilter === 'has-ip' && !r.management_address) return false
      if (ipFilter === 'no-ip' && r.management_address) return false
      return true
    })
  }, [rows, search, seenBy, showCaps, showNoCaps, ipFilter, siteDeviceIds])

  // Other ports/devices the same neighbor was seen on (matched by chassis or name).
  const sightingsOf = useCallback((r: UndiscoveredLldpNeighbor) => {
    const key = r.chassis_id || r.system_name
    if (!key) return [r]
    return rows.filter((o) => (o.chassis_id || o.system_name) === key)
  }, [rows])

  const exportCsv = () => {
    const headers = ['System Name', 'Chassis ID', 'Chassis Type', 'Mgmt IP', 'Capabilities',
      'Seen By', 'Interface', 'Port ID', 'Port Description', 'Last Seen', 'Guessed Platform']
    const esc = (v: string) => `"${(v ?? '').replace(/"/g, '""')}"`
    const lines = [headers.join(',')]
    filtered.forEach((r) => lines.push([
      r.system_name, r.chassis_id, r.chassis_id_type, r.management_address ?? '',
      r.capabilities.join(' '), r.seen_by_device_hostname, r.seen_on_interface,
      r.port_id, r.port_description, r.last_seen ?? '', r.guessed_platform,
    ].map((v) => esc(String(v))).join(',')))
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'lldp-undiscovered.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const openAdd = (r: UndiscoveredLldpNeighbor) => setAddPrefill({
    hostname: r.system_name ? r.system_name.split('.')[0] : '',
    ip_address: r.management_address ?? '',
    management_ip: r.management_address ?? '',
    platform: r.guessed_platform,
  })

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">LLDP Neighbors — Not in Inventory</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            {rows.length > 0
              ? `${rows.length} neighbor${rows.length !== 1 ? 's' : ''} discovered via LLDP but not yet added to your inventory.`
              : 'No undiscovered LLDP neighbors — your inventory matches what your devices see.'}
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <button
            onClick={exportCsv}
            disabled={filtered.length === 0}
            className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-40"
          >
            Export CSV
          </button>
          <button
            onClick={load}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 space-y-3">
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            type="search"
            value={search}
            onChange={(e) => patch({ search: e.target.value })}
            placeholder="Search hostname / IP / MAC…"
            className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-900 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <select value={seenBy} onChange={(e) => setSeenBy(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-900 rounded-lg text-sm">
            <option value="all">All devices</option>
            {seenByOptions.map(([id, name]) => <option key={id} value={String(id)}>{name}</option>)}
          </select>
          <select value={ipFilter} onChange={(e) => patch({ ipFilter: e.target.value as IpFilter })}
            className="px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-900 rounded-lg text-sm">
            <option value="all">Any IP</option>
            <option value="has-ip">Has mgmt IP</option>
            <option value="no-ip">No mgmt IP</option>
          </select>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="text-xs font-medium uppercase tracking-wide text-gray-400">Show:</span>
          {CAP_OPTIONS.map((cap) => (
            <label key={cap} className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={showCaps.includes(cap)}
                onChange={() => toggleCap(cap)}
                className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
              />
              <span title={CAP_META[cap]?.label}>{CAP_META[cap]?.icon}</span>
              {CAP_META[cap]?.label ?? cap}
            </label>
          ))}
          <label className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showNoCaps}
              onChange={() => patch({ showNoCaps: !showNoCaps })}
              className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
            />
            Unknown
          </label>
          <button
            onClick={() => setFilters(DEFAULT_FILTERS)}
            className="ml-auto text-xs text-blue-600 dark:text-blue-400 hover:underline"
          >
            Reset filters
          </button>
        </div>
      </div>

      {error && <div className="text-sm text-red-600 dark:text-red-400">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-sm text-gray-400">Loading…</div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon="📡"
            title={rows.length === 0 ? 'No undiscovered neighbors' : 'No matches'}
            description={rows.length === 0
              ? 'Run topology/LLDP discovery on your devices to surface neighbors here.'
              : 'No neighbors match the current filters.'}
          />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-4 py-3 font-medium">System Name</th>
                <th className="px-4 py-3 font-medium">MAC / Chassis</th>
                <th className="px-4 py-3 font-medium">Mgmt IP</th>
                <th className="px-4 py-3 font-medium">Capabilities</th>
                <th className="px-4 py-3 font-medium">Seen By</th>
                <th className="px-4 py-3 font-medium">Last Seen</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {filtered.map((r) => (
                <RowGroup
                  key={r.id}
                  r={r}
                  expanded={expanded === r.id}
                  onToggle={() => setExpanded(expanded === r.id ? null : r.id)}
                  onAdd={() => openAdd(r)}
                  sightings={sightingsOf(r)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {addPrefill && (
        <DeviceAddModal
          initial={addPrefill}
          onClose={() => setAddPrefill(null)}
          onCreated={() => { setAddPrefill(null); load() }}
        />
      )}
    </div>
  )
}

function RowGroup({ r, expanded, onToggle, onAdd, sightings }: {
  r: UndiscoveredLldpNeighbor
  expanded: boolean
  onToggle: () => void
  onAdd: () => void
  sightings: UndiscoveredLldpNeighbor[]
}) {
  return (
    <>
      <tr className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
        <td className="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">
          {r.system_name || <span className="text-gray-400 italic">Unknown</span>}
        </td>
        <td className="px-4 py-3 font-mono text-xs text-gray-600 dark:text-gray-300">
          {r.chassis_id_type === 'mac' && r.chassis_id
            ? r.chassis_id
            : <span className="text-gray-400">{r.chassis_id || '—'}</span>}
        </td>
        <td className="px-4 py-3">
          {r.management_address || <span className="text-gray-400 text-xs italic">via MAC</span>}
        </td>
        <td className="px-4 py-3">{capIcons(r.capabilities)}</td>
        <td className="px-4 py-3">
          <Link to={`/devices/${r.seen_by_device_id}`} className="text-blue-600 dark:text-blue-400 hover:underline">
            {r.seen_by_device_hostname}
          </Link>
          <span className="text-gray-400 text-xs"> · {r.seen_on_interface}</span>
        </td>
        <td className="px-4 py-3 text-gray-500 dark:text-gray-400 text-xs whitespace-nowrap">{fmtWhen(r.last_seen)}</td>
        <td className="px-4 py-3">
          <div className="flex gap-2 justify-end">
            <button
              onClick={onAdd}
              className="px-2.5 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-md font-medium"
            >
              + Add
            </button>
            <button
              onClick={onToggle}
              title="Details"
              className={clsx('px-2.5 py-1 text-xs border rounded-md',
                expanded
                  ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                  : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50')}
            >
              🔍
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50 dark:bg-gray-900/40">
          <td colSpan={7} className="px-6 py-4">
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <Detail label="System Description">
                {r.system_description || <span className="text-gray-400">—</span>}
              </Detail>
              <Detail label="Guessed Platform">
                <code className="text-xs">{r.guessed_platform}</code>
              </Detail>
              <Detail label="Port ID">{r.port_id || '—'}</Detail>
              <Detail label="Port Description">{r.port_description || '—'}</Detail>
              <Detail label="Capabilities">
                {r.capabilities.length
                  ? r.capabilities.map((c) => CAP_META[c]?.label ?? c).join(', ')
                  : '—'}
              </Detail>
              <Detail label="Chassis ID">
                <span className="font-mono text-xs">{r.chassis_id || '—'}</span>
                {r.chassis_id_type && <span className="text-gray-400 text-xs"> ({r.chassis_id_type})</span>}
              </Detail>
              <div className="md:col-span-2">
                <dt className="text-xs uppercase tracking-wide text-gray-400 mb-1">Seen on</dt>
                <dd className="space-y-1">
                  {sightings.map((s) => (
                    <div key={s.id} className="text-sm">
                      <Link to={`/devices/${s.seen_by_device_id}`} className="text-blue-600 dark:text-blue-400 hover:underline">
                        {s.seen_by_device_hostname}
                      </Link>
                      <span className="text-gray-500"> : {s.seen_on_interface}</span>
                      {s.port_id && <span className="text-gray-400 text-xs"> → {s.port_id}</span>}
                    </div>
                  ))}
                </dd>
              </div>
            </dl>
          </td>
        </tr>
      )}
    </>
  )
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-gray-400 mb-0.5">{label}</dt>
      <dd className="text-gray-700 dark:text-gray-200 break-words">{children}</dd>
    </div>
  )
}
