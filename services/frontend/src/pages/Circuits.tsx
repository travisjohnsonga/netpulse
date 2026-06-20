import { useEffect, useMemo, useState } from 'react'
import {
  fetchCircuits, deleteCircuit, type WanCircuit, type CircuitType,
} from '../api/client'
import { useSite } from '../store/siteStore'
import CircuitCard from '../components/CircuitCard'
import CircuitModal from '../components/CircuitModal'
import EmptyState from '../components/EmptyState'

export default function Circuits() {
  const { selectedSite } = useSite()
  const [circuits, setCircuits] = useState<WanCircuit[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<CircuitType | 'all'>('all')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<WanCircuit | null>(null)
  const [deleting, setDeleting] = useState<WanCircuit | null>(null)

  const load = () => {
    setLoading(true)
    const params: Record<string, string> = {}
    if (selectedSite) params.site = String(selectedSite)
    fetchCircuits(params)
      .then(setCircuits)
      .catch(() => setError('Failed to load circuits.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [selectedSite])

  const types = useMemo(() => Array.from(new Set(circuits.map((c) => c.circuit_type))), [circuits])
  const filtered = typeFilter === 'all' ? circuits : circuits.filter((c) => c.circuit_type === typeFilter)
  const monthly = circuits.reduce((s, c) => s + (c.monthly_cost ? Number(c.monthly_cost) : 0), 0)

  const confirmDelete = async () => {
    if (!deleting) return
    try { await deleteCircuit(deleting.id); setDeleting(null); load() }
    catch { setError('Could not delete the circuit.') }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">WAN Circuits</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            {circuits.length} circuit{circuits.length !== 1 ? 's' : ''}{monthly > 0 ? ` · $${monthly.toLocaleString()}/mo` : ''}
          </p>
        </div>
        <button onClick={() => setAdding(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ Add Circuit</button>
      </div>

      {error && <div className="bg-yellow-50 border border-yellow-200 dark:bg-yellow-900/30 dark:border-yellow-800 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:text-yellow-300">{error}</div>}

      {types.length > 1 && (
        <div className="flex gap-1 flex-wrap">
          <button onClick={() => setTypeFilter('all')} className={`px-3 py-1 text-sm rounded-md border ${typeFilter === 'all' ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950' : 'border-gray-200 dark:border-gray-700 text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800'}`}>All</button>
          {types.map((t) => (
            <button key={t} onClick={() => setTypeFilter(t)} className={`px-3 py-1 text-sm rounded-md border capitalize ${typeFilter === t ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950' : 'border-gray-200 dark:border-gray-700 text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800'}`}>{t}</button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
      ) : filtered.length === 0 ? (
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
          <EmptyState title="No WAN circuits" icon="🔌"
            description="Track your Internet/MPLS/LTE circuits — provider, bandwidth, IP assignments, cost, and live utilization."
            action={{ label: 'Add Circuit', onClick: () => setAdding(true) }} />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filtered.map((c) => (
            <CircuitCard key={c.id} circuit={c} onEdit={setEditing} onDelete={setDeleting} />
          ))}
        </div>
      )}

      {adding && <CircuitModal onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load() }} prefillSite={selectedSite ? Number(selectedSite) : undefined} />}
      {editing && <CircuitModal edit={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />}

      {deleting && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setDeleting(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Delete circuit?</h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">{deleting.name} ({deleting.provider}). This can't be undone.</p>
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
