import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { networkSearch, type NetworkSearchResult } from '../api/client'
import { detectQueryKind } from '../lib/ipmac'

// Compact IP/MAC quick-search for the top header. Click the 🔍 to reveal an
// input; results drop down inline. "View full results" hands off to the
// dedicated /network/lookup page with the query pre-filled.
export default function HeaderSearch() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [result, setResult] = useState<NetworkSearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  useEffect(() => { if (open) inputRef.current?.focus() }, [open])

  const run = async (e: React.FormEvent) => {
    e.preventDefault()
    const query = q.trim()
    if (!query) return
    setLoading(true)
    try {
      setResult(await networkSearch(query))
    } catch {
      setResult({ query, arp: [], mac: [] })
    } finally {
      setLoading(false)
    }
  }

  const goFull = () => {
    const query = q.trim()
    setOpen(false)
    navigate(query ? `/network/lookup?q=${encodeURIComponent(query)}` : '/network/lookup')
  }

  const arp = result?.arp ?? []
  const mac = result?.mac ?? []
  const hits = arp.length + mac.length
  const kind = detectQueryKind(q)

  return (
    <div className="relative" ref={wrap}>
      <button
        onClick={() => setOpen((o) => !o)}
        title="IP / MAC lookup"
        aria-label="IP / MAC lookup"
        className="p-1.5 rounded-md text-gray-500 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
      >
        🔍
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-40 p-3">
          <form onSubmit={run} className="flex gap-2">
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="IP or MAC…"
              className="flex-1 px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-md text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button type="submit" disabled={loading || !q.trim()}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium disabled:opacity-50">
              {loading ? '…' : 'Go'}
            </button>
          </form>
          {q.trim() && kind !== 'unknown' && (
            <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">looks like {kind === 'ip' ? 'an IP' : 'a MAC'}</p>
          )}

          {result && (
            <div className="mt-2 max-h-72 overflow-y-auto text-sm">
              {hits === 0 ? (
                <p className="text-gray-500 dark:text-gray-400 py-2">No device sees “{result.query}”.</p>
              ) : (
                <ul className="space-y-1">
                  {arp.map((e) => (
                    <li key={`a${e.id}`} className="text-gray-700 dark:text-gray-300">
                      <span className="text-gray-400">ARP</span>{' '}
                      <Link to={`/devices/${e.device_id}?tab=arpmac`} onClick={() => setOpen(false)} className="text-blue-600 dark:text-blue-400 hover:underline">{e.device_hostname}</Link>
                      <span className="text-gray-500"> · {e.ip_address}{e.interface ? ` · ${e.interface}` : ''}</span>
                    </li>
                  ))}
                  {mac.map((e) => (
                    <li key={`m${e.id}`} className="text-gray-700 dark:text-gray-300">
                      <span className="text-gray-400">MAC</span>{' '}
                      <Link to={`/devices/${e.device_id}?tab=arpmac`} onClick={() => setOpen(false)} className="text-blue-600 dark:text-blue-400 hover:underline">{e.device_hostname}</Link>
                      <span className="text-gray-500"> · port {e.interface || '?'}{e.vlan != null ? ` · VLAN ${e.vlan}` : ''}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <button onClick={goFull} className="mt-2 w-full text-center text-xs text-blue-600 dark:text-blue-400 hover:underline">
            View full results →
          </button>
        </div>
      )}
    </div>
  )
}
