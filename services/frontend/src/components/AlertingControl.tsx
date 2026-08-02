import { useEffect, useState } from 'react'
import clsx from 'clsx'
import type { AlertingPatch } from '../api/client'

// Per-device/server "Alerting" control (the three silencing forms, §4b):
// On / Observe-only (permanent, generate-but-don't-notify) + "Silence for…"
// (timed mute, auto-resumes). Suppresses NOTIFICATION only — alerts still show
// in the UI. Used on both device and server detail.
const DURATIONS: { label: string; hours: number }[] = [
  { label: '1 hour', hours: 1 }, { label: '4 hours', hours: 4 },
  { label: '8 hours', hours: 8 }, { label: '24 hours', hours: 24 },
]

function remaining(iso: string): string {
  let s = Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 1000))
  const h = Math.floor(s / 3600); s -= h * 3600
  const m = Math.floor(s / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export default function AlertingControl({ alertingEnabled, silencedUntil, onUpdate, canEdit }: {
  alertingEnabled: boolean
  silencedUntil: string | null | undefined
  onUpdate: (patch: AlertingPatch) => Promise<void>
  canEdit: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [, setTick] = useState(0)
  const silenced = !!silencedUntil && new Date(silencedUntil).getTime() > Date.now()

  useEffect(() => {
    if (!silenced) return
    const t = setInterval(() => setTick((x) => x + 1), 30_000)  // live-ish countdown
    return () => clearInterval(t)
  }, [silenced, silencedUntil])

  const apply = async (patch: AlertingPatch) => {
    setBusy(true); setMenuOpen(false)
    try { await onUpdate(patch) } finally { setBusy(false) }
  }
  const silenceFor = (hours: number) =>
    apply({ silenced_until: new Date(Date.now() + hours * 3600_000).toISOString() })
  const custom = () => {
    const h = Number(window.prompt('Silence alerts for how many hours?', '2'))
    if (h && h > 0) silenceFor(h)
  }

  const off = !alertingEnabled || silenced
  return (
    <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Alerting</div>
          <div className="text-xs text-gray-500 dark:text-gray-400">
            {!alertingEnabled
              ? 'Observe-only — alerts still appear in the UI but never notify.'
              : silenced
                ? `Silenced for ${remaining(silencedUntil!)} — notifications resume automatically.`
                : 'On — alerts notify the configured channels.'}
          </div>
        </div>
        <span className={clsx('inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full',
          off ? 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
              : 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400')}>
          <span className={clsx('w-2 h-2 rounded-full', off ? 'bg-amber-500' : 'bg-green-500')} />
          {!alertingEnabled ? 'Observe-only' : silenced ? 'Silenced' : 'On'}
        </span>
      </div>

      {canEdit && (
        <div className="flex flex-wrap items-center gap-2 mt-3">
          <button disabled={busy} onClick={() => apply({ alerting_enabled: !alertingEnabled })}
            className="px-3 py-1.5 text-xs border rounded-lg dark:border-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">
            {alertingEnabled ? 'Set observe-only' : 'Enable alerting'}
          </button>
          {silenced ? (
            <button disabled={busy} onClick={() => apply({ silenced_until: null })}
              className="px-3 py-1.5 text-xs border rounded-lg dark:border-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">
              Un-silence now
            </button>
          ) : (
            <div className="relative">
              <button disabled={busy} onClick={() => setMenuOpen((o) => !o)}
                className="px-3 py-1.5 text-xs border rounded-lg dark:border-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">
                Silence for… ▾
              </button>
              {menuOpen && (
                <div className="absolute z-10 mt-1 w-32 bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg py-1 text-xs">
                  {DURATIONS.map((d) => (
                    <button key={d.hours} onClick={() => silenceFor(d.hours)}
                      className="block w-full text-left px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">{d.label}</button>
                  ))}
                  <button onClick={custom}
                    className="block w-full text-left px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">Custom…</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
