import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { fetchCollectionHealth } from '../../api/client'

function relTime(ts: string | null): string {
  if (!ts) return 'never'
  const secs = Math.max(0, Math.round((Date.now() - new Date(ts).getTime()) / 1000))
  if (secs < 60) return 'just now'
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

function Stat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-4 py-3">
      <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">{label}</p>
      <p className={clsx('text-2xl font-bold', color ?? 'text-gray-800 dark:text-gray-100')}>{value}</p>
    </div>
  )
}

export default function CollectionHealthPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['collection-health'],
    queryFn: fetchCollectionHealth,
    refetchInterval: 60_000,
  })
  if (isLoading || !data) return null

  const w = data.last_24h
  const rate = w.success_rate
  const rateColor = rate == null ? 'text-gray-500' : rate >= 95 ? 'text-green-600' : rate >= 80 ? 'text-amber-600' : 'text-red-600'

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Collection Health (last 24h)</h3>
        <p className="text-xs text-gray-400 dark:text-gray-500">Every scheduled/manual collection attempt across the fleet.</p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Attempts" value={w.total} />
        <Stat label="Succeeded" value={w.success} color="text-green-600" />
        <Stat label="Failed" value={w.failed} color={w.failed > 0 ? 'text-red-600' : 'text-gray-800 dark:text-gray-100'} />
        <Stat label="Success rate" value={rate == null ? '—' : `${rate}%`} color={rateColor} />
      </div>

      {data.devices_never_collected > 0 && (
        <div className="text-xs text-amber-700 dark:text-amber-400">
          ⚠️ {data.devices_never_collected} active device{data.devices_never_collected === 1 ? '' : 's'} never collected.
        </div>
      )}

      {data.devices_failing.length > 0 && (
        <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
          <div className="px-4 py-2 border-b border-gray-200 dark:border-gray-700 text-sm font-medium text-gray-700 dark:text-gray-200">
            Devices with collection issues
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                  <th className="px-4 py-2 font-medium">Device</th>
                  <th className="px-4 py-2 font-medium">Error</th>
                  <th className="px-4 py-2 font-medium">Consecutive fails</th>
                  <th className="px-4 py-2 font-medium">Last OK</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {data.devices_failing.map((d) => (
                  <tr key={d.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-4 py-2">
                      <Link to={`/devices/${d.id}`} className="text-blue-600 hover:text-blue-800 dark:text-blue-400">{d.hostname}</Link>
                    </td>
                    <td className="px-4 py-2 text-red-600 dark:text-red-400 capitalize">{d.last_error.replace('_', ' ')}</td>
                    <td className="px-4 py-2 text-gray-700 dark:text-gray-300">{d.consecutive_failures}</td>
                    <td className="px-4 py-2 text-gray-500 dark:text-gray-400">{relTime(d.last_success)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
