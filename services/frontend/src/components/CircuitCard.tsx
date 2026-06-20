import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchCircuitUtilization, type WanCircuit, type CircuitUtilization,
} from '../api/client'

const STATUS_DOT: Record<string, string> = {
  active: 'bg-green-500', inactive: 'bg-gray-400', pending: 'bg-amber-500', cancelled: 'bg-red-500',
}

function fmtBw(mbps: number | null | undefined): string {
  if (!mbps) return '—'
  return mbps >= 1000 ? `${(mbps / 1000).toString()} Gbps` : `${mbps} Mbps`
}

function UtilBar({ label, mbps, pct, arrow }: { label: string; mbps: number | null | undefined; pct: number | null | undefined; arrow: string }) {
  const p = Math.min(100, Math.max(0, pct ?? 0))
  const color = p >= 90 ? 'bg-red-500' : p >= 75 ? 'bg-orange-500' : p >= 50 ? 'bg-yellow-500' : 'bg-green-500'
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-4 text-gray-400">{arrow}</span>
      <span className="w-20 font-mono text-gray-700 dark:text-gray-300">{mbps != null ? `${mbps.toFixed(0)} Mbps` : '—'}</span>
      <span className="w-12 font-mono text-gray-500">{pct != null ? `${pct.toFixed(1)}%` : '—'}</span>
      <div className="flex-1 h-2 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${p}%` }} title={label} />
      </div>
    </div>
  )
}

function monthsUntil(date: string): string {
  const d = new Date(date), now = new Date()
  const months = (d.getFullYear() - now.getFullYear()) * 12 + (d.getMonth() - now.getMonth())
  if (months < 0) return 'expired'
  return months < 1 ? '<1 mo' : `${months} mo`
}

export default function CircuitCard({ circuit, onEdit, onDelete }: {
  circuit: WanCircuit
  onEdit?: (c: WanCircuit) => void
  onDelete?: (c: WanCircuit) => void
}) {
  const [util, setUtil] = useState<CircuitUtilization | null>(null)
  useEffect(() => {
    let cancelled = false
    fetchCircuitUtilization(circuit.id).then((u) => { if (!cancelled) setUtil(u) }).catch(() => {})
    return () => { cancelled = true }
  }, [circuit.id])

  const bound = util?.bound
  const cur = util?.current
  const c = circuit
  const contractSoon = c.contract_end_date && monthsUntil(c.contract_end_date).includes('mo') &&
    parseInt(monthsUntil(c.contract_end_date)) <= 3

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 space-y-2">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className={clsx('w-2.5 h-2.5 rounded-full', STATUS_DOT[c.status] ?? 'bg-gray-400')} />
            <h3 className="font-semibold text-gray-900 dark:text-gray-100">{c.name}</h3>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {[c.provider, c.circuit_type_display,
              `${fmtBw(c.bandwidth_mbps_download)}${c.bandwidth_mbps_upload && c.bandwidth_mbps_upload !== c.bandwidth_mbps_download ? ` / ${fmtBw(c.bandwidth_mbps_upload)}` : ''}`]
              .filter(Boolean).join(' | ')}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="uppercase font-medium text-gray-400">{c.status_display}</span>
          {onEdit && <button onClick={() => onEdit(c)} className="text-blue-600 dark:text-blue-400 hover:text-blue-800" title="Edit">✏️</button>}
          {onDelete && <button onClick={() => onDelete(c)} className="text-red-600 dark:text-red-400 hover:text-red-800" title="Delete">🗑</button>}
        </div>
      </div>

      {c.circuit_id && <p className="text-xs text-gray-500 dark:text-gray-400">Circuit ID: <span className="font-mono">{c.circuit_id}</span></p>}
      {c.device_hostname && (
        <p className="text-xs text-gray-600 dark:text-gray-300">
          {c.device_hostname}{c.interface ? <span className="text-gray-400"> → {c.interface}</span> : ''}
        </p>
      )}

      {(c.isp_ipv4_block || c.ip_address || c.gateway_ip || c.isp_ipv6_block) && (
        <div className="text-xs text-gray-500 dark:text-gray-400 grid grid-cols-2 gap-x-3 font-mono">
          {c.isp_ipv4_block && <span>ISP: {c.isp_ipv4_block}</span>}
          {c.ip_address && <span>Our IP: {c.ip_address}</span>}
          {c.gateway_ip && <span>GW: {c.gateway_ip}</span>}
          {c.isp_ipv6_block && <span>IPv6: {c.isp_ipv6_block}</span>}
        </div>
      )}

      {bound && cur ? (
        <div className="space-y-1 pt-1">
          <UtilBar label="Download" arrow="↓" mbps={cur.rx_mbps} pct={cur.rx_pct} />
          <UtilBar label="Upload" arrow="↑" mbps={cur.tx_mbps} pct={cur.tx_pct} />
          {util?.p95?.rx_mbps != null && (
            <p className="text-xs text-gray-400 pt-0.5">
              P95 24h: ↓ {util.p95.rx_mbps?.toFixed(0)} Mbps ({util.p95.rx_pct?.toFixed(1)}%)
            </p>
          )}
        </div>
      ) : (
        <p className="text-xs text-gray-400 pt-1">{c.device_hostname ? 'No utilization data yet.' : 'Bind a device + interface for utilization.'}</p>
      )}

      <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 pt-1 border-t border-gray-100 dark:border-gray-700">
        {c.contract_end_date
          ? <span className={contractSoon ? 'text-amber-600 dark:text-amber-400 font-medium' : ''}>
              Contract ends: {new Date(c.contract_end_date).toLocaleDateString()} ({monthsUntil(c.contract_end_date)})
            </span>
          : <span />}
        {c.monthly_cost && <span>${Number(c.monthly_cost).toLocaleString()}/mo</span>}
      </div>
    </div>
  )
}
