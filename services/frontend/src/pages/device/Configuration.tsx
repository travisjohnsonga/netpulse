import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { api, type DeviceDetail } from '../../api/client'

interface DeviceConfigRow {
  id: number
  config_type: string
  collected_at: string
  collected_by: string
  content: string
  content_hash: string
  changed_from_previous: boolean
  diff_summary: string | null
}

interface CollectionLogRow {
  id: number
  collected_at: string
  status: string
  status_label: string
  collected_by: string
  duration_ms: number | null
  error_message: string
  config_changed: boolean | null
  method: string
}

// status → { emoji, label colour } for the collection-history table.
const COLLECTION_STATUS_STYLE: Record<string, { icon: string; cls: string }> = {
  success: { icon: '✅', cls: 'text-green-600 dark:text-green-400' },
  unchanged: { icon: '✅', cls: 'text-green-600 dark:text-green-400' },
  failed: { icon: '⛔', cls: 'text-red-600 dark:text-red-400' },
  timeout: { icon: '⏱', cls: 'text-amber-600 dark:text-amber-400' },
  auth_failed: { icon: '🔑', cls: 'text-red-600 dark:text-red-400' },
  empty: { icon: '∅', cls: 'text-gray-500 dark:text-gray-400' },
}

function CollectionHistory({ deviceId }: { deviceId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ['device-collection-log', deviceId],
    queryFn: () =>
      api.get(`/devices/${deviceId}/collection-log/?limit=25`).then((r) => r.data as CollectionLogRow[]),
  })
  const rows = data ?? []
  if (isLoading || rows.length === 0) return null

  return (
    <div className="mt-4 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Collection History</h3>
        <p className="text-xs text-gray-400 dark:text-gray-500">Every collection attempt — proves the device was reached even when the config didn’t change.</p>
      </div>
      <div className="overflow-x-auto max-h-80">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
              <th className="px-4 py-2 font-medium">Time</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Changed</th>
              <th className="px-4 py-2 font-medium">Method</th>
              <th className="px-4 py-2 font-medium">Duration</th>
              <th className="px-4 py-2 font-medium">By</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {rows.map((r) => {
              const style = COLLECTION_STATUS_STYLE[r.status] ?? { icon: '•', cls: 'text-gray-500' }
              return (
                <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-4 py-2 text-gray-700 dark:text-gray-300 whitespace-nowrap" title={new Date(r.collected_at).toLocaleString()}>{relativeTime(r.collected_at)}</td>
                  <td className={clsx('px-4 py-2 font-medium whitespace-nowrap', style.cls)} title={r.error_message || undefined}>
                    {style.icon} {r.status_label}
                  </td>
                  <td className="px-4 py-2 text-gray-600 dark:text-gray-400">{r.config_changed === null ? '—' : r.config_changed ? 'Yes' : 'No'}</td>
                  <td className="px-4 py-2 text-gray-500 dark:text-gray-400 uppercase text-xs">{r.method || '—'}</td>
                  <td className="px-4 py-2 text-gray-500 dark:text-gray-400">{r.duration_ms == null ? '—' : `${r.duration_ms} ms`}</td>
                  <td className="px-4 py-2 text-gray-500 dark:text-gray-400">{r.collected_by}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// UTC hours at which config-manager collects (mirrors the backend default
// CONFIG_COLLECTION_HOUR_1 / _2). Used to show the schedule + next-run estimate.
const COLLECTION_HOURS_UTC = [7, 19]

function nextCollectionUTC(): Date {
  const now = new Date()
  for (let day = 0; day <= 1; day++) {
    for (const h of COLLECTION_HOURS_UTC) {
      const d = new Date(Date.UTC(
        now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + day, h, 0, 0))
      if (d.getTime() > now.getTime()) return d
    }
  }
  return now
}

function untilNext(): string {
  const mins = Math.max(0, Math.round((nextCollectionUTC().getTime() - Date.now()) / 60000))
  if (mins < 60) return `in ${mins}m`
  const hrs = Math.floor(mins / 60)
  return `in ${hrs}h ${mins % 60}m`
}

function ScheduleBanner({ lastCollectedAt }: { lastCollectedAt?: string }) {
  const hours = COLLECTION_HOURS_UTC.map((h) => `${String(h).padStart(2, '0')}:00`).join(' and ')
  return (
    <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-2 mb-4 text-xs text-blue-800 dark:text-blue-300 flex flex-wrap gap-x-4 gap-y-1">
      <span>🕑 Auto-collected at {hours} UTC daily</span>
      {lastCollectedAt && <span>· Last collected: {relativeTime(lastCollectedAt)}</span>}
      <span>· Next collection: {untilNext()}</span>
    </div>
  )
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (secs < 60) return 'just now'
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return `${days}d ago`
}

export default function Configuration({ device }: { device: DeviceDetail }) {
  const deviceId = device.id
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: configsData, isLoading } = useQuery({
    queryKey: ['device-configs', deviceId],
    queryFn: () =>
      api
        .get(`/configbackup/configs/?device=${deviceId}&ordering=-collected_at`)
        .then((r) => (Array.isArray(r.data) ? r.data : r.data.results ?? []) as DeviceConfigRow[]),
  })
  const configs = configsData ?? []

  const [selectedId, setSelectedId] = useState<number | null>(null)
  useEffect(() => {
    if (configs.length && !selectedId) setSelectedId(configs[0].id)
  }, [configs, selectedId])

  const selected = configs.find((c) => c.id === selectedId)
  const [collecting, setCollecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const collectNow = async () => {
    setCollecting(true)
    setError(null)
    try {
      await api.post(`/configbackup/configs/collect/${deviceId}/`)
      setSelectedId(null) // let the latest snapshot become selected after refetch
      await queryClient.invalidateQueries({ queryKey: ['device-configs', deviceId] })
    } catch {
      setError('Collection failed. Check that the device is reachable and has a credential profile.')
    } finally {
      setCollecting(false)
    }
  }

  const download = () => {
    if (!selected) return
    const blob = new Blob([selected.content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${device.hostname}-running.cfg`
    a.click()
    URL.revokeObjectURL(url)
  }

  // The snapshot immediately older than the selected one (configs are sorted
  // newest-first), so "Compare" can pre-populate current vs previous.
  const selectedIdx = configs.findIndex((c) => c.id === (selectedId ?? configs[0]?.id))
  const previous = selectedIdx >= 0 ? configs[selectedIdx + 1] : undefined

  // Pre-populate Configuration Compare with this device's current + previous
  // snapshot (left = current, right = previous); the compare page auto-runs the
  // diff. Falls back to just the current version when there's no prior snapshot.
  const compare = () => {
    const curId = selectedId ?? configs[0]?.id
    const qs = new URLSearchParams({ device: String(deviceId) })
    if (curId) { qs.set('left', String(deviceId)); qs.set('leftVersion', String(curId)) }
    if (previous) { qs.set('right', String(deviceId)); qs.set('rightVersion', String(previous.id)) }
    navigate(`/configs/compare?${qs.toString()}`)
  }

  if (isLoading) {
    return <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  }

  if (configs.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 py-16 text-center">
        <div className="text-4xl mb-2">📄</div>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">No configurations collected yet</p>
        {error && <p className="text-xs text-red-600 mb-3">{error}</p>}
        <button onClick={collectNow} disabled={collecting} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
          {collecting ? 'Collecting…' : 'Collect Now'}
        </button>
        <CollectionHistory deviceId={deviceId} />
      </div>
    )
  }

  return (
    <div>
      <ScheduleBanner lastCollectedAt={configs[0]?.collected_at} />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Version history */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Version History</h3>
          <button onClick={collectNow} disabled={collecting} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50">
            {collecting ? '…' : 'Collect Now'}
          </button>
        </div>
        {error && <p className="text-xs text-red-600 px-4 py-2">{error}</p>}
        <div className="divide-y divide-gray-100 dark:divide-gray-700 max-h-[28rem] overflow-y-auto">
          {configs.map((c, i) => {
            const label = i === 0 ? `v${configs.length} (current)` : `v${configs.length - i}`
            return (
              <button
                key={c.id}
                onClick={() => setSelectedId(c.id)}
                className={clsx('w-full text-left px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50', selectedId === c.id && 'bg-blue-50 dark:bg-blue-900/20')}
              >
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-100">{label}</p>
                  {c.changed_from_previous && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">changed</span>}
                </div>
                <p className="text-xs text-gray-400 dark:text-gray-500">{c.collected_by} · {relativeTime(c.collected_at)}</p>
              </button>
            )
          })}
        </div>
      </div>

      {/* Config viewer */}
      <div className="lg:col-span-2 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Running Config</h3>
          <div className="flex gap-2">
            <button
              onClick={compare}
              title={previous ? 'Compare this version with the previous snapshot' : 'Open Configuration Compare for this device'}
              className="px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50"
            >{previous ? 'Compare with previous' : 'Compare'}</button>
            <button onClick={download} disabled={!selected} className="px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50">Download</button>
          </div>
        </div>
        <pre className="bg-gray-900 text-gray-100 text-xs font-mono p-4 overflow-x-auto leading-relaxed max-h-[28rem]">
          {(selected?.content ?? '').split('\n').map((line, i) => (
            <div key={i} className={clsx(
              line.startsWith('!') && 'text-gray-500',
              /^(hostname|interface|line|snmp-server|logging|service|ip|no)\b/.test(line) && 'text-sky-300',
              line.trim().startsWith('ip address') && 'text-emerald-300',
            )}>{line || ' '}</div>
          ))}
        </pre>
      </div>
      </div>
      <CollectionHistory deviceId={deviceId} />
    </div>
  )
}
