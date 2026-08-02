import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { DEVICE_COLUMNS } from '../lib/deviceColumns'

// Minimal column shape the picker needs (both DEVICE_COLUMNS and SERVER_COLUMNS
// satisfy it). Pass `columns` to reuse the picker on any column-config table.
export interface PickerColumn { key: string; label: string; locked?: boolean }

/**
 * Dropdown to choose & reorder a table's columns. The locked column(s) (e.g.
 * Hostname) are always shown and always first. Selection + order are owned by
 * the parent and persisted to localStorage there. `columns` defaults to the
 * Devices set so existing callers keep working; the Servers list passes its own.
 */
export default function ColumnPicker({ activeKeys, onChange, onReset, columns = DEVICE_COLUMNS }: {
  activeKeys: string[]
  onChange: (keys: string[]) => void
  onReset: () => void
  columns?: PickerColumn[]
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const active = new Set(activeKeys)
  const locked = columns.filter((c) => c.locked).map((c) => c.key)

  // Display order: active columns (in their saved order), then inactive ones.
  const ordered = [
    ...activeKeys,
    ...columns.map((c) => c.key).filter((k) => !active.has(k)),
  ]
  const colByKey = Object.fromEntries(columns.map((c) => [c.key, c]))

  const toggle = (key: string) => {
    const col = colByKey[key]
    if (col?.locked) return
    onChange(active.has(key) ? activeKeys.filter((k) => k !== key) : [...activeKeys, key])
  }

  const move = (key: string, dir: -1 | 1) => {
    const idx = activeKeys.indexOf(key)
    if (idx < 0) return
    const target = idx + dir
    // Never move above the locked column(s) at the front.
    if (target < locked.length || target >= activeKeys.length) return
    const next = [...activeKeys]
    ;[next[idx], next[target]] = [next[target], next[idx]]
    onChange(next)
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        title="Configure columns"
        className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 text-gray-700 dark:text-gray-300 inline-flex items-center gap-1.5"
      >
        <span aria-hidden>⊞</span> Columns
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-30 text-sm">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100 dark:border-gray-700">
            <span className="font-semibold text-gray-800 dark:text-gray-100">Configure Columns</span>
            <button onClick={onReset} className="text-xs text-blue-600 hover:text-blue-800 font-medium">Reset</button>
          </div>
          <div className="px-4 py-1.5 text-xs text-gray-400 dark:text-gray-500 border-b border-gray-50 dark:border-gray-700">
            {activeKeys.length} of {columns.length} columns shown
          </div>
          <div className="max-h-80 overflow-y-auto py-1">
            {ordered.map((key) => {
              const col = colByKey[key]
              if (!col) return null
              const isActive = active.has(key)
              const idx = activeKeys.indexOf(key)
              return (
                <div key={key} className="flex items-center gap-2 px-4 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <input
                    type="checkbox"
                    checked={isActive}
                    disabled={col.locked}
                    onChange={() => toggle(key)}
                    className="shrink-0"
                  />
                  <span className={clsx('flex-1 truncate', col.locked ? 'text-gray-400 dark:text-gray-500' : 'text-gray-700 dark:text-gray-300')}>
                    {col.label}{col.locked && <span className="text-xs text-gray-300 dark:text-gray-600 ml-1">(locked)</span>}
                  </span>
                  {isActive && !col.locked && (
                    <span className="flex items-center gap-0.5">
                      <button onClick={() => move(key, -1)} disabled={idx <= locked.length}
                        className="text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 disabled:opacity-30 px-1" title="Move up">▲</button>
                      <button onClick={() => move(key, 1)} disabled={idx >= activeKeys.length - 1}
                        className="text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 disabled:opacity-30 px-1" title="Move down">▼</button>
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
