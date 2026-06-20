import { useEffect, useState } from 'react'
import {
  fetchManualLinks, deleteManualLink, MANUAL_LINK_COLORS,
  type ManualTopologyLink,
} from '../api/client'
import ManualLinkModal from '../components/ManualLinkModal'
import EmptyState from '../components/EmptyState'

export default function NetworkManualLinks() {
  const [links, setLinks] = useState<ManualTopologyLink[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<ManualTopologyLink | null>(null)
  const [deleting, setDeleting] = useState<ManualTopologyLink | null>(null)

  const load = () => {
    setLoading(true)
    fetchManualLinks()
      .then(setLinks)
      .catch(() => setError('Failed to load manual links.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const confirmDelete = async () => {
    if (!deleting) return
    try { await deleteManualLink(deleting.id); setDeleting(null); load() }
    catch { setError('Could not delete the link.') }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Manual Links</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Operator-defined topology links for devices that don't support LLDP/CDP (firewalls, WAN, virtual).
          </p>
        </div>
        <button onClick={() => setAdding(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
          + Add Manual Link
        </button>
      </div>

      {error && <div className="bg-yellow-50 border border-yellow-200 dark:bg-yellow-900/30 dark:border-yellow-800 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:text-yellow-300">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : links.length === 0 ? (
          <EmptyState title="No manual links" icon="🔗"
            description="Add a manual link to connect devices the discovery engine can't see via LLDP/CDP."
            action={{ label: 'Add Manual Link', onClick: () => setAdding(true) }} />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Device A</th>
                <th className="px-5 py-3 font-medium">Device B</th>
                <th className="px-5 py-3 font-medium">Type</th>
                <th className="px-5 py-3 font-medium">Speed</th>
                <th className="px-5 py-3 font-medium">Description</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {links.map((l) => (
                <tr key={l.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-5 py-3">
                    <div className="font-medium text-gray-800 dark:text-gray-100">{l.device_a_hostname}</div>
                    {l.interface_a && <div className="text-xs text-gray-400 font-mono">{l.interface_a}</div>}
                  </td>
                  <td className="px-5 py-3">
                    <div className="font-medium text-gray-800 dark:text-gray-100">{l.device_b_hostname}</div>
                    {l.interface_b && <div className="text-xs text-gray-400 font-mono">{l.interface_b}</div>}
                  </td>
                  <td className="px-5 py-3">
                    <span className="inline-flex items-center gap-1.5 text-xs font-medium">
                      <span className="w-2.5 h-2.5 rounded-full" style={{ background: MANUAL_LINK_COLORS[l.link_type] }} />
                      {l.link_type_display}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-300 font-mono text-xs">
                    {l.speed_mbps ? (l.speed_mbps >= 1000 ? `${l.speed_mbps / 1000} Gbps` : `${l.speed_mbps} Mbps`) : '—'}
                  </td>
                  <td className="px-5 py-3 text-gray-500 dark:text-gray-400 max-w-xs truncate">{l.description || '—'}</td>
                  <td className="px-5 py-3 text-right whitespace-nowrap">
                    <button onClick={() => setEditing(l)} className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-800 font-medium mr-3" title="Edit">✏️ Edit</button>
                    <button onClick={() => setDeleting(l)} className="text-xs text-red-600 dark:text-red-400 hover:text-red-800 font-medium" title="Delete">🗑 Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {adding && <ManualLinkModal onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load() }} />}
      {editing && <ManualLinkModal edit={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />}

      {deleting && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setDeleting(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Delete manual link?</h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
              {deleting.device_a_hostname} ↔ {deleting.device_b_hostname} ({deleting.link_type_display}). This can't be undone.
            </p>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setDeleting(null)} className="px-4 py-2 rounded-md text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">Cancel</button>
              <button onClick={confirmDelete} className="px-4 py-2 rounded-md text-sm font-medium bg-red-600 hover:bg-red-700 text-white">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
