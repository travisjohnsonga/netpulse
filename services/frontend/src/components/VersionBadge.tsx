import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchVersionCheck, type VersionCheck } from '../api/client'

// Sidebar-footer version badge. Always shows the running version; turns amber
// with an ↑ when an update is available (tooltip with details + how to update).
// Re-checks hourly; hides entirely if the check is unavailable/disabled.
export default function VersionBadge() {
  const [v, setV] = useState<VersionCheck | null>(null)

  useEffect(() => {
    let cancelled = false
    const check = () => fetchVersionCheck().then((d) => { if (!cancelled) setV(d) })
    check()
    const t = setInterval(check, 3_600_000) // 1 hour
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  if (!v) return null
  const upd = v.update_available

  return (
    <div className="relative group inline-block">
      <span
        className={clsx(
          'text-xs px-2 py-0.5 rounded-full cursor-default',
          upd ? 'bg-amber-500/20 text-amber-300' : 'bg-gray-800 text-gray-500',
        )}
        title={upd ? 'Update available' : `NetPulse v${v.current_version}`}
      >
        v{v.current_version}{upd ? ' ↑' : ''}
      </span>
      {upd && (
        <div className="absolute bottom-7 left-0 hidden group-hover:block w-60 z-50 text-left
                        bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700
                        rounded-lg p-3 shadow-lg">
          <p className="text-sm font-medium text-gray-800 dark:text-gray-100">Update available</p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {v.latest_version ? `v${v.latest_version}` : 'A newer version'}
            {v.commits_behind ? ` · ${v.commits_behind} commit${v.commits_behind === 1 ? '' : 's'} ahead` : ''}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Run: <code className="text-gray-700 dark:text-gray-300">./scripts/update.sh</code>
          </p>
          <a href={v.release_notes_url} target="_blank" rel="noreferrer"
             className="text-xs text-blue-500 hover:underline mt-1 block">View changes →</a>
        </div>
      )}
    </div>
  )
}
