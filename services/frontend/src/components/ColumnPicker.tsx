import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { DEVICE_COLUMNS } from '../lib/deviceColumns'

/**
 * Dropdown to choose & reorder the Devices table columns. Hostname is locked
 * (always shown, always first). Selection + order are owned by the parent and
 * persisted to localStorage there.
 */
export default function ColumnPicker({ activeKeys, onChange, onReset }: {
  activeKeys: string[]
  onChange: (keys: string[]) => void
  onReset: () => void
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
  const locked = DEVICE_COLUMNS.filter((c) => c.locked).map((c) => c.key)

  // Display order: active columns (in their saved order), then inactive ones.
  const ordered = [
    ...activeKeys,
    ...DEVICE_COLUMNS.map((c) => c.key).filter((k) => !active.has(k)),
  ]
  const colByKey = Object.fromEntries(DEVICE_COLUMNS.map((c) => [c.key, c]))

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
        className="px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 text-gray-700 inline-flex items-center gap-1.5"
      >
        <span aria-hidden>⊞</span> Columns
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-72 bg-white border border-gray-200 rounded-lg shadow-lg z-30 text-sm">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100">
            <span className="font-semibold text-gray-800">Configure Columns</span>
            <button onClick={onReset} className="text-xs text-blue-600 hover:text-blue-800 font-medium">Reset</button>
          </div>
          <div className="px-4 py-1.5 text-xs text-gray-400 border-b border-gray-50">
            {activeKeys.length} of {DEVICE_COLUMNS.length} columns shown
          </div>
          <div className="max-h-80 overflow-y-auto py-1">
            {ordered.map((key) => {
              const col = colByKey[key]
              if (!col) return null
              const isActive = active.has(key)
              const idx = activeKeys.indexOf(key)
              return (
                <div key={key} className="flex items-center gap-2 px-4 py-1.5 hover:bg-gray-50">
                  <input
                    type="checkbox"
                    checked={isActive}
                    disabled={col.locked}
                    onChange={() => toggle(key)}
                    className="shrink-0"
                  />
                  <span className={clsx('flex-1 truncate', col.locked ? 'text-gray-400' : 'text-gray-700')}>
                    {col.label}{col.locked && <span className="text-xs text-gray-300 ml-1">(locked)</span>}
                  </span>
                  {isActive && !col.locked && (
                    <span className="flex items-center gap-0.5">
                      <button onClick={() => move(key, -1)} disabled={idx <= locked.length}
                        className="text-gray-400 hover:text-gray-700 disabled:opacity-30 px-1" title="Move up">▲</button>
                      <button onClick={() => move(key, 1)} disabled={idx >= activeKeys.length - 1}
                        className="text-gray-400 hover:text-gray-700 disabled:opacity-30 px-1" title="Move down">▼</button>
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
