import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { checkInfraHealth } from '../../api/client'

/**
 * Shared layout for the /tv/* fullscreen dashboards — built for an always-on
 * NOC monitor: no sidebar, no top nav, dark high-contrast theme, large fonts,
 * auto-refreshing. All TV routes render OUTSIDE the app shell so the URL can be
 * bookmarked and shown on a dedicated screen.
 *
 * Pages fetch their own data (React Query with refetchInterval); this layout
 * renders the chrome and a refresh countdown synced to that interval.
 */

// High-contrast TV palette (shared by all dashboards).
export const TV = {
  bg: '#0a0a0f',
  card: '#1a1a2e',
  text: '#e8e8f0',
  muted: '#8a8aa0',
  accent: '#4f86c6',
  success: '#27ae60',
  warning: '#f39c12',
  error: '#e74c3c',
}

export function tvStatusColor(ok: boolean): string {
  return ok ? TV.success : TV.error
}

/** A big-number stat block. */
export function TVStat({ label, value, sub, color }: { label: string; value: ReactNode; sub?: string; color?: string }) {
  return (
    <div style={{ background: TV.card }} className="rounded-2xl px-6 py-5">
      <div className="text-sm uppercase tracking-widest" style={{ color: TV.muted }}>{label}</div>
      <div className="mt-1 font-bold tabular-nums leading-none" style={{ fontSize: 56, color: color ?? TV.text }}>{value}</div>
      {sub && <div className="mt-1 text-lg" style={{ color: TV.muted }}>{sub}</div>}
    </div>
  )
}

/** A titled panel container. */
export function TVPanel({ title, children, className }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div style={{ background: TV.card }} className={`rounded-2xl p-5 ${className ?? ''}`}>
      {title && <div className="mb-3 text-lg uppercase tracking-widest" style={{ color: TV.muted }}>{title}</div>}
      {children}
    </div>
  )
}

export default function TVLayout({
  title,
  refreshInterval = 30,
  rotation,
  children,
}: {
  title: string
  refreshInterval?: number
  rotation?: { current: string; nextCountdown: number; progressPct: number }
  children: ReactNode
}) {
  const [countdown, setCountdown] = useState(refreshInterval)
  const [now, setNow] = useState(() => new Date().toLocaleString())
  const [version, setVersion] = useState<string | null>(null)
  const startedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    checkInfraHealth().then((h) => { if (!cancelled) setVersion(h.version || null) }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    setCountdown(refreshInterval)
    const timer = setInterval(() => {
      setCountdown((c) => (c <= 1 ? refreshInterval : c - 1))
      setNow(new Date().toLocaleString())
    }, 1000)
    startedRef.current = true
    return () => clearInterval(timer)
  }, [refreshInterval])

  return (
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: TV.bg, color: TV.text }}>
      <header className="flex items-center justify-between px-8 py-4" style={{ borderBottom: `1px solid ${TV.card}` }}>
        <div className="flex items-baseline gap-4">
          <span className="text-3xl font-bold" style={{ color: TV.accent }}>spane</span>
          <span className="text-2xl" style={{ color: TV.muted }}>· {title}</span>
        </div>
        <div className="flex items-center gap-6 text-xl" style={{ color: TV.muted }}>
          {rotation && <span>next: {rotation.current} in {rotation.nextCountdown}s</span>}
          <span>↻ {countdown}s</span>
          <Link to="/dashboard" className="text-base px-3 py-1 rounded-lg" style={{ background: TV.card, color: TV.text }}>← App</Link>
        </div>
      </header>

      <main className="flex-1 overflow-hidden p-8">{children}</main>

      <footer className="px-8 py-2 text-base flex items-center justify-between gap-4"
              style={{ color: TV.muted, borderTop: `1px solid ${TV.card}` }}>
        {rotation ? (
          <div className="h-2 flex-1 rounded-full" style={{ background: TV.card }}>
            <div className="h-2 rounded-full" style={{ width: `${rotation.progressPct}%`, background: TV.accent, transition: 'width 1s linear' }} />
          </div>
        ) : (
          <span>{now}</span>
        )}
        {version && <span className="shrink-0">spane v{version}</span>}
      </footer>
    </div>
  )
}
