/**
 * ChatOps slide-out chat panel + its launcher.
 *
 * Rendered once inside <Layout> as a sibling to the page content (outside the
 * page <Routes>), so it overlays every authenticated page and — together with
 * ChatOpsProvider mounted above the routes — keeps its open state and message
 * history across navigation.
 *
 * Replies are the structured IntentResult from /api/chatops/query/, rendered
 * natively (title heading, severity badge, label/value field rows, body lines) —
 * not as markdown. Denials and errors fall back to their plain message.
 */
import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { useChatOps, type ChatMessage, type SpaneMessage } from '../store/chatOpsStore'
import type { ChatOpsSeverity } from '../api/client'

// Severity → badge classes, mirroring the alerts/CVE convention used elsewhere.
const SEVERITY_BADGE: Record<ChatOpsSeverity, string> = {
  critical: 'bg-red-100 text-red-700 border border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800',
  high: 'bg-orange-100 text-orange-700 border border-orange-200 dark:bg-orange-900/30 dark:text-orange-400 dark:border-orange-800',
  medium: 'bg-yellow-100 text-yellow-700 border border-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-400 dark:border-yellow-800',
  low: 'bg-blue-100 text-blue-700 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800',
  info: 'bg-green-100 text-green-700 border border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800',
}

const EXAMPLE_PROMPTS = ['any alerts', 'status of a device', 'help']

function SpaneReply({ msg }: { msg: SpaneMessage }) {
  if (msg.result) {
    const r = msg.result
    return (
      <div className="rounded-lg rounded-tl-sm bg-gray-100 dark:bg-gray-800 px-3.5 py-3 text-sm text-gray-800 dark:text-gray-100">
        <div className="flex items-start justify-between gap-2">
          {r.title && <div className="font-semibold leading-snug">{r.title}</div>}
          <span
            className={clsx(
              'shrink-0 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide',
              SEVERITY_BADGE[r.severity] ?? SEVERITY_BADGE.info,
            )}
          >
            {r.severity}
          </span>
        </div>
        {r.fields.length > 0 && (
          <dl className="mt-2 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1">
            {r.fields.map(([label, val], i) => (
              <div key={i} className="contents">
                <dt className="text-gray-500 dark:text-gray-400">{label}</dt>
                <dd className="font-medium break-words">{val}</dd>
              </div>
            ))}
          </dl>
        )}
        {r.lines.length > 0 && (
          <div className="mt-2 space-y-1">
            {r.lines.map((line, i) => (
              <p key={i} className="leading-snug break-words">{line}</p>
            ))}
          </div>
        )}
      </div>
    )
  }
  // Denial or transport error → plain guidance text.
  const text = msg.denied ?? msg.error ?? 'No response.'
  return (
    <div className="rounded-lg rounded-tl-sm bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-3.5 py-3 text-sm text-amber-800 dark:text-amber-300">
      {text}
    </div>
  )
}

function MessageRow({ msg }: { msg: ChatMessage }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-tr-sm bg-blue-600 text-white px-3.5 py-2 text-sm break-words">
          {msg.text}
        </div>
      </div>
    )
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%]">
        <SpaneReply msg={msg} />
      </div>
    </div>
  )
}

export default function ChatOpsPanel() {
  const { open, messages, loading, openPanel, closePanel, toggle, sendQuery } = useChatOps()
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  // ESC collapses the panel while it's open.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closePanel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, closePanel])

  // Focus the input when the panel opens; scroll to newest on open/new message.
  useEffect(() => { if (open) inputRef.current?.focus() }, [open])
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, loading, open])

  const submit = (text: string) => {
    if (!text.trim() || loading) return
    sendQuery(text)
    setDraft('')
  }

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    submit(draft)
  }

  return (
    <>
      {/* Launcher — floating bottom-right, hidden while the panel is open. */}
      {!open && (
        <button
          onClick={openPanel}
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full bg-blue-600 hover:bg-blue-700 text-white pl-4 pr-5 py-3 shadow-lg shadow-blue-600/30 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2"
          aria-label="Ask spane"
        >
          <span aria-hidden className="text-lg leading-none">💬</span>
          <span className="text-sm font-semibold">Ask spane</span>
        </button>
      )}

      {/* Backdrop (mobile): tap to dismiss. */}
      <div
        className={clsx(
          'fixed inset-0 z-40 bg-black/30 transition-opacity lg:hidden',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={closePanel}
        aria-hidden
      />

      {/* Slide-out panel. Always mounted (so messages persist); animated via
          translate so it slides in/out. */}
      <aside
        className={clsx(
          'fixed inset-y-0 right-0 z-50 w-full max-w-md flex flex-col bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-800 shadow-2xl transition-transform duration-200 ease-out',
          open ? 'translate-x-0' : 'translate-x-full',
        )}
        role="dialog"
        aria-label="Ask spane"
        aria-hidden={!open}
      >
        {/* Header */}
        <header className="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <span aria-hidden className="text-lg">💬</span>
          <div className="min-w-0">
            <div className="font-semibold text-gray-800 dark:text-gray-100 leading-tight">Ask spane</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Status, alerts, CVEs, lifecycle</div>
          </div>
          <button
            onClick={toggle}
            className="ml-auto p-1.5 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            aria-label="Close chat"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        {/* Messages */}
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-3">
          {messages.length === 0 ? (
            <div className="text-sm text-gray-500 dark:text-gray-400">
              <p className="mb-3">Ask about your infrastructure in plain language.</p>
              <div className="flex flex-wrap gap-2">
                {EXAMPLE_PROMPTS.map((p) => (
                  <button
                    key={p}
                    onClick={() => submit(p)}
                    className="px-2.5 py-1 rounded-full border border-gray-300 dark:border-gray-700 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => <MessageRow key={m.id} msg={m} />)
          )}
          {loading && (
            <div className="flex justify-start">
              <div className="rounded-lg rounded-tl-sm bg-gray-100 dark:bg-gray-800 px-3.5 py-2.5">
                <span className="flex gap-1" aria-label="spane is thinking">
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" />
                </span>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* Composer */}
        <form onSubmit={onSubmit} className="flex items-center gap-2 px-3 py-3 border-t border-gray-200 dark:border-gray-800">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ask spane…"
            className="flex-1 min-w-0 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-800 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          <button
            type="submit"
            disabled={!draft.trim() || loading}
            className="shrink-0 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-3.5 py-2 text-sm font-semibold transition-colors"
          >
            Send
          </button>
        </form>
      </aside>
    </>
  )
}
