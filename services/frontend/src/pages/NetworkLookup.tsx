import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { networkHosts, downloadNetworkHostsCsv, searchFlows, type NetworkHost, type NetworkHostSort } from '../api/client'
import { detectQueryKind, relTime } from '../lib/ipmac'
import { STRIPED_ROW } from '../lib/tableStyles'
import FlowsTable from '../components/FlowsTable'
import DeviceLink from '../components/DeviceLink'

const card = 'bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800'
const th = 'text-left px-3 py-2 font-semibold text-gray-500 dark:text-gray-400 whitespace-nowrap'
const td = 'px-3 py-2 text-gray-700 dark:text-gray-300 align-top'
const PAGE_SIZES = [50, 100, 200]
const DEFAULT_PAGE_SIZE = 100

const COLUMNS: { key: NetworkHostSort; label: string }[] = [
  { key: 'mac', label: 'MAC' },
  { key: 'vendor', label: 'Vendor' },
  { key: 'ip', label: 'IP Address(es)' },
  { key: 'device', label: 'Device' },
  { key: 'port', label: 'Port' },
  { key: 'vlan', label: 'VLAN' },
  { key: 'type', label: 'Type' },
  { key: 'last_seen', label: 'Last Seen' },
]

// Unified, browsable IP/MAC table — one row per MAC across the whole fleet,
// joining ARP (L3) and MAC-table (L2 switchport) sightings. Default-all,
// sortable, paginated. q/page/sort/order live in the URL. The header
// quick-search still hits /network/search/ separately.
export default function NetworkLookup() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const sort = (params.get('sort') as NetworkHostSort) || 'mac'
  const order = params.get('order') === 'desc' ? 'desc' : 'asc'
  const page = Math.max(1, Number(params.get('page')) || 1)
  // Hide hosts only seen on uplinks (lag/ae/Po/vtep) — no access port.
  const hideUplinks = params.get('uplinks') === 'hide'
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [qInput, setQInput] = useState(q)
  const [exporting, setExporting] = useState(false)
  const [exportErr, setExportErr] = useState<string | null>(null)

  // Merge a partial change into the URL params (kept as the single source of truth).
  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === '') next.delete(k)
      else next.set(k, v)
    }
    setParams(next, { replace: true })
  }

  // Keep the search box in sync if q changes from elsewhere (e.g. header link).
  useEffect(() => { setQInput(q) }, [q])

  // Debounce typing → q param, resetting to page 1 on a new filter.
  useEffect(() => {
    if (qInput === q) return
    const t = setTimeout(() => update({ q: qInput || null, page: null }), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qInput])

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['network-hosts', q, sort, order, page, pageSize, hideUplinks],
    queryFn: () => networkHosts({ q, sort, order, page, page_size: pageSize, exclude_uplinks: hideUplinks }),
    placeholderData: keepPreviousData,
  })

  const rows = data?.results ?? []
  const count = data?.count ?? 0
  const numPages = data?.num_pages ?? 1

  const onSort = (key: NetworkHostSort) => {
    const nextOrder = sort === key && order === 'asc' ? 'desc' : 'asc'
    update({ sort: key, order: nextOrder, page: null })
  }

  const kind = detectQueryKind(q.trim())

  // Export every matching host (all pages) with the current filter + sort.
  const onExport = async () => {
    setExporting(true); setExportErr(null)
    try {
      await downloadNetworkHostsCsv({ q, sort, order, exclude_uplinks: hideUplinks })
    } catch {
      setExportErr('Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          🔍 IP / MAC Lookup
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Every host seen across the fleet's ARP and MAC address-tables — one row per MAC.
        </p>
      </div>

      <div className={`${card} p-4 flex flex-wrap items-center gap-3`}>
        <input
          autoFocus
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          placeholder="Filter by MAC, vendor, IP, device, or port…"
          className="flex-1 min-w-[16rem] px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {qInput && (
          <button onClick={() => setQInput('')}
            className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200">
            Clear
          </button>
        )}
        <label className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          Show
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); update({ page: null }) }}
            className="px-2 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          >
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300"
          title="Hide hosts only seen on uplink/trunk ports (lagN, aeN, Port-channelN, vtep)">
          <input type="checkbox" checked={hideUplinks}
            onChange={(e) => update({ uplinks: e.target.checked ? 'hide' : null, page: null })} />
          Hide uplinks
        </label>
        <button onClick={onExport} disabled={exporting || count === 0}
          className="px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-lg text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800/50 disabled:opacity-40 disabled:cursor-not-allowed">
          {exporting ? 'Exporting…' : `Export CSV${count ? ` (${count.toLocaleString()})` : ''}`}
        </button>
        {exportErr && <span className="text-sm text-red-600 dark:text-red-400">{exportErr}</span>}
      </div>

      <div className={card}>
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-200 dark:border-gray-800 text-xs text-gray-500 dark:text-gray-400">
          <span>
            {count.toLocaleString()} host{count !== 1 ? 's' : ''}
            {isFetching && <span className="ml-2 text-gray-400">· updating…</span>}
          </span>
          {data?.last_collected && <span>Last collected: {relTime(data.last_collected)}</span>}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-gray-200 dark:border-gray-800">
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key} className={`${th} cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-200`}
                      onClick={() => onSort(c.key)}>
                    {c.label}
                    <span className="ml-1 inline-block w-2 text-gray-400">
                      {sort === c.key ? (order === 'asc' ? '▲' : '▼') : ''}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr><td className="px-3 py-10 text-center text-gray-400" colSpan={COLUMNS.length}>Loading…</td></tr>
              ) : rows.length === 0 ? (
                <tr><td className="px-3 py-10 text-center text-gray-400" colSpan={COLUMNS.length}>
                  {q ? 'No hosts match this filter.' : 'No ARP/MAC data collected yet.'}
                </td></tr>
              ) : (
                rows.map((h) => <HostRow key={h.mac} h={h} />)
              )}
            </tbody>
          </table>
        </div>

        {/* Pager */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-800 text-sm">
          <span className="text-gray-500 dark:text-gray-400">
            {count.toLocaleString()} total
          </span>
          <div className="flex items-center gap-3">
            <button
              onClick={() => update({ page: String(page - 1) })}
              disabled={page <= 1}
              className="px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md hover:bg-gray-50 dark:hover:bg-gray-800/50 disabled:opacity-40 disabled:cursor-not-allowed text-gray-700 dark:text-gray-300"
            >
              ← Prev
            </button>
            <span className="text-gray-500 dark:text-gray-400">page {page} of {numPages}</span>
            <button
              onClick={() => update({ page: String(page + 1) })}
              disabled={page >= numPages}
              className="px-3 py-1.5 border border-gray-300 dark:border-gray-700 rounded-md hover:bg-gray-50 dark:hover:bg-gray-800/50 disabled:opacity-40 disabled:cursor-not-allowed text-gray-700 dark:text-gray-300"
            >
              Next →
            </button>
          </div>
        </div>
      </div>

      {/* Recent flows panel — only when the filter is a single IP. */}
      {kind === 'ip' && <IpFlows ip={q.trim()} />}
    </div>
  )
}

function HostRow({ h }: { h: NetworkHost }) {
  return (
    <tr className={STRIPED_ROW}>
      <td className={`${td} font-mono text-xs`}>{h.mac}</td>
      <td className={`${td} text-gray-500 dark:text-gray-400`}>{h.vendor || '—'}</td>
      <td className={`${td} font-mono text-xs`}>
        {h.ips.length ? h.ips.map((ip) => <div key={ip}>{ip}</div>) : '—'}
      </td>
      <td className={td}>
        {h.device_id != null
          ? <DeviceLink deviceId={h.device_id} hostname={h.device} className="font-medium" />
          : (h.device || '—')}
      </td>
      <td className={td}>{h.port || '—'}</td>
      <td className={td}>{h.vlan ?? '—'}</td>
      <td className={td}>{h.entry_type || '—'}</td>
      <td className={`${td} whitespace-nowrap text-gray-500 dark:text-gray-400`}>{relTime(h.last_seen)}</td>
    </tr>
  )
}

// "Recent flows involving this IP" — flows where the IP is source OR destination
// (last 24h). Only shown when the filter is a single IP.
function IpFlows({ ip }: { ip: string }) {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['lookup-flows', ip],
    queryFn: () => searchFlows(ip, '24h', 100),
  })
  const rows = data?.results ?? []

  if (!isLoading && rows.length === 0) return null

  return (
    <div className={card}>
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
