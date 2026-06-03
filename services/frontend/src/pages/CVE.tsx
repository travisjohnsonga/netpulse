import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import StatCard from '../components/StatCard'
import {
  fetchCVEs, fetchCVESummary, triggerCVESync,
  type CVECatalogEntry, type CVESummary,
} from '../api/client'

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  high: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  none: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
}
const SEVERITIES = ['All', 'critical', 'high', 'medium', 'low']

function relTime(iso: string | null): string {
  if (!iso) return 'never'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export default function CVE() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState<CVESummary | null>(null)
  const [rows, setRows] = useState<CVECatalogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [severity, setSeverity] = useState('All')
  const [search, setSearch] = useState('')
  const [platform, setPlatform] = useState('All')
  const [showAll, setShowAll] = useState(false) // false = inventory platforms only
  const [syncing, setSyncing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, list] = await Promise.all([
        fetchCVESummary(!showAll),
        fetchCVEs({
          severity: severity === 'All' ? undefined : severity,
          search: search || undefined,
          platform: platform === 'All' ? undefined : platform,
          inventory_only: !showAll,
        }),
      ])
      setSummary(s)
      setRows(list)
    } finally {
      setLoading(false)
    }
  }, [severity, search, platform, showAll])

  useEffect(() => { void load() }, [load])

  const onSync = async () => {
    setSyncing(true)
    setNotice(null)
    try {
      await triggerCVESync()
      setNotice('Sync started — pulling CVEs from NVD and correlating to devices. This runs in the background; refresh in a few minutes.')
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status
      setNotice(status === 409 ? 'A sync is already running.' : 'Failed to start sync.')
    } finally {
      setSyncing(false)
    }
  }

  const synced = summary?.last_synced_at ?? null
  const empty = !loading && rows.length === 0 && (summary?.total ?? 0) === 0
  const invSet = new Set(summary?.inventory_platforms ?? [])
  // When scoped to inventory, the Platforms column shows only the inventory
  // platforms this CVE affects (not every platform NVD lists for it).
  const platformsLabel = (row: CVECatalogEntry): string => {
    const shown = showAll ? row.affected_platforms : row.affected_platforms.filter((p) => invSet.has(p))
    return shown.join(', ') || '—'
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">CVE Intelligence</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Vulnerabilities correlated against your device platforms and software versions
            {synced && <> · last sync {relTime(synced)}{summary?.last_sync_status === 'running' && ' (running…)'}</>}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => navigate('/settings/data-sources')}
            className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded-lg text-sm font-medium">
            Feed Settings
          </button>
          <button onClick={onSync} disabled={syncing}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {syncing ? 'Starting…' : 'Sync Now'}
          </button>
        </div>
      </div>

      {notice && (
        <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-3 text-sm text-blue-800 dark:text-blue-300">{notice}</div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Critical CVEs" value={summary?.critical ?? 0} color="red"
          subtitle={`${summary?.kev_count ?? 0} on CISA KEV`} />
        <StatCard title="High CVEs" value={summary?.high ?? 0} color="yellow"
          subtitle={`${summary?.total ?? 0} total tracked`} />
        <StatCard title="Affected Devices" value={summary?.affected_devices ?? 0} color="red"
          subtitle="with unpatched CVEs" />
        <StatCard title="Patched" value={summary?.patched ?? 0} color="green"
          subtitle={synced ? `synced ${relTime(synced)}` : 'not synced yet'} />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search CVE ID or description…"
          className="flex-1 min-w-[14rem] px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500" />
        <select value={severity} onChange={(e) => setSeverity(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 dark:text-gray-100">
          {SEVERITIES.map((s) => <option key={s} value={s}>{s === 'All' ? 'All severities' : s}</option>)}
        </select>
        <select value={platform} onChange={(e) => setPlatform(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 dark:text-gray-100">
          <option value="All">All platforms</option>
          {(summary?.inventory_platforms ?? []).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <label className="inline-flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-400 select-none">
          <input type="checkbox" checked={showAll} onChange={(e) => { setShowAll(e.target.checked); setPlatform('All') }}
            className="rounded border-gray-300 dark:border-gray-600" />
          Show all platforms
        </label>
      </div>

      {/* Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : empty ? (
          <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
            <span className="text-5xl mb-4" role="img" aria-label="shield">🛡</span>
            <h3 className="text-lg font-semibold text-gray-700 dark:text-gray-300 mb-2">No CVE data yet</h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 max-w-sm mb-6">
              Click <span className="font-medium">Sync Now</span> to pull CVEs from NVD for the platforms in your inventory
              and correlate them to device software versions.
            </p>
            <button onClick={onSync} disabled={syncing}
              className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
              {syncing ? 'Starting…' : 'Run First Sync'}
            </button>
          </div>
        ) : rows.length === 0 ? (
          <div className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">No CVEs match this filter.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">CVE ID</th>
                  <th className="px-5 py-3 font-medium">CVSS</th>
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium">Platforms</th>
                  <th className="px-5 py-3 font-medium">Affects</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {rows.map((row) => (
                  <tr key={row.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-5 py-3">
                      <a href={row.source_url || `https://nvd.nist.gov/vuln/detail/${row.cve_id}`} target="_blank" rel="noreferrer"
                        className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline">{row.cve_id}</a>
                      {row.cisa_kev && <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-red-600 text-white">KEV</span>}
                      <div className="text-xs text-gray-500 dark:text-gray-400 max-w-md truncate">{row.description}</div>
                    </td>
                    <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">{row.cvss_score ?? '—'}</td>
                    <td className="px-5 py-3">
                      <span className={clsx('text-xs font-medium px-2 py-1 rounded-md capitalize', SEVERITY_BADGE[row.severity])}>{row.severity}</span>
                    </td>
                    <td className="px-5 py-3 text-xs text-gray-600 dark:text-gray-400">{platformsLabel(row)}</td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">
                      {row.affected_device_count} {row.affected_device_count === 1 ? 'device' : 'devices'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
