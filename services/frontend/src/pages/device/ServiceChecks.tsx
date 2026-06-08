import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { fetchChecks, type DeviceDetail, type ServiceCheck, type CheckStatus } from '../../api/client'
import { CollectorBadges } from '../Checks'
import EmptyState from '../../components/EmptyState'

const STATUS_BADGE: Record<CheckStatus, string> = {
  up: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  down: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  degraded: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  unknown: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
}

export default function ServiceChecks({ device }: { device: DeviceDetail }) {
  const [checks, setChecks] = useState<ServiceCheck[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetchChecks({ device: String(device.id) })
      .then(setChecks)
      .catch(() => setChecks([]))
      .finally(() => setLoading(false))
  }, [device.id])

  if (loading) return <div className="p-8 text-center text-sm text-gray-400">Loading…</div>

  if (checks.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <EmptyState
          icon="✓"
          title="No service checks"
          description="No service checks are associated with this device. Create one under Checks."
        />
      </div>
    )
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
            <th className="px-5 py-3 font-medium">Check</th>
            <th className="px-5 py-3 font-medium">Collectors</th>
            <th className="px-5 py-3 font-medium">Overall</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
          {checks.map((c) => (
            <tr key={c.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
              <td className="px-5 py-3">
                <Link to="/checks" className="font-medium text-gray-900 dark:text-gray-100 hover:text-blue-600">
                  {c.name}
                </Link>
                <span className="ml-2 text-xs text-gray-400 uppercase">{c.check_type}</span>
              </td>
              <td className="px-5 py-3"><CollectorBadges results={c.collector_results} /></td>
              <td className="px-5 py-3">
                <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_BADGE[c.current_status])}>
                  {c.current_status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
