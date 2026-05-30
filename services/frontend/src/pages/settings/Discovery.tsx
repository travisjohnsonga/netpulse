import { useState } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import { SectionHeader } from '../Settings'

// Discovery jobs/subnets management. The DiscoveryJob model exists in the
// devices app but isn't exposed via the API yet, so this screen is local and
// illustrative of the intended workflow (see CLAUDE.md → Device Discovery).

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

interface Job { id: number; name: string; method: string; status: string; found: number }

const STATUS_BADGE: Record<string, string> = {
  running: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  completed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  pending: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
}

export default function Discovery() {
  const [allowed, setAllowed] = useState<string[]>(['10.0.0.0/8'])
  const [excluded, setExcluded] = useState<string[]>(['10.99.0.0/16'])
  const [jobs, setJobs] = useState<Job[]>([
    { id: 1, name: 'DC-1 topology walk', method: 'Topology', status: 'completed', found: 42 },
  ])
  const [adding, setAdding] = useState(false)

  return (
    <div>
      <SectionHeader
        title="Discovery"
        description="Discovery jobs, allowed subnets and OT/ICS exclusions."
        action={<button onClick={() => setAdding(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ New Job</button>}
      />

      {/* Jobs */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden divide-y divide-gray-100 dark:divide-gray-700 mb-6">
        {jobs.map((j) => (
          <div key={j.id} className="flex items-center gap-4 px-5 py-3">
            <div className="flex-1 min-w-0">
              <p className="font-medium text-gray-800 dark:text-gray-100">{j.name}</p>
              <p className="text-xs text-gray-500 dark:text-gray-400">{j.method} · {j.found} devices found</p>
            </div>
            <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full capitalize', STATUS_BADGE[j.status])}>{j.status}</span>
          </div>
        ))}
      </div>

      {/* Subnet lists */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SubnetCard
          title="Allowed subnets"
          hint="Discovery never probes outside these ranges."
          subnets={allowed}
          onAdd={(s) => setAllowed((x) => [...x, s])}
          onRemove={(s) => setAllowed((x) => x.filter((v) => v !== s))}
        />
        <SubnetCard
          title="Excluded subnets"
          hint="⚠ Exclude OT/ICS/SCADA networks — probing controllers can cause physical damage."
          danger
          subnets={excluded}
          onAdd={(s) => setExcluded((x) => [...x, s])}
          onRemove={(s) => setExcluded((x) => x.filter((v) => v !== s))}
        />
      </div>

      {adding && <NewJobModal onClose={() => setAdding(false)} onCreate={(j) => { setJobs((js) => [...js, { ...j, id: Date.now() }]); setAdding(false) }} />}
    </div>
  )
}

function SubnetCard({ title, hint, subnets, onAdd, onRemove, danger }: {
  title: string; hint: string; subnets: string[]
  onAdd: (s: string) => void; onRemove: (s: string) => void; danger?: boolean
}) {
  const [val, setVal] = useState('')
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
      <p className="font-medium text-gray-800 dark:text-gray-100">{title}</p>
      <p className={clsx('text-xs mt-0.5 mb-3', danger ? 'text-red-600 dark:text-red-400' : 'text-gray-400 dark:text-gray-500')}>{hint}</p>
      <div className="flex gap-2 mb-3">
        <input className={inputCls} value={val} onChange={(e) => setVal(e.target.value)} placeholder="10.0.0.0/8" />
        <button onClick={() => { if (val.trim()) { onAdd(val.trim()); setVal('') } }} className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300 shrink-0">Add</button>
      </div>
      <div className="flex flex-wrap gap-2">
        {subnets.map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5 bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300 text-xs font-mono px-2 py-1 rounded-md">
            {s}
            <button onClick={() => onRemove(s)} className="text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">×</button>
          </span>
        ))}
        {subnets.length === 0 && <span className="text-xs text-gray-400 dark:text-gray-500">None</span>}
      </div>
    </div>
  )
}

function NewJobModal({ onClose, onCreate }: { onClose: () => void; onCreate: (j: Omit<Job, 'id'>) => void }) {
  const [name, setName] = useState('')
  const [method, setMethod] = useState('Active Scan')
  return (
    <Modal
      title="New Discovery Job"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={() => name && onCreate({ name, method, status: 'pending', found: 0 })} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Create</button>
        </>
      }
    >
      <div className="space-y-3">
        <div><label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Method</label>
          <select className={inputCls} value={method} onChange={(e) => setMethod(e.target.value)}>
            {['Passive', 'Topology', 'Active Scan', 'Import'].map((m) => <option key={m}>{m}</option>)}
          </select>
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500">Discovered devices land in PENDING and require admin approval before becoming active.</p>
      </div>
    </Modal>
  )
}
