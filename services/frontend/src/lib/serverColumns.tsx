import { type ReactNode } from 'react'
import { type Server, type PingSummary } from '../api/client'
import PingSparkline, { pingColor } from '../components/PingSparkline'
import StatusBadge from '../components/StatusBadge'
import { compactAgo } from './time'

// Column config for the Servers list — mirrors deviceColumns so both lists share
// the column picker + the canonical shared order (Hostname → Status → Ping → CPU
// → Memory → Last Change), with server-specific columns slotted after.

const OFFLINE_MS = 5 * 60 * 1000
function serverUp(s: Server): boolean {
  if (typeof s.is_online === 'boolean') return s.is_online
  return s.status === 'active' && !!s.last_seen &&
    Date.now() - new Date(s.last_seen).getTime() < OFFLINE_MS
}

export interface ServerColCtx {
  ping?: Record<number, PingSummary>
}

export interface ServerColumn {
  key: string
  label: string
  locked?: boolean
  default: boolean
  render: (s: Server, ctx: ServerColCtx) => ReactNode
}

const dash = <span className="text-gray-300 dark:text-gray-500">—</span>

function MetricBar({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return <span className="text-xs text-gray-300 dark:text-gray-500">—</span>
  const c = pct >= 80 ? 'bg-red-500' : pct >= 60 ? 'bg-amber-500' : 'bg-green-500'
  return (
    <span className="inline-flex items-center gap-2 min-w-[6rem]">
      <span className="flex-1 h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
        <span className={`block h-full ${c}`} style={{ width: `${Math.min(100, pct)}%` }} />
      </span>
      <span className="text-xs tabular-nums w-9 text-right text-gray-600 dark:text-gray-300">{Math.round(pct)}%</span>
    </span>
  )
}

export const SERVER_COLUMNS: ServerColumn[] = [
  {
    key: 'hostname', label: 'Hostname', locked: true, default: true,
    render: (s) => <span className="font-medium text-gray-900 dark:text-gray-100">{s.hostname}</span>,
  },
  // ── Canonical shared order (identical positions to the Devices list) ─────────
  { key: 'status', label: 'Status', default: true, render: (s) => <StatusBadge up={serverUp(s)} /> },
  {
    key: 'ping', label: 'Ping', default: true,
    render: (s, ctx) => {
      const p = s.device_id != null ? ctx.ping?.[s.device_id] : undefined
      const ms = p?.current_ms ?? null
      const color = pingColor(ms)
      return (
        <span className="inline-flex items-center gap-2">
          <span className="text-xs tabular-nums w-12" style={ms != null ? { color } : undefined}>
            {ms != null ? `${ms}ms` : dash}
          </span>
          {p?.sparkline?.length ? (
            <span className="hidden sm:inline-block"><PingSparkline data={p.sparkline} color={color} /></span>
          ) : null}
        </span>
      )
    },
  },
  { key: 'cpu', label: 'CPU', default: true, render: (s) => <MetricBar pct={s.latest_metrics.cpu_pct} /> },
  { key: 'memory', label: 'Memory', default: true, render: (s) => <MetricBar pct={s.latest_metrics.memory_pct} /> },
  {
    key: 'last_change', label: 'Last Change', default: true,
    render: (s) => <span className="text-xs tabular-nums text-gray-500 dark:text-gray-400">{compactAgo(s.last_seen)}</span>,
  },
  // ── Server-specific columns ─────────────────────────────────────────────────
  { key: 'os', label: 'OS', default: true, render: (s) => <span className="text-gray-600 dark:text-gray-300">{s.os_name || s.os || '—'}</span> },
  {
    key: 'disk', label: 'Disk', default: true,
    render: (s) => {
      const m = s.latest_metrics
      if (m.disk_max_pct == null) return dash
      return (
        <span className={m.disk_max_pct >= 80 ? 'text-red-600 dark:text-red-400' : 'text-gray-600 dark:text-gray-300'}>
          {m.disk_max_mount} {Math.round(m.disk_max_pct)}%
        </span>
      )
    },
  },
  {
    key: 'load', label: 'Load', default: true,
    render: (s) => <span className="tabular-nums text-gray-600 dark:text-gray-300">{s.latest_metrics.load_1 == null ? '—' : s.latest_metrics.load_1.toFixed(2)}</span>,
  },
  {
    key: 'roles', label: 'Roles', default: true,
    render: (s) => (
      <div className="flex flex-wrap gap-1">
        {s.roles.length ? s.roles.map((r) => (
          <span key={r} className="px-1.5 py-0.5 text-[10px] uppercase rounded bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">{r}</span>
        )) : dash}
      </div>
    ),
  },
  {
    key: 'ip_address', label: 'IP Address', default: true,
    render: (s) => <span className="font-mono text-xs text-gray-600 dark:text-gray-300">{s.last_ip || dash}</span>,
  },
]

export const SERVER_COLUMN_STORAGE_KEY = 'netpulse.servers.columns'

export function defaultServerColumnKeys(): string[] {
  return SERVER_COLUMNS.filter((c) => c.locked || c.default).map((c) => c.key)
}

export function loadServerColumnKeys(): string[] {
  try {
    const raw = localStorage.getItem(SERVER_COLUMN_STORAGE_KEY)
    if (!raw) return defaultServerColumnKeys()
    const saved: string[] = JSON.parse(raw)
    const valid = saved.filter((k) => SERVER_COLUMNS.some((c) => c.key === k))
    const locked = SERVER_COLUMNS.filter((c) => c.locked).map((c) => c.key)
    const ordered = [...locked, ...valid.filter((k) => !locked.includes(k))]
    return ordered.length ? ordered : defaultServerColumnKeys()
  } catch {
    return defaultServerColumnKeys()
  }
}

export function saveServerColumnKeys(keys: string[]): void {
  try { localStorage.setItem(SERVER_COLUMN_STORAGE_KEY, JSON.stringify(keys)) } catch { /* ignore */ }
}
