import { type ReactNode } from 'react'
import clsx from 'clsx'
import { type Device, type PingSummary, reachabilityOf, reachabilityReason } from '../api/client'
import { sshUrl, sshTooltip } from './ssh'
import PingSparkline, { pingColor } from '../components/PingSparkline'

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-100 text-green-700',
  inactive: 'bg-gray-100 text-gray-600',
  pending: 'bg-yellow-100 text-yellow-700',
  unreachable: 'bg-red-100 text-red-700',
}

const REACH_DOT: Record<string, string> = {
  reachable: 'bg-green-500',
  degraded: 'bg-yellow-500',
  unreachable: 'bg-red-500',
}

export interface ColCtx {
  credNames: Record<number, string>
  // Per-device ping summary, fetched in the background after the list renders.
  ping?: Record<number, PingSummary>
}

export interface DeviceColumn {
  key: string
  label: string
  locked?: boolean
  default: boolean
  // Backend `ordering` field this column sorts by. Omit for client-derived
  // columns (role/notes/credentials) that have no stable server-side order.
  sortKey?: string
  render: (d: Device, ctx: ColCtx) => ReactNode
}

function relTime(iso: string | null): string {
  if (!iso) return '—'
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

function roleFromNotes(notes: string): string {
  for (const line of (notes || '').split('\n')) {
    if (line.toLowerCase().startsWith('role:')) return line.split(':').slice(1).join(':').trim()
  }
  return '—'
}

/** Compact downtime since the device went unreachable, e.g. "4m", "2h", "3d". */
function downtime(iso: string | null | undefined): string | null {
  if (!iso) return null
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

const dash = (v: string | null | undefined): ReactNode => (v ? v : <span className="text-gray-300">—</span>)

export const DEVICE_COLUMNS: DeviceColumn[] = [
  {
    key: 'hostname', label: 'Hostname', locked: true, default: true, sortKey: 'hostname',
    render: (d) => (
      <span className="inline-flex items-center gap-2">
        <span className="font-medium text-gray-800 dark:text-gray-100" title={d.hostname}>{d.display_hostname || d.hostname}</span>
        <a
          href={sshUrl(d)}
          onClick={(e) => e.stopPropagation()}
          target="_blank" rel="noopener noreferrer"
          title={sshTooltip(d.hostname, d)}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium border border-blue-200 text-blue-600 hover:bg-blue-50 dark:border-blue-800 dark:text-blue-400 dark:hover:bg-blue-900/30 transition-colors"
        >
          <span aria-hidden>⌨</span> Connect
        </a>
      </span>
    ),
  },
  {
    key: 'status', label: 'Status', default: true, sortKey: 'status',
    render: (d) => {
      const reach = reachabilityOf(d)
      const down = d.status === 'unreachable' ? downtime(d.unreachable_since) : null
      return (
        <span className="inline-flex items-center gap-1.5">
          <span className={clsx('w-2 h-2 rounded-full', REACH_DOT[reach])} title={reachabilityReason(d)} />
          <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_COLORS[d.status] ?? 'bg-gray-100 text-gray-600')}>
            {d.status}{down ? ` · ${down}` : ''}
          </span>
        </span>
      )
    },
  },
  { key: 'ip_address', label: 'IP Address', default: true, sortKey: 'ip_address', render: (d) => <span className="font-mono text-xs text-gray-600">{d.ip_address}</span> },
  {
    key: 'ping', label: 'Ping', default: true,
    render: (d, ctx) => {
      const p = ctx.ping?.[d.id]
      const ms = p?.current_ms ?? null
      const color = pingColor(ms)
      return (
        <span className="inline-flex items-center gap-2">
          <span className="text-xs tabular-nums w-12" style={ms != null ? { color } : undefined}>
            {ms != null ? `${ms}ms` : <span className="text-gray-300 dark:text-gray-600">—</span>}
          </span>
          {/* Sparkline hidden on small screens; ms value always shown. */}
          {p?.sparkline?.length ? (
            <span className="hidden sm:inline-block"><PingSparkline data={p.sparkline} color={color} /></span>
          ) : null}
        </span>
      )
    },
  },
  { key: 'vendor', label: 'Vendor', default: true, sortKey: 'vendor', render: (d) => <span className="text-gray-600">{dash(d.vendor)}</span> },
  { key: 'platform', label: 'Platform', default: true, sortKey: 'platform', render: (d) => <span className="text-gray-600">{dash(d.platform)}</span> },
  { key: 'site', label: 'Site', default: true, sortKey: 'site__name', render: (d) => <span className="text-gray-600">{dash(d.site_name)}</span> },
  { key: 'management_ip', label: 'Mgmt IP', default: false, render: (d) => <span className="font-mono text-xs text-gray-600">{dash(d.management_ip)}</span> },
  { key: 'os_version', label: 'OS Version', default: false, sortKey: 'os_version', render: (d) => <span className="text-gray-600">{dash(d.os_version)}</span> },
  { key: 'model', label: 'Model', default: false, sortKey: 'model', render: (d) => <span className="text-gray-600">{dash(d.model)}</span> },
  { key: 'serial_number', label: 'Serial', default: false, sortKey: 'serial_number', render: (d) => <span className="font-mono text-xs text-gray-600">{dash(d.serial_number)}</span> },
  { key: 'last_seen', label: 'Last Seen', default: false, sortKey: 'last_seen', render: (d) => <span className="text-gray-500 text-xs">{relTime(d.last_seen)}</span> },
  {
    key: 'credential_profile', label: 'Credentials', default: false,
    render: (d, ctx) => <span className="text-gray-600">{d.credential_profile ? (ctx.credNames[d.credential_profile] ?? `#${d.credential_profile}`) : <span className="text-gray-300">—</span>}</span>,
  },
  { key: 'created_at', label: 'Added', default: false, sortKey: 'created_at', render: (d) => <span className="text-gray-500 text-xs">{new Date(d.created_at).toLocaleDateString()}</span> },
  { key: 'role', label: 'Role', default: false, render: (d) => <span className="text-gray-600">{roleFromNotes(d.notes)}</span> },
  { key: 'notes', label: 'Notes', default: false, render: (d) => <span className="text-gray-500 text-xs line-clamp-1 max-w-xs">{dash(d.notes?.trim())}</span> },
]

export const COLUMN_STORAGE_KEY = 'netpulse.devices.columns'

export function defaultColumnKeys(): string[] {
  return DEVICE_COLUMNS.filter((c) => c.locked || c.default).map((c) => c.key)
}

/** Load persisted column keys, sanitised against the current definitions. */
export function loadColumnKeys(): string[] {
  try {
    const raw = localStorage.getItem(COLUMN_STORAGE_KEY)
    if (!raw) return defaultColumnKeys()
    const saved: string[] = JSON.parse(raw)
    const valid = saved.filter((k) => DEVICE_COLUMNS.some((c) => c.key === k))
    // Locked columns are always present and lead.
    const locked = DEVICE_COLUMNS.filter((c) => c.locked).map((c) => c.key)
    const ordered = [...locked, ...valid.filter((k) => !locked.includes(k))]
    return ordered.length ? ordered : defaultColumnKeys()
  } catch {
    return defaultColumnKeys()
  }
}

export function saveColumnKeys(keys: string[]): void {
  try { localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(keys)) } catch { /* ignore */ }
}
