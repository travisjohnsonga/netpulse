import { useState } from 'react'
import { Link } from 'react-router-dom'
import { networkSearch, type NetworkSearchResult } from '../api/client'

// "Where is this host plugged in?" — find which device sees a given IP or MAC
// in its ARP or MAC table. Helpdesk-oriented global lookup.
export default function NetworkSearch() {
  const [q, setQ] = useState('')
  const [result, setResult] = useState<NetworkSearchResult | null>(null)
  const [loading, setLoading] = useState(false)

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

  const hits = result ? result.arp.length + result.mac.length : 0

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <form onSubmit={run} className="flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Find host by IP or MAC (e.g. 10.150.0.21 or aa:bb:cc:dd:ee:ff)…"
          className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button type="submit" disabled={loading}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

      {result && (
        <div className="mt-3 text-sm">
          {hits === 0 ? (
            <p className="text-gray-500 dark:text-gray-400">No device has “{result.query}” in its ARP or MAC table.</p>
          ) : (
            <div className="space-y-1">
              {result.arp.map((e) => (
                <div key={`a${e.id}`} className="text-gray-700 dark:text-gray-300">
                  <span className="text-gray-400">ARP</span>{' '}
                  <span className="font-mono text-xs">{e.ip_address}</span> →{' '}
                  <span className="font-mono text-xs">{e.mac_address}</span>
                  {e.vendor && <span className="text-gray-400"> ({e.vendor})</span>} on{' '}
                  <Link to={`/devices/${e.device_id}?tab=arpmac`} className="text-blue-600 hover:underline">{e.device_hostname}</Link>
                  {e.interface && <span className="text-gray-500"> · {e.interface}</span>}
                </div>
              ))}
              {result.mac.map((e) => (
                <div key={`m${e.id}`} className="text-gray-700 dark:text-gray-300">
                  <span className="text-gray-400">MAC</span>{' '}
                  <span className="font-mono text-xs">{e.mac_address}</span>
                  {e.vendor && <span className="text-gray-400"> ({e.vendor})</span>} on{' '}
                  <Link to={`/devices/${e.device_id}?tab=arpmac`} className="text-blue-600 hover:underline">{e.device_hostname}</Link>
                  <span className="text-gray-500"> · port {e.interface || '?'}{e.vlan != null ? ` · VLAN ${e.vlan}` : ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
