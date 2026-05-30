import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchComplianceResults, type DeviceDetail, type ComplianceResult } from '../../api/client'
import Gauge from '../../components/Gauge'
import EmptyState from '../../components/EmptyState'

const OUTCOME_BADGE: Record<string, string> = {
  pass: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  fail: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  error: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
}

// Classify a failing result's detail text into a drift category.
function driftKind(detail: string): 'MISSING' | 'EXTRA' | 'DRIFT' {
  const d = detail.toLowerCase()
  if (d.includes('missing')) return 'MISSING'
  if (d.includes('extra') || d.includes('unexpected')) return 'EXTRA'
  return 'DRIFT'
}

const DRIFT_BADGE: Record<string, string> = {
  MISSING: 'bg-red-50 text-red-600 border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800',
  EXTRA: 'bg-orange-50 text-orange-600 border-orange-200 dark:bg-orange-900/20 dark:text-orange-400 dark:border-orange-800',
  DRIFT: 'bg-yellow-50 text-yellow-700 border-yellow-200 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800',
}

export default function Compliance({ device }: { device: DeviceDetail }) {
  const [results, setResults] = useState<ComplianceResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetchComplianceResults(device.id)
      .then((r) => { setResults(r); setError(null) })
      .catch(() => setError('Failed to load compliance results.'))
      .finally(() => setLoading(false))
  }, [device.id])

  if (loading) return <Spinner />
  if (error) return <Banner text={error} />
  if (results.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <EmptyState title="No compliance checks" description="This device hasn't been evaluated against any compliance policy yet." icon="✅" />
      </div>
    )
  }

  const total = results.length
  const passed = results.filter((r) => r.outcome === 'pass').length
  const score = Math.round((passed / total) * 100)
  const fails = results.filter((r) => r.outcome !== 'pass')
  const lastChecked = results.reduce((m, r) => (r.created_at > m ? r.created_at : m), results[0].created_at)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Compliance Score</h3>
        <Gauge value={score} label="% pass" />
        <p className="text-xs text-gray-400 dark:text-gray-500 text-center">{passed}/{total} rules passing</p>
        <p className="text-xs text-gray-400 dark:text-gray-500 text-center mt-1">Last checked {new Date(lastChecked).toLocaleString()}</p>
      </div>

      {/* Drift items */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 lg:col-span-2">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Drift Items <span className="font-normal text-gray-400 dark:text-gray-500">({fails.length})</span></h3>
        {fails.length === 0 ? (
          <p className="text-sm text-green-600">No drift — all rules pass. 🎉</p>
        ) : (
          <ul className="space-y-2">
            {fails.map((r) => {
              const kind = driftKind(r.detail)
              return (
                <li key={r.id} className="flex items-start gap-2 text-sm">
                  <span className={clsx('text-xs font-medium px-1.5 py-0.5 rounded border shrink-0', DRIFT_BADGE[kind])}>{kind}</span>
                  <span className="text-gray-700 dark:text-gray-300">{r.detail || `Rule #${r.rule} ${r.outcome}`}</span>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* Per-rule list */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden lg:col-span-3">
        <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-700"><h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Per-Rule Results</h3></div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Rule</th>
                <th className="px-5 py-3 font-medium">Outcome</th>
                <th className="px-5 py-3 font-medium">Detail</th>
                <th className="px-5 py-3 font-medium">Checked</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {results.map((r) => (
                <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-5 py-3 text-gray-700 dark:text-gray-300">Rule #{r.rule}</td>
                  <td className="px-5 py-3"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', OUTCOME_BADGE[r.outcome])}>{r.outcome}</span></td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{r.detail || '—'}</td>
                  <td className="px-5 py-3 text-gray-400 dark:text-gray-500 text-xs">{new Date(r.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Spinner() {
  return <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
}
function Banner({ text }: { text: string }) {
  return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-400">{text}</div>
}
