import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { fetchWirelessSummary, type WirelessSummary, type UnifiApStatus, type UnifiRadio } from '../api/client'
import StatCard from '../components/StatCard'
import EmptyState from '../components/EmptyState'
import { useSite } from '../store/siteStore'

function fmtUptime(secs: number | null | undefined): string {
  if (!secs || secs <= 0) return '—'
  const d = Math.floor(secs / 86400)
  const h = Math.floor((secs % 86400) / 3600)
  if (d > 0) return `${d}d ${h}h`
  const m = Math.floor((secs % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function scoreColor(score: number | null | undefined): string {
  if (score == null) return 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-300'
  if (score >= 90) return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
  if (score >= 70) return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
  return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
}

function radioFor(ap: UnifiApStatus, band: string): UnifiRadio | undefined {
  return ap.radios.find((r) => r.band === band)
}

function RadioCell({ radio }: { radio?: UnifiRadio }) {
  if (!radio || radio.channel == null) return <span className="text-gray-400">—</span>
  return (
    <span className="text-xs text-gray-600 dark:text-gray-300 whitespace-nowrap">
      ch{radio.channel} · {radio.channel_utilization_pct != null ? `${Math.round(radio.channel_utilization_pct)}%` : '—'} · {radio.clients}cl
    </span>
  )
}

function statusBadge(ap: UnifiApStatus): { label: string; cls: string } {
  if (ap.state !== 1) return { label: '🔴 Offline', cls: 'text-red-600 dark:text-red-400' }
  if (ap.satisfaction != null && ap.satisfaction < 70) return { label: '⚠️ Degraded', cls: 'text-yellow-600 dark:text-yellow-400' }
  return { label: '✅ Online', cls: 'text-green-600 dark:text-green-400' }
}

/** Horizontal bar for a single channel in the congestion heatmap. */
function ChannelBar({ ch, util, apCount }: { ch: string; util: number; apCount: number }) {
  const filled = Math.round(util / 10)
  const bar = '█'.repeat(Math.max(0, Math.min(10, filled))) + '░'.repeat(Math.max(0, 10 - filled))
  const color = util >= 60 ? 'text-red-500' : util >= 30 ? 'text-yellow-500' : 'text-green-500'
  return (
    <div className="flex items-center gap-3 text-xs font-mono">
      <span className="w-12 text-gray-600 dark:text-gray-300">ch{ch}</span>
      <span className={clsx('tracking-tighter', color)}>{bar}</span>
      <span className="w-10 text-right text-gray-700 dark:text-gray-200">{util}%</span>
      <span className="text-gray-400">({apCount} AP{apCount === 1 ? '' : 's'})</span>
    </div>
  )
}

export default function Wireless() {
  const navigate = useNavigate()
  const [data, setData] = useState<WirelessSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [scoreFilter, setScoreFilter] = useState('')
  // Site scoping comes from the global header selector (APs carry a site_name,
  // so match on the selected site's display name).
  const { selectedSite, selectedSiteName } = useSite()

  useEffect(() => {
    let cancelled = false
    const load = () => fetchWirelessSummary()
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setData(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  const aps = useMemo(() => {
    const list = data?.aps ?? []
    const q = search.trim().toLowerCase()
    return list.filter((a) => {
      if (q && !a.hostname.toLowerCase().includes(q) && !(a.model || '').toLowerCase().includes(q)) return false
      if (selectedSite && a.site_name !== selectedSiteName) return false
      if (statusFilter === 'online' && a.state !== 1) return false
      if (statusFilter === 'offline' && a.state === 1) return false
      if (statusFilter === 'degraded' && !(a.state === 1 && a.satisfaction != null && a.satisfaction < 70)) return false
      if (scoreFilter === 'green' && !(a.satisfaction != null && a.satisfaction >= 90)) return false
      if (scoreFilter === 'yellow' && !(a.satisfaction != null && a.satisfaction >= 70 && a.satisfaction < 90)) return false
      if (scoreFilter === 'red' && !(a.satisfaction != null && a.satisfaction < 70)) return false
      return true
    })
  }, [data, search, selectedSite, selectedSiteName, statusFilter, scoreFilter])

  if (loading) {
    return <div className="py-20 flex justify-center"><div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  }

  if (!data || data.total_aps === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <EmptyState
          icon="📶"
          title="No wireless controllers configured"
          description="Connect a UniFi controller to import access points and see live wireless telemetry here."
          action={{ label: 'Go to Settings → Integrations', onClick: () => navigate('/settings/integrations') }}
        />
      </div>
    )
  }

  const selectCls = 'px-2.5 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200'

  // Summary cards: use the server-wide totals normally; when a site is active,
  // recompute from that site's APs so the cards match the filtered table.
  const cards = (() => {
    if (!selectedSite) {
      return {
        total: data.total_aps, online: data.online, offline: data.offline,
        clients: data.total_clients, avg: data.avg_satisfaction,
      }
    }
    const siteAps = (data.aps ?? []).filter((a) => a.site_name === selectedSiteName)
    const online = siteAps.filter((a) => a.state === 1).length
    const sats = siteAps.map((a) => a.satisfaction).filter((s): s is number => s != null)
    return {
      total: siteAps.length, online, offline: siteAps.length - online,
      clients: siteAps.reduce((sum, a) => sum + (a.client_count ?? 0), 0),
      avg: sats.length ? Math.round(sats.reduce((a, b) => a + b, 0) / sats.length) : null,
    }
  })()

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold text-gray-800 dark:text-gray-100">Wireless</h1>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Total APs" value={cards.total} />
        <StatCard title="Online / Offline" value={`${cards.online} / ${cards.offline}`}
          color={cards.offline > 0 ? 'yellow' : 'green'} />
        <StatCard title="Clients" value={cards.clients} color="blue" />
        <StatCard title="Avg Score" value={cards.avg ?? '—'}
          color={cards.avg == null ? 'blue' : cards.avg >= 90 ? 'green' : cards.avg >= 70 ? 'yellow' : 'red'} />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name / model…"
          className={clsx(selectCls, 'flex-1 min-w-[180px]')} />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={selectCls}>
          <option value="">All Status</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
          <option value="degraded">Degraded</option>
        </select>
        <select value={scoreFilter} onChange={(e) => setScoreFilter(e.target.value)} className={selectCls}>
          <option value="">All Scores</option>
          <option value="green">≥ 90</option>
          <option value="yellow">70–89</option>
          <option value="red">&lt; 70</option>
        </select>
      </div>

      {/* AP table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Site</th>
                <th className="px-4 py-2 font-medium">Model</th>
                <th className="px-4 py-2 font-medium">Clients</th>
                <th className="px-4 py-2 font-medium">2.4 GHz</th>
                <th className="px-4 py-2 font-medium">5 GHz</th>
                <th className="px-4 py-2 font-medium">Score</th>
                <th className="px-4 py-2 font-medium">Uptime</th>
                <th className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {aps.length === 0 ? (
                <tr><td colSpan={9} className="px-4 py-6 text-center text-gray-400">No APs match the filters</td></tr>
              ) : aps.map((ap) => {
                const st = statusBadge(ap)
                return (
                  <tr key={ap.device_id} onClick={() => navigate(`/devices/${ap.device_id}?tab=wireless`)}
                    className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer">
                    <td className="px-4 py-2 font-medium text-gray-800 dark:text-gray-100">{ap.hostname}</td>
                    <td className="px-4 py-2 text-gray-500">{ap.site_name || '—'}</td>
                    <td className="px-4 py-2 text-gray-500">{ap.model || '—'}</td>
                    <td className="px-4 py-2">{ap.client_count}</td>
                    <td className="px-4 py-2"><RadioCell radio={radioFor(ap, '2.4GHz')} /></td>
                    <td className="px-4 py-2"><RadioCell radio={radioFor(ap, '5GHz')} /></td>
                    <td className="px-4 py-2">
                      <span className={clsx('px-2 py-0.5 rounded-full text-xs font-semibold', scoreColor(ap.satisfaction))}>
                        {ap.satisfaction ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-gray-500">{fmtUptime(ap.uptime_seconds)}</td>
                    <td className={clsx('px-4 py-2 whitespace-nowrap', st.cls)}>{st.label}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Channel utilization heatmap */}
      {Object.keys(data.channel_utilization).length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100 mb-3">Channel Utilization</h3>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-10 gap-y-4">
            {Object.entries(data.channel_utilization).sort().map(([band, channels]) => (
              <div key={band}>
                <div className="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2">{band}</div>
                <div className="space-y-1">
                  {Object.entries(channels).map(([ch, info]) => (
                    <ChannelBar key={ch} ch={ch} util={info.utilization_pct} apCount={info.ap_count} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
