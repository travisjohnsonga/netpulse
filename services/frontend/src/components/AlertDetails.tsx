import { useState } from 'react'
import clsx from 'clsx'
import type { Alert } from '../api/client'

const PREVIEW_LINES = 50

/** Looks like a unified diff (config-change alerts). */
function isDiff(alert: Alert, text: string): boolean {
  return alert.alert_type === 'config_changed' || text.includes('@@ ') || text.includes('--- previous')
}

function lineClass(line: string): string {
  if (line.startsWith('+') && !line.startsWith('+++')) return 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400'
  if (line.startsWith('-') && !line.startsWith('---')) return 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'
  if (line.startsWith('@@')) return 'bg-cyan-50 dark:bg-cyan-900/20 text-cyan-700 dark:text-cyan-400'
  return 'text-gray-600 dark:text-gray-400'
}

/** Colored, scrollable diff with a show-full toggle. */
function AlertDiff({ diff }: { diff: string }) {
  const [full, setFull] = useState(false)
  const lines = diff.split('\n')
  const shown = full ? lines : lines.slice(0, PREVIEW_LINES)
  const copy = () => navigator.clipboard?.writeText(diff).catch(() => {})
  return (
    <div>
      <div className="font-mono text-xs overflow-x-auto overflow-y-auto max-h-96 rounded-md border border-gray-200 dark:border-gray-700">
        {shown.map((line, i) => (
          <div key={i} className={clsx('px-2 whitespace-pre leading-5', lineClass(line))}>{line || ' '}</div>
        ))}
      </div>
      <div className="flex items-center gap-3 mt-1.5">
        {lines.length > PREVIEW_LINES && (
          <button onClick={() => setFull((f) => !f)} className="text-xs text-blue-600 dark:text-blue-400 hover:underline">
            {full ? 'Show less ▲' : `Show full diff (${lines.length} lines) ▼`}
          </button>
        )}
        <button onClick={copy} className="text-xs text-gray-500 dark:text-gray-400 hover:underline">Copy</button>
      </div>
    </div>
  )
}

/**
 * Type-aware rendering of an alert's expanded details: a colored diff viewer for
 * config-change alerts, a one-line summary for interface/latency/unreachable
 * alerts, otherwise the detail/message as preformatted text.
 */
export default function AlertDetails({ alert }: { alert: Alert }) {
  const text = (alert.details || '').trim()

  if (text && isDiff(alert, text)) {
    return (
      <div>
        <p className="text-xs font-semibold text-gray-600 dark:text-gray-300 mb-1.5">Changes detected</p>
        <AlertDiff diff={text} />
        {alert.device_id != null && (
          <a href={`/devices/${alert.device_id}?tab=configuration`} className="inline-block mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline">View config →</a>
        )}
      </div>
    )
  }

  // Interface state-change: "GigabitEthernet1: up → down".
  if (alert.is_interface_alert && alert.interface) {
    const to = alert.transition || (alert.is_resolved ? 'up' : 'down')
    const from = to === 'down' ? 'up' : 'down'
    return (
      <p className="text-sm text-gray-700 dark:text-gray-200">
        <span className="font-mono">{alert.interface}</span>: {from} → <span className={to === 'down' ? 'text-red-600 dark:text-red-400 font-medium' : 'text-green-600 dark:text-green-400 font-medium'}>{to}</span>
        {alert.downtime_seconds != null && <span className="text-gray-400"> (down {alert.downtime_seconds}s)</span>}
      </p>
    )
  }

  // Fallback: preformatted text (preserves newlines), scrollable if long.
  const body = text || alert.message || '—'
  return (
    <pre className="font-mono text-xs text-gray-700 dark:text-gray-300 whitespace-pre-wrap break-words max-h-72 overflow-y-auto">{body}</pre>
  )
}
