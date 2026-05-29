import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchLifecycleMilestones, type DeviceDetail, type LifecycleMilestone, type MilestoneType } from '../../api/client'
import EmptyState from '../../components/EmptyState'

const TYPE_LABEL: Record<MilestoneType, string> = {
  eos: 'End of Sale',
  eosm: 'End of Software Maintenance',
  eoss: 'End of Security Support',
  eol: 'End of Life',
}

// Hardware vs software grouping for the headline cards.
const HARDWARE: MilestoneType[] = ['eos', 'eol']
const SOFTWARE: MilestoneType[] = ['eosm', 'eoss']

function daysUntil(dateStr: string): number {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((new Date(dateStr).getTime() - today.getTime()) / 86_400_000)
}

function urgency(days: number): { cls: string; text: string } {
  if (days < 0) return { cls: 'bg-red-100 text-red-700', text: `${Math.abs(days)}d ago` }
  if (days < 180) return { cls: 'bg-orange-100 text-orange-700', text: `${days}d left` }
  if (days < 365) return { cls: 'bg-yellow-100 text-yellow-700', text: `${days}d left` }
  return { cls: 'bg-green-100 text-green-700', text: `${days}d left` }
}

function recommendation(milestones: LifecycleMilestone[]): string[] {
  const recs: string[] = []
  const byType = Object.fromEntries(milestones.map((m) => [m.milestone_type, m])) as Record<MilestoneType, LifecycleMilestone>
  const eoss = byType.eoss && daysUntil(byType.eoss.milestone_date)
  const eol = byType.eol && daysUntil(byType.eol.milestone_date)
  if (eoss != null && eoss < 0) recs.push('Security support has ended — prioritize replacement; no further security patches will ship.')
  else if (eoss != null && eoss < 365) recs.push('Security support ends within a year — budget a hardware refresh now.')
  if (eol != null && eol < 0) recs.push('Device is past End of Life — decommission and replace.')
  else if (eol != null && eol < 180) recs.push('End of Life approaching — order replacement hardware.')
  if (recs.length === 0) recs.push('No imminent lifecycle risk. Continue normal patching cadence.')
  return recs
}

export default function Lifecycle({ device }: { device: DeviceDetail }) {
  const [milestones, setMilestones] = useState<LifecycleMilestone[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetchLifecycleMilestones(device.id)
      .then((m) => { setMilestones(m); setError(null) })
      .catch(() => setError('Failed to load lifecycle milestones.'))
      .finally(() => setLoading(false))
  }, [device.id])

  if (loading) return <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  if (error) return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
  if (milestones.length === 0) {
    return <div className="bg-white rounded-lg border border-gray-200"><EmptyState title="No lifecycle data" description="No EOL/EOS milestones are recorded for this device's model yet." icon="📅" /></div>
  }

  const eosl = milestones.find((m) => m.milestone_type === 'eoss') ?? milestones.find((m) => m.milestone_type === 'eol')

  return (
    <div className="space-y-4">
      {/* Headline */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Group title="Hardware" milestones={milestones.filter((m) => HARDWARE.includes(m.milestone_type))} />
        <Group title="Software" milestones={milestones.filter((m) => SOFTWARE.includes(m.milestone_type))} />
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <p className="text-xs text-gray-400">Days until end of security support</p>
          {eosl ? (
            <>
              <p className={clsx('text-3xl font-bold mt-1', daysUntil(eosl.milestone_date) < 0 ? 'text-red-600' : daysUntil(eosl.milestone_date) < 365 ? 'text-orange-600' : 'text-green-600')}>
                {daysUntil(eosl.milestone_date) < 0 ? 'Passed' : daysUntil(eosl.milestone_date)}
              </p>
              <p className="text-xs text-gray-400 mt-1">{TYPE_LABEL[eosl.milestone_type]} · {eosl.milestone_date}</p>
            </>
          ) : <p className="text-gray-400 mt-2">—</p>}
        </div>
      </div>

      {/* All milestones */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-200"><h3 className="text-sm font-semibold text-gray-800">Milestones</h3></div>
        <div className="divide-y divide-gray-100">
          {milestones.map((m) => {
            const days = daysUntil(m.milestone_date)
            const u = urgency(days)
            return (
              <div key={m.id} className="flex items-center gap-4 px-5 py-3">
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-gray-800">{TYPE_LABEL[m.milestone_type]}</p>
                  <p className="text-xs text-gray-500">{m.milestone_date}{m.source && ` · ${m.source}`}</p>
                </div>
                <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', u.cls)}>{u.text}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Recommended actions */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
        <h3 className="text-sm font-semibold text-gray-800 mb-2">Recommended Actions</h3>
        <ul className="space-y-1.5 text-sm text-gray-700 list-disc list-inside">
          {recommendation(milestones).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      </div>
    </div>
  )
}

function Group({ title, milestones }: { title: string; milestones: LifecycleMilestone[] }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <p className="text-xs text-gray-400 mb-2">{title} lifecycle</p>
      {milestones.length === 0 ? <p className="text-sm text-gray-400">No data</p> : (
        <ul className="space-y-1">
          {milestones.map((m) => {
            const u = urgency(daysUntil(m.milestone_date))
            return (
              <li key={m.id} className="flex items-center justify-between text-sm">
                <span className="text-gray-600">{TYPE_LABEL[m.milestone_type]}</span>
                <span className={clsx('text-xs font-medium px-1.5 py-0.5 rounded', u.cls)}>{m.milestone_date}</span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
