import clsx from 'clsx'
import type { FlowRecord } from '../api/client'
import { fmtBytes } from '../lib/bytes'
import IPLink from './IPLink'

interface Props {
  rows: FlowRecord[]
  loading?: boolean
  // Clicking a src/dst IP — used to drill into "flows for this IP".
  onIpClick?: (ip: string) => void
  maxHeight?: string
}

// Per-protocol badge colour (TCP/UDP/ICMP and a neutral fallback).
function protoBadge(proto: string): string {
  switch ((proto || '').toUpperCase()) {
    case 'TCP':
      return 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
    case 'UDP':
      return 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300'
    case 'ICMP':
      return 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
    default:
      return 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
  }
}

export default function FlowsTable({ rows, loading, onIpClick, maxHeight = 'max-h-[34rem]' }: Props) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }
  if (rows.length === 0) {
    return <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-500">No flows in this window.</p>
  }

  // With an onIpClick handler the IP drills into "flows for this IP" in-place;
  // otherwise it links to the IP/MAC lookup page.
  const IpCell = ({ ip }: { ip: string }) =>
    onIpClick ? (
      <button
        onClick={() => onIpClick(ip)}
        className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline"
      >
        {ip}
      </button>
    ) : (
      <IPLink ip={ip} className="text-xs" />
    )

  return (
    <div className={clsx('overflow-x-auto', maxHeight)}>
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-gray-50 dark:bg-gray-900/50">
          <tr className="text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
            <th className="px-4 py-2 font-medium w-28">Time</th>
            <th className="px-4 py-2 font-medium">Src → Dst</th>
            <th className="px-4 py-2 font-medium w-32">Port</th>
            <th className="px-4 py-2 font-medium w-20">Proto</th>
            <th className="px-4 py-2 font-medium w-24 text-right">Bytes</th>
            <th className="px-4 py-2 font-medium w-20 text-right">Pkts</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 align-top">
              <td className="px-4 py-1.5 text-gray-500 dark:text-gray-400 font-mono text-xs whitespace-nowrap">
                {new Date(r.timestamp).toLocaleTimeString()}
              </td>
              <td className="px-4 py-1.5">
                <div className="flex items-center gap-1.5">
                  <IpCell ip={r.src_ip} />
                  <span className="text-gray-300 dark:text-gray-600">→</span>
                  <IpCell ip={r.dst_ip} />
                </div>
              </td>
              <td className="px-4 py-1.5 text-gray-600 dark:text-gray-400 text-xs">
                {r.dst_port > 0 ? (
                  <>
                    <span className="font-mono">{r.dst_port}</span>
                    {r.service && <span className="ml-1 text-gray-400 dark:text-gray-500">{r.service}</span>}
                  </>
                ) : (
                  '—'
                )}
              </td>
              <td className="px-4 py-1.5">
                <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', protoBadge(r.protocol))}>
                  {r.protocol}
                </span>
              </td>
              <td className="px-4 py-1.5 text-right font-mono text-xs text-gray-700 dark:text-gray-300 whitespace-nowrap">
                {fmtBytes(r.bytes)}
              </td>
              <td className="px-4 py-1.5 text-right font-mono text-xs text-gray-600 dark:text-gray-400">
                {(r.packets ?? 0).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
