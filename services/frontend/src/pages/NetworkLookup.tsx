import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { networkSearch, searchFlows, type NetworkSearchResult } from '../api/client'
import { detectQueryKind, relTime, latestCollected } from '../lib/ipmac'
import FlowsTable from '../components/FlowsTable'

const card = 'bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800'
const th = 'text-left px-3 py-2 font-semibold text-gray-500 dark:text-gray-400'
const td = 'px-3 py-2 text-gray-700 dark:text-gray-300'

// Dedicated "where is this host?" workflow — find the device that sees a given
// IP or MAC in its ARP or MAC address-table. Mirrors the header quick-search.
export default function NetworkLookup() {
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') ?? '')
  const [result, setResult] = useState<NetworkSearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  const run = useCallback(async (raw: string) => {
    const query = raw.trim()
    if (!query) return
    setLoading(true)
    setSearched(true)
    try {
      setResult(await networkSearch(query))
    } catch {
      setResult({ query, arp: [], mac: [] })
    } finally {
      setLoading(false)
    }
  }, [])

  // Auto-run when arriving with ?q= (e.g. from the header "View full results").
  useEffect(() => {
    const initial = params.get('q')
    if (initial) void run(initial)
    // run once on mount for the incoming query
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const query = q.trim()
    if (!query) return
    setParams(query ? { q: query } : {}, { replace: true })
    void run(query)
  }

  const kind = detectQueryKind(q)
  const arp = result?.arp ?? []
  const mac = result?.mac ?? []
  const hits = arp.length + mac.length
  const lastCollected = latestCollected([
    ...arp.map((e) => e.collected_at),
    ...mac.map((e) => e.collected_at),
  ])
  // A MAC table hit with a port is the "this host is plugged in here" answer.
  const portHit = mac.find((e) => e.interface)

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          🔍 IP / MAC Lookup
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Find any device by IP or MAC address.</p>
      </div>

      <div className={`${card} p-4`}>
        <form onSubmit={onSubmit} className="flex gap-2">
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. 10.150.0.50 or aa:bb:cc:dd:ee:ff"
            className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button type="submit" disabled={loading || !q.trim()}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {loading ? 'Searching…' : 'Search'}
          </button>
        </form>
        <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
          Accepts: IPv4 · MAC (any format)
          {q.trim() && kind !== 'unknown' && (
            <span className="ml-2 text-gray-500 dark:text-gray-400">— looks like {kind === 'ip' ? 'an IP address' : 'a MAC address'}</span>
          )}
        </p>
      </div>

      {searched && !loading && result && (
        <div className={`${card} p-4 space-y-5`}>
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Results for <span className="font-mono text-base">{result.query}</span>
            </h2>
            {hits > 0 && (
              <span className="text-xs text-gray-400 dark:text-gray-500">Last collected: {relTime(lastCollected)}</span>
            )}
          </div>

          {hits === 0 ? (
            <EmptyResults />
          ) : (
            <>
              {portHit && (
                <div className="rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 px-4 py-3 text-sm text-blue-800 dark:text-blue-300">
                  📍 This device is on port <span className="font-mono font-medium">{portHit.interface}</span>
                  {' '}on <Link to={`/devices/${portHit.device_id}`} className="font-medium underline hover:no-underline">{portHit.device_hostname}</Link>
                  {portHit.vlan != null && <> · VLAN {portHit.vlan}</>}
                </div>
              )}

              {arp.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">ARP Table Matches</h3>
                  <div className={`${card} overflow-x-auto`}>
                    <table className="w-full text-sm">
                      <thead className="border-b border-gray-200 dark:border-gray-800">
                        <tr><th className={th}>Device</th><th className={th}>IP Address</th><th className={th}>MAC</th><th className={th}>Interface</th></tr>
                      </thead>
                      <tbody>
                        {arp.map((e) => (
                          <tr key={`a${e.id}`} className="border-b border-gray-50 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/40">
                            <td className={td}><DeviceLink id={e.device_id} name={e.device_hostname} /></td>
                            <td className={`${td} font-mono text-xs`}>{e.ip_address}</td>
                            <td className={td}>
                              <span className="font-mono text-xs">{e.mac_address}</span>
                              {e.vendor && <span className="block text-xs text-gray-400 dark:text-gray-500">{e.vendor}</span>}
                            </td>
                            <td className={td}>{e.interface || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              {mac.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">MAC Table Matches</h3>
                  <div className={`${card} overflow-x-auto`}>
                    <table className="w-full text-sm">
                      <thead className="border-b border-gray-200 dark:border-gray-800">
                        <tr><th className={th}>Device</th><th className={th}>MAC</th><th className={th}>VLAN</th><th className={th}>Type</th><th className={th}>Port</th></tr>
                      </thead>
                      <tbody>
                        {mac.map((e) => (
                          <tr key={`m${e.id}`} className="border-b border-gray-50 dark:border-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800/40">
                            <td className={td}><DeviceLink id={e.device_id} name={e.device_hostname} /></td>
                            <td className={td}>
                              <span className="font-mono text-xs">{e.mac_address}</span>
                              {e.vendor && <span className="block text-xs text-gray-400 dark:text-gray-500">{e.vendor}</span>}
                            </td>
                            <td className={td}>{e.vlan ?? '—'}</td>
                            <td className={td}>{e.entry_type || '—'}</td>
                            <td className={td}>
                              <Link to={`/devices/${e.device_id}?tab=arpmac`} className="text-blue-600 dark:text-blue-400 hover:underline">
                                {e.interface || '—'} →
                              </Link>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      )}

      {searched && !loading && result && detectQueryKind(result.query) === 'ip' && (
        <IpFlows ip={result.query} />
      )}
    </div>
  )
}

// "Recent flows involving this IP" — flows where the IP is source OR destination
// (last 24h). Only shown for IP lookups.
function IpFlows({ ip }: { ip: string }) {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['lookup-flows', ip],
    queryFn: () => searchFlows(ip, '24h', 100),
  })
  const rows = data?.results ?? []

  if (!isLoading && rows.length === 0) return null

  return (
    <div className={`${card}`}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Recent flows involving this IP</h3>
        <button
          onClick={() => navigate(`/flows`)}
          className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
        >
          Open Flow Analytics →
        </button>
      </div>
      <FlowsTable rows={rows} loading={isLoading} maxHeight="max-h-[24rem]" />
      <div className="px-4 py-2 text-xs text-gray-400 dark:text-gray-500 border-t border-gray-100 dark:border-gray-800">
        {rows.length.toLocaleString()} of {(data?.count ?? 0).toLocaleString()} flows · last 24h
      </div>
    </div>
  )
}

function DeviceLink({ id, name }: { id: number; name: string }) {
  return <Link to={`/devices/${id}`} className="text-blue-600 dark:text-blue-400 font-medium hover:underline">{name}</Link>
}

function EmptyResults() {
  return (
    <div className="text-center py-8 text-sm text-gray-500 dark:text-gray-400 space-y-2">
      <p className="text-gray-600 dark:text-gray-300 font-medium">No results found.</p>
      <p>
        No device has this IP or MAC in a collected ARP/MAC table. ARP/MAC tables are
        collected on a schedule (every 6 hours) or on demand.
      </p>
      <p className="text-gray-400 dark:text-gray-500">
        Try collecting now: open a device → <span className="font-medium">ARP / MAC</span> tab → <span className="font-medium">Collect Now</span>.
      </p>
    </div>
  )
}
