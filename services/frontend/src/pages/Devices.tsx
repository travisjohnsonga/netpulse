import { useEffect, useState, useCallback, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import EmptyState from '../components/EmptyState'
import DeviceAddModal from '../components/DeviceAddModal'
import ColumnPicker from '../components/ColumnPicker'
import { fetchDevices, fetchCredentials, fetchDeviceRoles, fetchPingSummary,
  fetchDeviceMetricsSummary, fetchDeviceStatusSummary,
  type Device, type DeviceRole, type PingSummary, type DeviceMetricsSummary,
  type DeviceStatusSummary } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import { useComplianceRunAll } from '../hooks/useComplianceRun'
import { useSite } from '../store/siteStore'
import {
  DEVICE_COLUMNS, defaultColumnKeys, loadColumnKeys, saveColumnKeys, COLUMN_STORAGE_KEY, type ColCtx,
} from '../lib/deviceColumns'
import { sshUrl, sshTooltip } from '../lib/ssh'
import { INPUT, SELECT, BTN_PRIMARY, BTN_SECONDARY } from '../lib/ui'
import { STRIPED_ROW } from '../lib/tableStyles'
import StatCard from '../components/StatCard'

const PLATFORM_OPTIONS = ['All', 'IOS-XE', 'IOS-XR', 'NX-OS', 'Junos', 'EOS', 'FortiOS', 'Other']
const STATUS_OPTIONS = ['All', 'active', 'inactive', 'pending', 'unreachable']
const PAGE_SIZE = 20

export default function Devices() {
  const navigate = useNavigate()
  const location = useLocation()
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Toast passed via navigation state (e.g. redirect from a deleted device).
  const [toast, setToast] = useState<string | null>(
    (location.state as { toast?: string } | null)?.toast ?? null,
  )
  useEffect(() => {
    if (!toast) return
    // Clear the history state so the toast doesn't reappear on back/refresh.
    navigate(location.pathname, { replace: true, state: {} })
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('All')
  const [platformFilter, setPlatformFilter] = useState('All')
  const [roleFilter, setRoleFilter] = useState('All')
  const [complianceFilter, setComplianceFilter] = useState('All')
  const [roles, setRoles] = useState<DeviceRole[]>([])
  // Site scoping comes from the global header selector (persists across pages).
  const { selectedSite } = useSite()
  // Sort: column sortKey with optional leading '-' for descending (DRF ordering).
  const [ordering, setOrdering] = useState('hostname')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showDiscoveryModal, setShowDiscoveryModal] = useState(false)
  const [columnKeys, setColumnKeys] = useState<string[]>(loadColumnKeys)
  const [credNames, setCredNames] = useState<Record<number, string>>({})
  const [pingMap, setPingMap] = useState<Record<number, PingSummary>>({})
  const [metricsMap, setMetricsMap] = useState<Record<number, DeviceMetricsSummary>>({})
  const [statusSummary, setStatusSummary] = useState<DeviceStatusSummary | null>(null)
  // Bulk selection for "Run Compliance" on the chosen devices.
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const { status: runStatus, start: startRun, starting, isRunning } = useComplianceRunAll()
  const toggleSelect = (id: number) => setSelected((prev) => {
    const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next
  })
  const pageIds = devices.map((d) => d.id)
  const allSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id))
  const someSelected = selected.size > 0 && !allSelected
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(pageIds))
  const runComplianceSelected = () => { if (selected.size) startRun(Array.from(selected)) }

  useEffect(() => {
    fetchCredentials()
      .then((profiles) => setCredNames(Object.fromEntries(profiles.map((p) => [p.id, p.name]))))
      .catch(() => {})
    fetchDeviceRoles().then(setRoles).catch(() => {})
  }, [])

  // Reset to the first page whenever the global site filter changes.
  useEffect(() => { setPage(1) }, [selectedSite])

  // Ping sparklines + CPU/Mem: fetched in the background (don't block the list
  // render), refreshed on the 60s cache cadence. Failures are non-fatal.
  useEffect(() => {
    let active = true
    const loadTelemetry = () => {
      fetchPingSummary()
        .then((rows) => { if (active) setPingMap(Object.fromEntries(rows.map((r) => [r.device_id, r]))) })
        .catch(() => {})
      fetchDeviceMetricsSummary()
        .then((rows) => { if (active) setMetricsMap(Object.fromEntries(rows.map((r) => [r.device_id, r]))) })
        .catch(() => {})
    }
    loadTelemetry()
    const t = setInterval(loadTelemetry, 60000)
    return () => { active = false; clearInterval(t) }
  }, [])

  // Summary-card counts (DB totals over the network-device set, site-scoped) —
  // the list is paginated so these can't be derived from the current page.
  useEffect(() => {
    let active = true
    fetchDeviceStatusSummary(selectedSite || undefined)
      .then((s) => { if (active) setStatusSummary(s) })
      .catch(() => { if (active) setStatusSummary(null) })
    return () => { active = false }
  }, [selectedSite, total])

  // Live reachability updates: patch the matching row when the monitor pushes.
  const { lastMessage } = useWebSocket('/ws/devices/')
  useEffect(() => {
    const m = lastMessage as { type?: string; device_id?: number; is_reachable?: boolean; status?: string } | null
    if (!m || m.type !== 'device_status' || m.device_id == null) return
    const nowIso = new Date().toISOString()
    setDevices((prev) => prev.map((d) => {
      if (d.id !== m.device_id) return d
      const status = (m.status as Device['status']) ?? d.status
      return {
        ...d,
        is_reachable: m.is_reachable,
        status,
        last_seen: m.is_reachable ? nowIso : d.last_seen,
        // Start/stop the downtime clock so the badge updates live.
        unreachable_since: status === 'unreachable' ? (d.unreachable_since ?? nowIso) : null,
      }
    }))
  }, [lastMessage])

  // Click a sortable header: toggle asc/desc, or switch sort column.
  const toggleSort = (sortKey: string) => {
    setPage(1)
    setOrdering((cur) => (cur === sortKey ? `-${sortKey}` : cur === `-${sortKey}` ? sortKey : sortKey))
  }

  const setColumns = (keys: string[]) => { setColumnKeys(keys); saveColumnKeys(keys) }
  const resetColumns = () => {
    localStorage.removeItem(COLUMN_STORAGE_KEY)
    setColumnKeys(defaultColumnKeys())
  }

  const activeColumns = useMemo(
    () => columnKeys.map((k) => DEVICE_COLUMNS.find((c) => c.key === k)).filter(Boolean) as typeof DEVICE_COLUMNS,
    [columnKeys],
  )
  const colCtx: ColCtx = { credNames, ping: pingMap, metrics: metricsMap }

  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = { page: String(page), page_size: String(PAGE_SIZE) }
    if (search) params.search = search
    if (statusFilter !== 'All') params.status = statusFilter
    if (platformFilter !== 'All') params.platform = platformFilter
    if (selectedSite) params.site = selectedSite
    if (roleFilter !== 'All') params.role = roleFilter
    if (complianceFilter !== 'All') params.compliance_grade = complianceFilter
    if (ordering) params.ordering = ordering

    fetchDevices(params)
      .then((data) => {
        setDevices(data.results)
        setTotal(data.count)
        setLoading(false)
        setError(null)
      })
      .catch(() => {
        setError('Failed to load devices. Check that the API is running.')
        setLoading(false)
      })
  }, [page, search, statusFilter, platformFilter, selectedSite, roleFilter, complianceFilter, ordering])

  useEffect(() => { load() }, [load])

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="space-y-4">
      {/* Toast (e.g. redirected here after a device was not found) */}
      {toast && (
        <div className="bg-amber-50 border border-amber-200 dark:bg-amber-900/30 dark:border-amber-800 rounded-lg px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
          {toast}
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Devices</h1>
          <p className="text-sm text-gray-500 dark:text-gray-300 mt-0.5">
            {total > 0 ? `${total} device${total !== 1 ? 's' : ''} managed` : 'No devices yet'}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowDiscoveryModal(true)} className={BTN_SECONDARY}>
            Run Discovery
          </button>
          <button onClick={() => setShowAddModal(true)} className={BTN_PRIMARY}>
            + Add Device
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">
          {error}
        </div>
      )}

      {/* Count-based summary (matches the Servers cards) — how many are down is
          the actionable number; a fleet CPU/mem average hides hosts in trouble. */}
      {statusSummary && (
        <div className="grid grid-cols-3 gap-3">
          <StatCard title="Total Devices" value={statusSummary.total} color="blue" />
          <StatCard title="Up" value={statusSummary.up} color="green" />
          <StatCard title="Down" value={statusSummary.down} color="red" />
        </div>
      )}

      {/* Filters */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 flex flex-col sm:flex-row gap-3">
        <input
          type="search"
          placeholder="Search hostname, IP, vendor..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          className={`flex-1 ${INPUT}`}
        />
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
          className={SELECT}
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>{s === 'All' ? 'All Statuses' : s}</option>
          ))}
        </select>
        <select
          value={platformFilter}
          onChange={(e) => { setPlatformFilter(e.target.value); setPage(1) }}
          className={SELECT}
        >
          {PLATFORM_OPTIONS.map((p) => (
            <option key={p} value={p}>{p === 'All' ? 'All Platforms' : p}</option>
          ))}
        </select>
        <select
          value={roleFilter}
          onChange={(e) => { setRoleFilter(e.target.value); setPage(1) }}
          className={SELECT}
        >
          <option value="All">All Roles</option>
          {roles.map((r) => (
            <option key={r.id} value={String(r.id)}>{r.name}</option>
          ))}
        </select>
        <select
          value={complianceFilter}
          onChange={(e) => { setComplianceFilter(e.target.value); setPage(1) }}
          className={SELECT}
        >
          <option value="All">Any Compliance</option>
          <option value="A">A (90–100)</option>
          <option value="B">B (80–89)</option>
          <option value="C">C (70–79)</option>
          <option value="D">D (60–69)</option>
          <option value="F">F (below 60)</option>
          <option value="none">Not checked</option>
        </select>
        <ColumnPicker activeKeys={columnKeys} onChange={setColumns} onReset={resetColumns} />
      </div>

      {/* Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : devices.length === 0 ? (
          <EmptyState
            title="No devices found"
            description={
              search || statusFilter !== 'All' || platformFilter !== 'All' || selectedSite || roleFilter !== 'All'
                ? 'No devices match your current filters. Try adjusting your search.'
                : 'Add your first device or run auto-discovery to populate this list.'
            }
            action={
              search || statusFilter !== 'All' || platformFilter !== 'All' || roleFilter !== 'All'
                ? { label: 'Clear Filters', onClick: () => { setSearch(''); setStatusFilter('All'); setPlatformFilter('All'); setRoleFilter('All') } }
                : { label: 'Add Device', onClick: () => setShowAddModal(true) }
            }
            icon="📡"
          />
        ) : (
          <>
            {/* Bulk action toolbar — visible when devices are selected */}
            {selected.size > 0 && (
              <div className="flex items-center gap-3 bg-blue-50 dark:bg-blue-900/20 border-b border-blue-200 dark:border-blue-800 px-5 py-2.5 text-sm">
                <span className="font-medium text-blue-800 dark:text-blue-300">✓ {selected.size} selected</span>
                <div className="flex items-center gap-2 ml-auto">
                  <button
                    onClick={runComplianceSelected}
                    disabled={starting || isRunning}
                    className="px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 font-medium"
                  >
                    {starting || isRunning ? 'Running…' : '▶ Run Compliance'}
                  </button>
                  <button onClick={() => setSelected(new Set())} className="px-3 py-1.5 rounded-md text-gray-500 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-200 font-medium">Clear</button>
                </div>
              </div>
            )}
            {runStatus && (runStatus.running || runStatus.done > 0) && (
              <div className="bg-blue-50 dark:bg-blue-900/30 border-b border-blue-200 dark:border-blue-800 px-5 py-2 text-sm text-blue-700 dark:text-blue-300 flex items-center gap-2">
                {runStatus.running ? (
                  <>
                    <span className="w-3.5 h-3.5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    Running compliance… {runStatus.done}/{runStatus.total} devices
                  </>
                ) : (
                  <>✅ Complete: {runStatus.success} passed, {runStatus.failed} failed</>
                )}
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                    {/* Frozen identity columns (checkbox + Hostname) so they stay
                        anchored while the metric columns scroll on laptop widths.
                        Checkbox fixed at w-12 (3rem) so Hostname pins flush at left-12. */}
                    <th className="w-12 px-3 py-3 sticky left-0 z-20 bg-gray-50 dark:bg-gray-900">
                      <input
                        type="checkbox"
                        aria-label="Select all devices on this page"
                        className="rounded border-gray-300 dark:border-gray-600 cursor-pointer"
                        checked={allSelected}
                        ref={(el) => { if (el) el.indeterminate = someSelected }}
                        onChange={toggleAll}
                      />
                    </th>
                    {activeColumns.map((col, i) => {
                      const sortable = !!col.sortKey
                      const active = col.sortKey === ordering || `-${col.sortKey}` === ordering
                      const arrow = !active ? '' : ordering.startsWith('-') ? ' ↓' : ' ↑'
                      const sticky = i === 0 ? 'sticky left-12 z-20 bg-gray-50 dark:bg-gray-900' : ''
                      return (
                        <th
                          key={col.key}
                          onClick={sortable ? () => toggleSort(col.sortKey as string) : undefined}
                          className={`px-5 py-3 font-medium whitespace-nowrap ${sticky} ${sortable ? 'cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-200' : ''} ${active ? 'text-gray-700 dark:text-gray-200' : ''}`}
                        >
                          {col.label}<span className="text-blue-500">{arrow}</span>
                        </th>
                      )
                    })}
                    <th className="px-5 py-3 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((device) => {
                    const isSel = selected.has(device.id)
                    // Frozen cells need an opaque bg matching the row state.
                    const frozenBg = isSel ? 'bg-blue-50 dark:bg-blue-900/40' : 'bg-white dark:bg-gray-800'
                    return (
                    <tr
                      key={device.id}
                      onClick={() => navigate(`/devices/${device.id}`)}
                      className={`cursor-pointer ${isSel ? 'bg-blue-50/60 dark:bg-blue-900/20' : STRIPED_ROW}`}
                    >
                      <td className={`w-12 px-3 py-3 sticky left-0 z-10 ${frozenBg}`} onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${device.hostname}`}
                          className="rounded border-gray-300 dark:border-gray-600 cursor-pointer"
                          checked={isSel}
                          onChange={() => toggleSelect(device.id)}
                        />
                      </td>
                      {activeColumns.map((col, i) => (
                        <td key={col.key}
                          className={`px-5 py-3 whitespace-nowrap ${i === 0 ? `sticky left-12 z-10 ${frozenBg}` : ''}`}>{col.render(device, colCtx)}</td>
                      ))}
                      {/* Right-aligned row action: opens the SSH/console (was the
                          inline "Connect" button next to the hostname). */}
                      <td className="px-5 py-3 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        <a
                          href={sshUrl(device)}
                          target="_blank" rel="noopener noreferrer"
                          title={sshTooltip(device.hostname, device)}
                          className="text-sm font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400"
                        >
                          SSH
                        </a>
                      </td>
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-5 py-3 border-t border-gray-200 dark:border-gray-700 text-sm">
                <span className="text-gray-500 dark:text-gray-300">
                  Page {page} of {totalPages}
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                    className="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page === totalPages}
                    className="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Add Device Modal — 5-step workflow */}
      {showAddModal && (
        <DeviceAddModal
          onClose={() => setShowAddModal(false)}
          onCreated={load}
        />
      )}

      {/* Discovery Modal (stub) */}
      {showDiscoveryModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-md p-6">
            <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-4">Auto-Discovery</h2>
            <p className="text-sm text-gray-500 dark:text-gray-300 mb-4">
              Automatically discover devices on your network using SNMP, gNMI, NETCONF, and topology walking.
            </p>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-6">
              <p className="text-xs text-blue-800">
                <strong>Tip:</strong> Start with a seed device — spane will walk CDP/LLDP neighbors
                and route tables to find the rest of your network.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setShowDiscoveryModal(false)}
                className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50"
              >
                Close
              </button>
              <button
                onClick={() => setShowDiscoveryModal(false)}
                className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
              >
                Configure Discovery
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
