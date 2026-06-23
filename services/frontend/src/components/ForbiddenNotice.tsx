import { useEffect } from 'react'
import { useNoticeStore } from '../store/noticeStore'

/**
 * Global "Not authorized" banner, raised by the API client on any 403 it isn't
 * told to skip. Non-destructive: it informs without tearing down the current
 * view, catching deep-links and mid-session capability changes that nav-gating
 * misses. Auto-dismisses; the API 403 remains the real security boundary.
 */
export default function ForbiddenNotice() {
  const message = useNoticeStore((s) => s.forbidden)
  const clear = useNoticeStore((s) => s.clearForbidden)

  useEffect(() => {
    if (!message) return
    const t = setTimeout(clear, 6000)
    return () => clearTimeout(t)
  }, [message, clear])

  if (!message) return null

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-4 right-4 z-[60] max-w-sm flex items-start gap-3 rounded-lg border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-900/30 px-4 py-3 shadow-lg"
    >
      <span className="text-lg leading-none" aria-hidden>🔒</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">Not authorized</p>
        <p className="text-xs text-amber-700 dark:text-amber-400 break-words">{message}</p>
      </div>
      <button
        onClick={clear}
        aria-label="Dismiss"
        className="text-amber-500 hover:text-amber-700 dark:hover:text-amber-300 text-lg leading-none"
      >
        ×
      </button>
    </div>
  )
}
