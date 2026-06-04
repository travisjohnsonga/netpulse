import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchDeviceArp, fetchDeviceMac, collectDeviceArpMac,
  type DeviceDetail, type ArpEntry, type MacEntry,
} from '../../api/client'

function relTime(iso: string | null): string {
  if (!iso) return 'never'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const card = 'bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800'
const input = 'rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-1.5 text-sm'
const th = 'text-left px-3 py-2 font-semibold text-gray-500 dark:text-gray-400'
const td = 'px-3 py-1.5 text-gray-700 dark:text-gray-300'

function MacCell({ mac, vendor }: { mac: string; vendor: string }) {
  return (
    <span className="font-mono text-xs">
      {mac}
      {vendor && <span className="ml-2 text-gray-400 dark:text-gray-500 font-sans">{vendor}</span>}
    </span>
  )
}

export default function ArpMac({ device }: { device: DeviceDetail }) {
  const [sub, setSub] = useState<'arp' | 'mac'>('arp')
  const [arp, setArp] = useState<ArpEntry[]>([])
  const [mac, setMac] = useState<MacEntry[]>([])
  const [lastArp, setLastArp] = useState<string | null>(null)
  const [lastMac, setLastMac] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [vlan, setVlan] = useState('')
  const [loading, setLoading] = useState(true)
  const [collecting, setCollecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      if (sub === 'arp') {
        const r = await fetchDeviceArp(device.id, search)
        setArp(r.results); setLastArp(r.last_collected)
      } else {
        const r = await fetchDeviceMac(device.id, { search, vlan })
        setMac(r.results); setLastMac(r.last_collected)
      }
    } catch {
      setError('Failed to load. ARP/MAC collection may not have run yet.')
    } finally {
      setLoading(false)
    }
  }, [device.id, sub, search, vlan])

  useEffect(() => { void load() }, [load])

  const collectNow = async () => {
    setCollecting(true)
    setError(null)
    try {
      // Collection runs in the background on the server; poll the tables a few
      // times so the freshly collected rows appear without a manual refresh.
      await collectDeviceArpMac(device.id)
      for (let i = 0; i < 6; i++) {
        await new Promise((r) => setTimeout(r, 5000))
        await load()
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
      setError(msg || 'Collection failed (check SSH credentials and reachability).')
    } finally {
      setCollecting(false)
    }
  }

  const lastCollected = sub === 'arp' ? lastArp : lastMac

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex gap-1">
          {(['arp', 'mac'] as const).map((s) => (
            <button key={s} onClick={() => setSub(s)}
              className={clsx('px-3 py-1.5 text-sm rounded-md',
                sub === s ? 'bg-blue-600 text-white' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800')}>
              {s === 'arp' ? 'ARP Table' : 'MAC Table'}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-gray-400 dark:text-gray-500">Last collected: {relTime(lastCollected)}</span>
          <button onClick={collectNow} disabled={collecting}
            className="px-3 py-1.5 rounded-md bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50">
            {collecting ? 'Collecting…' : 'Collect Now'}
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <input className={input} placeholder={sub === 'arp' ? 'Search IP or MAC…' : 'Search MAC…'}
          value={search} onChange={(e) => setSearch(e.target.value)} />
        {sub === 'mac' && (
          <input className={clsx(input, 'w-28')} placeholder="VLAN" value={vlan}
            onChange={(e) => setVlan(e.target.value.replace(/[^0-9]/g, ''))} />
        )}
      </div>

      {error && <div className="text-sm text-amber-600 dark:text-amber-400">{error}</div>}

      <div className={clsx(card, 'overflow-x-auto')}>
        {loading ? (
          <div className="p-6 text-center text-gray-400 text-sm">Loading…</div>
        ) : sub === 'arp' ? (
          arp.length === 0 ? <Empty /> : (
            <table className="w-full text-sm">
              <thead className="border-b border-gray-200 dark:border-gray-800">
                <tr><th className={th}>IP Address</th><th className={th}>MAC Address</th>
                  <th className={th}>Age</th><th className={th}>Interface</th></tr>
              </thead>
              <tbody>
                {arp.map((e) => (
                  <tr key={e.id} className="border-b border-gray-50 dark:border-gray-800/50">
                    <td className={clsx(td, 'font-mono text-xs')}>{e.ip_address}</td>
                    <td className={td}><MacCell mac={e.mac_address} vendor={e.vendor} /></td>
                    <td className={td}>{e.age_minutes != null ? `${e.age_minutes}m` : '—'}</td>
                    <td className={td}>{e.interface || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          mac.length === 0 ? <Empty /> : (
            <table className="w-full text-sm">
              <thead className="border-b border-gray-200 dark:border-gray-800">
                <tr><th className={th}>MAC Address</th><th className={th}>VLAN</th>
                  <th className={th}>Type</th><th className={th}>Port</th></tr>
              </thead>
              <tbody>
                {mac.map((e) => (
                  <tr key={e.id} className="border-b border-gray-50 dark:border-gray-800/50">
                    <td className={td}><MacCell mac={e.mac_address} vendor={e.vendor} /></td>
                    <td className={td}>{e.vlan ?? '—'}</td>
                    <td className={td}>{e.entry_type}</td>
                    <td className={td}>{e.interface || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  )
}

function Empty() {
  return (
    <div className="p-8 text-center text-gray-400 dark:text-gray-500 text-sm">
      No entries. Click <span className="font-medium">Collect Now</span> to pull the table over SSH,
      or wait for the scheduled 6-hour collection.
    </div>
  )
}
