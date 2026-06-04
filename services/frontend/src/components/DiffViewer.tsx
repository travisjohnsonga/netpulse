import { useMemo, useState } from 'react'
import clsx from 'clsx'
import { type ConfigDiff, type ConfigDiffHunk } from '../api/client'

/** Build a standard unified-diff text from structured hunks (for copy/download). */
export function unifiedDiffText(diff: ConfigDiff, leftLabel = 'a', rightLabel = 'b'): string {
  const out: string[] = [`--- ${leftLabel}`, `+++ ${rightLabel}`]
  for (const h of diff.hunks) {
    out.push(`@@ -${h.old_start},${h.old_count} +${h.new_start},${h.new_count} @@`)
    for (const ln of h.lines) {
      const prefix = ln.type === 'add' ? '+' : ln.type === 'remove' ? '-' : ' '
      out.push(prefix + ln.content)
    }
  }
  return out.join('\n') + '\n'
}

function hunkVisibleLines(hunk: ConfigDiffHunk, showContext: boolean) {
  return showContext ? hunk.lines : hunk.lines.filter((l) => l.type !== 'context')
}

export default function DiffViewer({ diff, leftLabel = 'a', rightLabel = 'b' }: {
  diff: ConfigDiff
  leftLabel?: string
  rightLabel?: string
}) {
  const [showContext, setShowContext] = useState(true)
  const [copied, setCopied] = useState(false)

  const { added, removed, changed } = diff.summary
  const patch = useMemo(() => unifiedDiffText(diff, leftLabel, rightLabel), [diff, leftLabel, rightLabel])

  const copyDiff = async () => {
    try {
      await navigator.clipboard.writeText(patch)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard unavailable */ }
  }

  const downloadDiff = () => {
    const blob = new Blob([patch], { type: 'text/x-patch' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${sanitize(leftLabel)}__${sanitize(rightLabel)}.diff`
    a.click()
    URL.revokeObjectURL(url)
  }

  const identical = diff.hunks.length === 0

  return (
    <div>
      {/* Summary bar + actions */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <Badge className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">+{added} added</Badge>
          <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">-{removed} removed</Badge>
          <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">~{changed} changed</Badge>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400 select-none cursor-pointer">
            <input type="checkbox" checked={showContext} onChange={(e) => setShowContext(e.target.checked)} className="rounded" />
            Show context
          </label>
          <button onClick={copyDiff} disabled={identical} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-40">
            {copied ? 'Copied ✓' : 'Copy diff'}
          </button>
          <button onClick={downloadDiff} disabled={identical} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-40">
            Download .diff
          </button>
        </div>
      </div>

      {identical ? (
        <div className="py-16 text-center text-sm text-gray-500 dark:text-gray-400">The two configurations are identical.</div>
      ) : (
        <div className="font-mono text-xs overflow-auto max-h-[36rem] bg-gray-900">
          {diff.hunks.map((hunk, i) => (
            <div key={i}>
              <div className="bg-sky-950 text-sky-300 px-4 py-1 select-none">
                @@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@
              </div>
              {hunkVisibleLines(hunk, showContext).map((line, j) => (
                <div
                  key={j}
                  className={clsx(
                    'flex px-2 leading-relaxed',
                    line.type === 'add' && 'bg-green-950/60 text-green-300',
                    line.type === 'remove' && 'bg-red-950/60 text-red-300',
                    line.type === 'context' && 'text-gray-400',
                  )}
                >
                  <span className="select-none text-gray-600 w-10 inline-block pr-2 text-right shrink-0">{line.line_no}</span>
                  <span className="select-none w-4 inline-block text-center shrink-0">
                    {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}
                  </span>
                  <span className="whitespace-pre-wrap break-all">{line.content || ' '}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Badge({ children, className }: { children: React.ReactNode; className: string }) {
  return <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', className)}>{children}</span>
}

function sanitize(s: string): string {
  return s.replace(/[^a-z0-9._-]+/gi, '_').slice(0, 60) || 'config'
}
