import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import EmptyState from '../components/EmptyState'
import DeviceAddModal from '../components/DeviceAddModal'
import { fetchDevices, type Device } from '../api/client'

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-100 text-green-700',
  inactive: 'bg-gray-100 text-gray-600',
  pending: 'bg-yellow-100 text-yellow-700',
  unreachable: 'bg-red-100 text-red-700',
}

const PLATFORM_OPTIONS = ['All', 'IOS-XE', 'IOS-XR', 'NX-OS', 'Junos', 'EOS', 'FortiOS', 'Other']
const STATUS_OPTIONS = ['All', 'active', 'inactive', 'pending', 'unreachable']
const PAGE_SIZE = 20

export default function Devices() {
  const navigate = useNavigate()
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('All')
  const [platformFilter, setPlatformFilter] = useState('All')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showDiscoveryModal, setShowDiscoveryModal] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const params: Record<string, string> = { page: String(page), page_size: String(PAGE_SIZE) }
    if (search) params.search = search
    if (statusFilter !== 'All') params.status = statusFilter
    if (platformFilter !== 'All') params.platform = platformFilter

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
  }, [page, search, statusFilter, platformFilter])

  useEffect(() => { load() }, [load])

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Devices</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {total > 0 ? `${total} device${total !== 1 ? 's' : ''} managed` : 'No devices yet'}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowDiscoveryModal(true)}
            className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
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
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 flex flex-col sm:flex-row gap-3">
        <input
          type="search"
          placeholder="Search hostname, IP, vendor..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>{s === 'All' ? 'All Statuses' : s}</option>
          ))}
        </select>
        <select
          value={platformFilter}
          onChange={(e) => { setPlatformFilter(e.target.value); setPage(1) }}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {PLATFORM_OPTIONS.map((p) => (
            <option key={p} value={p}>{p === 'All' ? 'All Platforms' : p}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : devices.length === 0 ? (
          <EmptyState
            title="No devices found"
            description={
              search || statusFilter !== 'All' || platformFilter !== 'All'
                ? 'No devices match your current filters. Try adjusting your search.'
                : 'Add your first device or run auto-discovery to populate this list.'
            }
            action={
              search || statusFilter !== 'All' || platformFilter !== 'All'
                ? { label: 'Clear Filters', onClick: () => { setSearch(''); setStatusFilter('All'); setPlatformFilter('All') } }
                : { label: 'Add Device', onClick: () => setShowAddModal(true) }
            }
            icon="📡"
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                    <th className="px-5 py-3 font-medium">Hostname</th>
                    <th className="px-5 py-3 font-medium">IP Address</th>
                    <th className="px-5 py-3 font-medium">Platform</th>
                    <th className="px-5 py-3 font-medium">Vendor</th>
                    <th className="px-5 py-3 font-medium">Status</th>
                    <th className="px-5 py-3 font-medium">Last Seen</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {devices.map((device) => (
                    <tr
                      key={device.id}
                      onClick={() => navigate(`/devices/${device.id}`)}
                      className="hover:bg-gray-50 cursor-pointer"
                    >
                      <td className="px-5 py-3 font-medium text-gray-800">{device.hostname}</td>
                      <td className="px-5 py-3 text-gray-600 font-mono text-xs">{device.ip_address}</td>
                      <td className="px-5 py-3 text-gray-600">{device.platform}</td>
                      <td className="px-5 py-3 text-gray-600">{device.vendor}</td>
                      <td className="px-5 py-3">
                        <span
                          className={clsx(
                            'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                            STATUS_COLORS[device.status] ?? 'bg-gray-100 text-gray-600',
                          )}
                        >
                          {device.status}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-gray-500 text-xs">
                        {device.last_seen ? new Date(device.last_seen).toLocaleString() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-5 py-3 border-t border-gray-200 text-sm">
                <span className="text-gray-500">
                  Page {page} of {totalPages}
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                    className="px-3 py-1.5 border border-gray-300 rounded-md disabled:opacity-40 hover:bg-gray-50 transition-colors"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page === totalPages}
                    className="px-3 py-1.5 border border-gray-300 rounded-md disabled:opacity-40 hover:bg-gray-50 transition-colors"
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
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <h2 className="text-lg font-bold text-gray-900 mb-4">Auto-Discovery</h2>
            <p className="text-sm text-gray-500 mb-4">
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
                className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50"
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
