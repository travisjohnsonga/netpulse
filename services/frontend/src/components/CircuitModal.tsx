import { useEffect, useState } from 'react'
import Modal from './Modal'
import {
  fetchDevices, fetchSites, fetchMonitoredInterfaces, createCircuit, updateCircuit,
  type Device, type Site, type WanCircuit, type CircuitType, type CircuitStatus,
} from '../api/client'

const CIRCUIT_TYPES: { value: CircuitType; label: string }[] = [
  { value: 'internet', label: 'Internet' }, { value: 'dia', label: 'Dedicated Internet Access' },
  { value: 'mpls', label: 'MPLS' }, { value: 'broadband', label: 'Broadband' },
  { value: 'fiber', label: 'Fiber' }, { value: 'coax', label: 'Coax/Cable' },
  { value: 'lte', label: 'LTE/Cellular' }, { value: 'sdwan', label: 'SD-WAN' },
  { value: 'dark_fiber', label: 'Dark Fiber' }, { value: 'p2p', label: 'Point-to-Point' },
  { value: 'other', label: 'Other' },
]
const STATUSES: { value: CircuitStatus; label: string }[] = [
  { value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' },
  { value: 'pending', label: 'Pending Install' }, { value: 'cancelled', label: 'Cancelled' },
]

const inp = 'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
const lbl = 'block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1'

export default function CircuitModal({ onClose, onSaved, edit, prefillSite }: {
  onClose: () => void
  onSaved: () => void
  edit?: WanCircuit
  prefillSite?: number
}) {
  const [devices, setDevices] = useState<Device[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [ifaces, setIfaces] = useState<string[]>([])
  const [f, setF] = useState({
    name: edit?.name ?? '', circuit_id: edit?.circuit_id ?? '',
    circuit_type: edit?.circuit_type ?? 'internet' as CircuitType,
    status: edit?.status ?? 'active' as CircuitStatus,
    provider: edit?.provider ?? '', contract_end_date: edit?.contract_end_date ?? '',
    monthly_cost: edit?.monthly_cost ?? '',
    bandwidth_mbps_download: edit?.bandwidth_mbps_download?.toString() ?? '',
    bandwidth_mbps_upload: edit?.bandwidth_mbps_upload?.toString() ?? '',
    committed_mbps: edit?.committed_mbps?.toString() ?? '',
    alert_threshold_pct: edit?.alert_threshold_pct?.toString() ?? '80',
    site: edit?.site?.toString() ?? prefillSite?.toString() ?? '',
    device: edit?.device?.toString() ?? '', interface: edit?.interface ?? '',
    ip_address: edit?.ip_address ?? '',
    isp_ipv4_block: edit?.isp_ipv4_block ?? '', isp_ipv6_block: edit?.isp_ipv6_block ?? '',
    gateway_ip: edit?.gateway_ip ?? '', bgp_asn: edit?.bgp_asn ?? '', our_bgp_asn: edit?.our_bgp_asn ?? '',
    notes: edit?.notes ?? '',
  })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const set = (k: keyof typeof f, v: string) => setF((p) => ({ ...p, [k]: v }))

  useEffect(() => {
    fetchDevices({ page_size: '500' }).then((d) => setDevices(d.results)).catch(() => {})
    fetchSites().then(setSites).catch(() => {})
  }, [])
  useEffect(() => {
    if (!f.device) { setIfaces([]); return }
    fetchMonitoredInterfaces(Number(f.device))
      .then((r) => setIfaces(r.map((x) => x.if_name).filter(Boolean))).catch(() => setIfaces([]))
  }, [f.device])

  const num = (v: string) => (v === '' ? null : Number(v))
  const save = async () => {
    setError(null)
    if (!f.name.trim()) { setError('Name is required.'); return }
    setSaving(true)
    const payload = {
      name: f.name, circuit_id: f.circuit_id, circuit_type: f.circuit_type, status: f.status,
      provider: f.provider, contract_end_date: f.contract_end_date || null,
      monthly_cost: f.monthly_cost || null,
      bandwidth_mbps_download: num(f.bandwidth_mbps_download),
      bandwidth_mbps_upload: num(f.bandwidth_mbps_upload),
      committed_mbps: num(f.committed_mbps),
      alert_threshold_pct: Number(f.alert_threshold_pct || 80),
      site: f.site ? Number(f.site) : null, device: f.device ? Number(f.device) : null,
      interface: f.interface, ip_address: f.ip_address || null,
      isp_ipv4_block: f.isp_ipv4_block, isp_ipv6_block: f.isp_ipv6_block,
      gateway_ip: f.gateway_ip || null, bgp_asn: f.bgp_asn, our_bgp_asn: f.our_bgp_asn,
      notes: f.notes,
    }
    try {
      if (edit) await updateCircuit(edit.id, payload); else await createCircuit(payload)
      onSaved()
    } catch (e) {
      const resp = (e as { response?: { data?: Record<string, unknown> } }).response
      setError(resp?.data ? Object.entries(resp.data).map(([k, v]) => `${k}: ${v}`).join(' ') : 'Could not save.')
    } finally { setSaving(false) }
  }

  return (
    <Modal onClose={onClose} title={edit ? 'Edit WAN Circuit' : 'Add WAN Circuit'} size="lg">
      <div className="space-y-4">
        {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{error}</div>}

        <div className="grid sm:grid-cols-2 gap-3">
          <div><label className={lbl}>Name *</label><input className={inp} value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="WCO2 Primary Internet" /></div>
          <div><label className={lbl}>Circuit ID</label><input className={inp} value={f.circuit_id} onChange={(e) => set('circuit_id', e.target.value)} placeholder="ATTD-12345678" /></div>
          <div><label className={lbl}>Type</label><select className={inp} value={f.circuit_type} onChange={(e) => set('circuit_type', e.target.value)}>{CIRCUIT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></div>
          <div><label className={lbl}>Status</label><select className={inp} value={f.status} onChange={(e) => set('status', e.target.value)}>{STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select></div>
          <div><label className={lbl}>Provider</label><input className={inp} value={f.provider} onChange={(e) => set('provider', e.target.value)} placeholder="AT&T" /></div>
          <div><label className={lbl}>Contract End</label><input className={inp} type="date" value={f.contract_end_date ?? ''} onChange={(e) => set('contract_end_date', e.target.value)} /></div>
          <div><label className={lbl}>Monthly Cost ($)</label><input className={inp} type="number" step="0.01" value={f.monthly_cost} onChange={(e) => set('monthly_cost', e.target.value)} placeholder="1200" /></div>
          <div><label className={lbl}>Alert at (% util)</label><input className={inp} type="number" value={f.alert_threshold_pct} onChange={(e) => set('alert_threshold_pct', e.target.value)} /></div>
          <div><label className={lbl}>Download (Mbps)</label><input className={inp} type="number" value={f.bandwidth_mbps_download} onChange={(e) => set('bandwidth_mbps_download', e.target.value)} placeholder="1000" /></div>
          <div><label className={lbl}>Upload (Mbps)</label><input className={inp} type="number" value={f.bandwidth_mbps_upload} onChange={(e) => set('bandwidth_mbps_upload', e.target.value)} placeholder="blank = symmetric" /></div>
          <div><label className={lbl}>CIR (Mbps)</label><input className={inp} type="number" value={f.committed_mbps} onChange={(e) => set('committed_mbps', e.target.value)} placeholder="optional" /></div>
        </div>

        <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
          <p className="text-xs font-semibold text-gray-600 dark:text-gray-300 mb-2">Interface Binding</p>
          <div className="grid sm:grid-cols-2 gap-3">
            <div><label className={lbl}>Site</label><select className={inp} value={f.site} onChange={(e) => set('site', e.target.value)}><option value="">—</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></div>
            <div><label className={lbl}>Device</label><select className={inp} value={f.device} onChange={(e) => set('device', e.target.value)}><option value="">—</option>{devices.map((d) => <option key={d.id} value={d.id}>{d.hostname}</option>)}</select></div>
            <div><label className={lbl}>Interface</label><input className={inp} list="circuit-iface" value={f.interface} onChange={(e) => set('interface', e.target.value)} placeholder="GigabitEthernet0/0/0" /><datalist id="circuit-iface">{ifaces.map((n) => <option key={n} value={n} />)}</datalist></div>
            <div><label className={lbl}>WAN IP</label><input className={inp} value={f.ip_address ?? ''} onChange={(e) => set('ip_address', e.target.value)} placeholder="203.0.113.1" /></div>
          </div>
        </div>

        <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
          <p className="text-xs font-semibold text-gray-600 dark:text-gray-300 mb-2">ISP IP Assignment</p>
          <div className="grid sm:grid-cols-2 gap-3">
            <div><label className={lbl}>IPv4 Block</label><input className={inp} value={f.isp_ipv4_block} onChange={(e) => set('isp_ipv4_block', e.target.value)} placeholder="203.0.113.0/30" /></div>
            <div><label className={lbl}>IPv6 Block</label><input className={inp} value={f.isp_ipv6_block} onChange={(e) => set('isp_ipv6_block', e.target.value)} placeholder="2001:db8::/48" /></div>
            <div><label className={lbl}>Gateway IP</label><input className={inp} value={f.gateway_ip ?? ''} onChange={(e) => set('gateway_ip', e.target.value)} placeholder="203.0.113.2" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className={lbl}>BGP ASN (ISP)</label><input className={inp} value={f.bgp_asn} onChange={(e) => set('bgp_asn', e.target.value)} placeholder="7018" /></div>
              <div><label className={lbl}>BGP ASN (ours)</label><input className={inp} value={f.our_bgp_asn} onChange={(e) => set('our_bgp_asn', e.target.value)} placeholder="65001" /></div>
            </div>
          </div>
        </div>

        <div><label className={lbl}>Notes</label><input className={inp} value={f.notes} onChange={(e) => set('notes', e.target.value)} /></div>

        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">Cancel</button>
          <button onClick={save} disabled={saving} className="px-4 py-2 text-sm rounded-lg font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50">{saving ? 'Saving…' : 'Save Circuit'}</button>
        </div>
      </div>
    </Modal>
  )
}
