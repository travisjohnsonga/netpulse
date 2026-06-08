import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { SectionHeader } from '../Settings'
import {
  fetchAuditLog, fetchAuditStats, downloadAuditCsv,
  type AuditLogEntry, type AuditStats,
} from '../../api/client'

const inputCls =
  'px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

// Event-type prefix → category icon.
function eventIcon(eventType: string): string {
  if (eventType.startsWith('login') || eventType.startsWith('logout') || eventType.startsWith('password')) return '🔐'
  if (eventType.startsWith('device')) return '🖥️'
  if (eventType.startsWith('config') || eventType.startsWith('compliance')) return '⚙️'
  if (eventType.startsWith('credential')) return '🔑'
  if (eventType.startsWith('discovery')) return '🔍'
  if (eventType.startsWith('alert')) return '⚠️'
  if (eventType.startsWith('user')) return '👤'
  if (eventType.startsWith('settings') || eventType.startsWith('sso') || eventType.startsWith('api_key')) return '🔧'
  return '•'
}

const EVENT_GROUPS: { label: string; options: { value: string; label: string }[] }[] = [
  { label: 'Auth', options: [
    { value: 'login_success', label: 'Login Success' },
    { value: 'login_failed', label: 'Login Failed' },
    { value: 'password_changed', label: 'Password Changed' },
  ] },
  { label: 'Devices', options: [
    { value: 'device_created', label: 'Device Created' },
    { value: 'device_updated', label: 'Device Updated' },
    { value: 'device_deleted', label: 'Device Deleted' },
    { value: 'device_approved', label: 'Device Approved' },
    { value: 'device_rejected', label: 'Device Rejected' },
  ] },
  { label: 'Config', options: [
    { value: 'config_pushed', label: 'Config Pushed' },
  ] },
  { label: 'Other', options: [
    { value: 'discovery_started', label: 'Discovery Started' },
    { value: 'user_created', label: 'User Created' },
    { value: 'user_role_changed', label: 'User Role Changed' },
    { value: 'settings_changed', label: 'Settings Changed' },
  ] },
]

export default function AuditLog() {
  const [rows, setRows] = useState<AuditLogEntry[]>([])
  const [count, setCount] = useState(0)
  const [stats, setStats] = useState<AuditStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)

  const [eventType, setEventType] = useState('')
  const [success, setSuccess] = useState('')
  const [search, setSearch] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [page, setPage] = useState(1)

  const params = useCallback((): Record<string, string> => {
    const p: Record<string, string> = { page: String(page) }
    if (eventType) p.event_type = eventType
    if (success) p.success = success
    if (search.trim()) p.search = search.trim()
    if (start) p.start = new Date(start).toISOString()
    if (end) p.end = new Date(end).toISOString()
    return p
  }, [eventType, success, search, start, end, page])

  const load = useCallback(() => {
    setLoading(true)
    fetchAuditLog(params())
      .then((d) => { setRows(d.results); setCount(d.count) })
      .catch(() => { setRows([]); setCount(0) })
      .finally(() => setLoading(false))
  }, [params])

  useEffect(() => { load() }, [load])
  useEffect(() => { fetchAuditStats().then(setStats).catch(() => {}) }, [])
  // Reset to page 1 when filters change.
  useEffect(() => { setPage(1) }, [eventType, success, search, start, end])

  const totalPages = Math.max(1, Math.ceil(count / 50))

  return (
    <div>
      <SectionHeader
        title="Audit Log"
        description="Security- and operationally-significant events: who did what, from where, and whether it succeeded."
        action={
          <button onClick={() => downloadAuditCsv(params())}
            className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">
            Export CSV
          </button>
        }
      />

      {/* Stats panel */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <Stat label="Today" value={stats.today} />
          <Stat label="This Week" value={stats.this_week} />
          <Stat label="Failed Logins (24h)" value={stats.failed_logins_24h} accent={stats.failed_logins_24h > 0 ? 'red' : undefined} />
          <Stat label="Config Pushes" value={stats.by_event_type?.config_pushed ?? 0} />
        </div>
      )}

      {/* Filters */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-3 mb-4 flex flex-wrap gap-2">
        <select className={inputCls} value={eventType} onChange={(e) => setEventType(e.target.value)}>
          <option value="">All Events</option>
          {EVENT_GROUPS.map((g) => (
            <optgroup key={g.label} label={g.label}>
              {g.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </optgroup>
          ))}
        </select>
        <select className={inputCls} value={success} onChange={(e) => setSuccess(e.target.value)}>
          <option value="">Any Outcome</option>
          <option value="true">Success</option>
          <option value="false">Failed</option>
        </select>
        <input type="date" className={inputCls} value={start} onChange={(e) => setStart(e.target.value)} title="From" />
        <input type="date" className={inputCls} value={end} onChange={(e) => setEnd(e.target.value)} title="To" />
        <input type="search" className={clsx(inputCls, 'flex-1 min-w-[160px]')} placeholder="Search user, target, description…"
          value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-sm text-gray-400">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500 dark:text-gray-400">No audit events match the filters.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Time</th>
                <th className="px-5 py-3 font-medium">User</th>
                <th className="px-5 py-3 font-medium">Event</th>
                <th className="px-5 py-3 font-medium">Target</th>
                <th className="px-5 py-3 font-medium text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {rows.map((r) => (
                <Row key={r.id} r={r} expanded={expanded === r.id}
                  onToggle={() => setExpanded(expanded === r.id ? null : r.id)} />
              ))}
            </tbody>
          </table>
        )}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-gray-200 dark:border-gray-700 text-sm">
            <span className="text-gray-500">{page} of {totalPages} · {count} events</span>
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage((p) => p - 1)}
                className="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md disabled:opacity-40">Previous</button>
              <button disabled={page === totalPages} onClick={() => setPage((p) => p + 1)}
                className="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md disabled:opacity-40">Next</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, accent }: { label: string; value: number; accent?: 'red' }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <div className={clsx('text-2xl font-bold', accent === 'red' ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-gray-100')}>{value}</div>
      <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{label}</div>
    </div>
  )
}

function Row({ r, expanded, onToggle }: { r: AuditLogEntry; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer" onClick={onToggle}>
        <td className="px-5 py-3 text-gray-500 dark:text-gray-400 text-xs whitespace-nowrap">
          {new Date(r.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
        </td>
        <td className="px-5 py-3 text-gray-700 dark:text-gray-200">{r.username || <span className="text-gray-400">system</span>}</td>
        <td className="px-5 py-3 text-gray-800 dark:text-gray-100">
          <span className="mr-1.5" aria-hidden>{eventIcon(r.event_type)}</span>{r.event_label}
        </td>
        <td className="px-5 py-3 text-gray-600 dark:text-gray-300">{r.target_name || <span className="text-gray-300">—</span>}</td>
        <td className="px-5 py-3 text-center">{r.success ? '✅' : '❌'}</td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50 dark:bg-gray-900/40">
          <td colSpan={5} className="px-6 py-4">
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 text-sm">
              <Detail label="IP address">{r.ip_address || '—'}</Detail>
              <Detail label="Target">{r.target_type ? `${r.target_type} ${r.target_id}` : '—'}</Detail>
              <div className="md:col-span-2"><Detail label="Description">{r.description || '—'}</Detail></div>
              <div className="md:col-span-2"><Detail label="User agent"><span className="text-xs break-all">{r.user_agent || '—'}</span></Detail></div>
              {!r.success && r.error_message && (
                <div className="md:col-span-2"><Detail label="Error"><span className="text-red-600 dark:text-red-400">{r.error_message}</span></Detail></div>
              )}
              {r.metadata && Object.keys(r.metadata).length > 0 && (
                <div className="md:col-span-2">
                  <dt className="text-xs uppercase tracking-wide text-gray-400 mb-1">Metadata</dt>
                  <pre className="text-xs bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded p-2 overflow-x-auto">{JSON.stringify(r.metadata, null, 2)}</pre>
                </div>
              )}
            </dl>
          </td>
        </tr>
      )}
    </>
  )
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-gray-400 mb-0.5">{label}</dt>
      <dd className="text-gray-700 dark:text-gray-200 break-words">{children}</dd>
    </div>
  )
}
