import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { useSite } from '../store/siteStore'

// Threshold above which the dropdown shows a type-to-filter box.
const FILTER_THRESHOLD = 6

/**
 * Global site selector for the top navbar. Shows 🌐 All Sites by default and
 * 📍 <name> when a site is active. The selection lives in the global site store
 * (persisted to localStorage) so it filters every page that respects it.
 */
export default function SiteSelector() {
  const { selectedSite, selectedSiteName, setSelectedSite, sites } = useSite()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Close on outside click or Escape; focus the filter box when opening.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    inputRef.current?.focus()
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Nothing to switch between until at least one site exists.
  if (sites.length === 0) return null

  const q = query.trim().toLowerCase()
  const matches = q ? sites.filter((s) => s.name.toLowerCase().includes(q)) : sites

  const choose = (id: string | null) => {
    setSelectedSite(id)
    setOpen(false)
    setQuery('')
  }

  const itemCls = (active: boolean) =>
    clsx(
      'w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors',
      active
        ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 font-medium'
        : 'text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700/60',
    )

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        title={selectedSite ? `Filtered to ${selectedSiteName}` : 'Filter by site'}
        className={clsx(
          'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-sm font-medium transition-colors',
          selectedSite
            ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
            : 'border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800',
        )}
      >
        <span aria-hidden>{selectedSite ? '📍' : '🌐'}</span>
        <span className="max-w-[10rem] truncate">{selectedSiteName}</span>
        <span className={clsx('text-[10px] text-gray-400 transition-transform', open && 'rotate-180')} aria-hidden>▾</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-56 z-50 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg py-1">
          {sites.length > FILTER_THRESHOLD && (
            <div className="px-2 pb-1">
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter sites…"
                className="w-full px-2 py-1 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          )}

          <button onClick={() => choose(null)} className={itemCls(!selectedSite)}>
            <span aria-hidden>🌐</span> All Sites
          </button>
          <div className="my-1 border-t border-gray-100 dark:border-gray-700" aria-hidden />

          <div className="max-h-72 overflow-y-auto">
            {matches.map((s) => (
              <button key={s.id} onClick={() => choose(String(s.id))} className={itemCls(selectedSite === String(s.id))}>
                <span aria-hidden className="text-[10px] text-blue-500">●</span>
                <span className="truncate">{s.name}</span>
              </button>
            ))}
            {matches.length === 0 && (
              <div className="px-3 py-2 text-xs text-gray-400">No sites match.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
