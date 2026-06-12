import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { fetchSites, type Site, type SiteType } from '../api/client'
import EmptyState from '../components/EmptyState'
import SiteFormModal from '../components/SiteFormModal'
import SiteDeviceStatus from '../components/SiteDeviceStatus'

const TYPE_BADGE: Record<SiteType, string> = {
  datacenter: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  campus: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  branch: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  remote: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  cloud: 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400',
}
const TYPE_ICON: Record<SiteType, string> = {
  datacenter: '🏢', campus: '🏫', branch: '🏬', remote: '📡', cloud: '☁️',
}
export default function Sites() {
  const navigate = useNavigate()
  const [sites, setSites] = useState<Site[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'table' | 'tree'>('table')
  const [editing, setEditing] = useState<Site | 'new' | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchSites()
      .then((s) => { setSites(s); setError(null) })
      .catch(() => setError('Failed to load sites. Check that the API is running.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Sites</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{sites.length} site{sites.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="flex gap-2">
          <div className="flex rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
            <button onClick={() => setView('table')} className={clsx('px-3 py-2 text-sm', view === 'table' ? 'bg-blue-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700')}>Table</button>
            <button onClick={() => setView('tree')} className={clsx('px-3 py-2 text-sm', view === 'tree' ? 'bg-blue-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700')}>Hierarchy</button>
          </div>
          <button onClick={() => setEditing('new')} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ Add Site</button>
        </div>
      </div>

      {error && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : sites.length === 0 ? (
          <EmptyState title="No sites yet" description="Add datacenters, campuses and branches to organize your devices." action={{ label: 'Add Site', onClick: () => setEditing('new') }} icon="🏢" />
        ) : view === 'table' ? (
          <TableView sites={sites} onOpen={(id) => navigate(`/sites/${id}`)} onEdit={setEditing} />
        ) : (
          <TreeView sites={sites} onOpen={(id) => navigate(`/sites/${id}`)} onEdit={setEditing} />
        )}
      </div>

      {editing && (
        <SiteFormModal
          site={editing === 'new' ? null : editing}
          sites={sites}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
          onDeleted={editing !== 'new' ? () => { setEditing(null); load() } : undefined}
        />
      )}
    </div>
  )
}

function TypeBadge({ t }: { t: SiteType }) {
  return <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', TYPE_BADGE[t])}>{TYPE_ICON[t]} {t}</span>
}

function TableView({ sites, onOpen, onEdit }: { sites: Site[]; onOpen: (id: number) => void; onEdit: (s: Site) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
            <th className="px-5 py-3 font-medium">Name</th>
            <th className="px-5 py-3 font-medium">Type</th>
            <th className="px-5 py-3 font-medium">City</th>
            <th className="px-5 py-3 font-medium">Devices</th>
            <th className="px-5 py-3 font-medium">Parent</th>
            <th className="px-5 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
          {sites.map((s) => (
            <tr key={s.id} onClick={() => onOpen(s.id)} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer">
              <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">{s.name}</td>
              <td className="px-5 py-3"><TypeBadge t={s.site_type} /></td>
              <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{s.city || '—'}</td>
              <td className="px-5 py-3"><SiteDeviceStatus site={s} /></td>
              <td className="px-5 py-3 text-gray-500 dark:text-gray-400">{s.parent_site_name || '—'}</td>
              <td className="px-5 py-3 text-right">
                <button
                  onClick={(e) => { e.stopPropagation(); onEdit(s) }}
                  className="text-blue-600 hover:text-blue-800 dark:text-blue-400 text-sm font-medium"
                >
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TreeView({ sites, onOpen, onEdit }: { sites: Site[]; onOpen: (id: number) => void; onEdit: (s: Site) => void }) {
  const byParent = useMemo(() => {
    const m = new Map<number | null, Site[]>()
    for (const s of sites) {
      const k = s.parent_site
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(s)
    }
    return m
  }, [sites])

  const render = (parent: number | null, depth: number): React.ReactNode =>
    (byParent.get(parent) ?? []).map((s) => (
      <div key={s.id}>
        <div
          onClick={() => onOpen(s.id)}
          className="group flex items-center gap-2 px-5 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer border-b border-gray-50 dark:border-gray-700"
          style={{ paddingLeft: `${1.25 + depth * 1.5}rem` }}
        >
          {depth > 0 && <span className="text-gray-300 dark:text-gray-600">└</span>}
          <span className="font-medium text-gray-800 dark:text-gray-100">{s.name}</span>
          <TypeBadge t={s.site_type} />
          <SiteDeviceStatus site={s} className="ml-auto" />
          <button
            onClick={(e) => { e.stopPropagation(); onEdit(s) }}
            className="text-blue-600 hover:text-blue-800 dark:text-blue-400 text-sm font-medium opacity-0 group-hover:opacity-100"
          >
            Edit
          </button>
        </div>
        {render(s.id, depth + 1)}
      </div>
    ))

  return <div>{render(null, 0)}</div>
}
