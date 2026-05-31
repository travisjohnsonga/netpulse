import { useEffect, useState, useCallback, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import EmptyState from '../components/EmptyState'
import DeviceAddModal from '../components/DeviceAddModal'
import ColumnPicker from '../components/ColumnPicker'
import { fetchDevices, fetchCredentials, fetchSites, type Device, type Site } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import {
  DEVICE_COLUMNS, defaultColumnKeys, loadColumnKeys, saveColumnKeys, type ColCtx,
} from '../lib/deviceColumns'

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
  const [siteFilter, setSiteFilter] = useState('All')
  const [sites, setSites] = useState<Site[]>([])
  // Sort: column sortKey with optional leading '-' for descending (DRF ordering).
  const [ordering, setOrdering] = useState('hostname')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showDiscoveryModal, setShowDiscoveryModal] = useState(false)
  const [columnKeys, setColumnKeys] = useState<string[]>(loadColumnKeys)
  const [credNames, setCredNames] = useState<Record<number, string>>({})

  useEffect(() => {
    fetchCredentials()
      .then((profiles) => setCredNames(Object.fromEntries(profiles.map((p) => [p.id, p.name]))))
      .catch(() => {})
    fetchSites().then(setSites).catch(() => {})
  }, [])

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
    localStorage.removeItem('netpulse.devices.columns')
    setColumnKeys(defaultColumnKeys())
  }

  const activeColumns = useMemo(
    () => columnKeys.map((k) => DEVICE_COLUMNS.find((c) => c.key === k)).filter(Boolean) as typeof DEVICE_COLUMNS,
    [columnKeys],
  )
  const colCtx: ColCtx = { credNames }

  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = { page: String(page), page_size: String(PAGE_SIZE) }
    if (search) params.search = search
    if (statusFilter !== 'All') params.status = statusFilter
    if (platformFilter !== 'All') params.platform = platformFilter
    if (siteFilter !== 'All') params.site = siteFilter
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
  }, [page, search, statusFilter, platformFilter, siteFilter, ordering])

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
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            {total > 0 ? `${total} device${total !== 1 ? 's' : ''} managed` : 'No devices yet'}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowDiscoveryModal(true)}
            className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
          >
            Run Discovery
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
          >
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

      {/* Filters */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 flex flex-col sm:flex-row gap-3">
        <input
          type="search"
          placeholder="Search hostname, IP, vendor..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
          className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>{s === 'All' ? 'All Statuses' : s}</option>
          ))}
        </select>
        <select
          value={platformFilter}
          onChange={(e) => { setPlatformFilter(e.target.value); setPage(1) }}
          className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {PLATFORM_OPTIONS.map((p) => (
            <option key={p} value={p}>{p === 'All' ? 'All Platforms' : p}</option>
          ))}
        </select>
        <select
          value={siteFilter}
          onChange={(e) => { setSiteFilter(e.target.value); setPage(1) }}
          className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="All">All Sites</option>
          {sites.map((s) => (
            <option key={s.id} value={String(s.id)}>{s.name}</option>
          ))}
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
              search || statusFilter !== 'All' || platformFilter !== 'All' || siteFilter !== 'All'
                ? 'No devices match your current filters. Try adjusting your search.'
                : 'Add your first device or run auto-discovery to populate this list.'
            }
            action={
              search || statusFilter !== 'All' || platformFilter !== 'All' || siteFilter !== 'All'
                ? { label: 'Clear Filters', onClick: () => { setSearch(''); setStatusFilter('All'); setPlatformFilter('All'); setSiteFilter('All') } }
                : { label: 'Add Device', onClick: () => setShowAddModal(true) }
            }
            icon="📡"
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                    {activeColumns.map((col) => {
                      const sortable = !!col.sortKey
                      const active = col.sortKey === ordering || `-${col.sortKey}` === ordering
                      const arrow = !active ? '' : ordering.startsWith('-') ? ' ↓' : ' ↑'
                      return (
                        <th
                          key={col.key}
                          onClick={sortable ? () => toggleSort(col.sortKey as string) : undefined}
                          className={`px-5 py-3 font-medium whitespace-nowrap ${sortable ? 'cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-200' : ''} ${active ? 'text-gray-700 dark:text-gray-200' : ''}`}
                        >
                          {col.label}<span className="text-blue-500">{arrow}</span>
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                  {devices.map((device) => (
                    <tr
                      key={device.id}
                      onClick={() => navigate(`/devices/${device.id}`)}
                      className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer"
                    >
                      {activeColumns.map((col) => (
                        <td key={col.key} className="px-5 py-3">{col.render(device, colCtx)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-5 py-3 border-t border-gray-200 dark:border-gray-700 text-sm">
                <span className="text-gray-500 dark:text-gray-400">
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
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
              Automatically discover devices on your network using SNMP, gNMI, NETCONF, and topology walking.
            </p>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-6">
              <p className="text-xs text-blue-800">
                <strong>Tip:</strong> Start with a seed device — NetPulse will walk CDP/LLDP neighbors
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
