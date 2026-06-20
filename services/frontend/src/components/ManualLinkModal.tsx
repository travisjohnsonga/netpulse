import { useEffect, useState } from 'react'
import Modal from './Modal'
import {
  fetchDevices, fetchMonitoredInterfaces, createManualLink, updateManualLink,
  type Device, type ManualTopologyLink, type ManualLinkType,
} from '../api/client'

const LINK_TYPES: { value: ManualLinkType; label: string }[] = [
  { value: 'ethernet', label: 'Ethernet' },
  { value: 'fiber', label: 'Fiber' },
  { value: 'wan', label: 'WAN Circuit' },
  { value: 'lacp', label: 'LACP/LAG' },
  { value: 'mgmt', label: 'Management' },
  { value: 'virtual', label: 'Virtual/Tunnel' },
  { value: 'other', label: 'Other' },
]

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
const label = 'block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1'

// Per-device interface name cache for the "browse" datalists.
function useInterfaceNames(deviceId: number | '') {
  const [names, setNames] = useState<string[]>([])
  useEffect(() => {
    if (!deviceId) { setNames([]); return }
    let cancelled = false
    fetchMonitoredInterfaces(Number(deviceId))
      .then((rows) => { if (!cancelled) setNames(rows.map((r) => r.if_name).filter(Boolean)) })
      .catch(() => { if (!cancelled) setNames([]) })
    return () => { cancelled = true }
  }, [deviceId])
  return names
}

export default function ManualLinkModal({
  onClose, onSaved, prefillDeviceA, edit,
}: {
  onClose: () => void
  onSaved: () => void
  prefillDeviceA?: number
  edit?: ManualTopologyLink
}) {
  const [devices, setDevices] = useState<Device[]>([])
  const [deviceA, setDeviceA] = useState<number | ''>(edit?.device_a ?? prefillDeviceA ?? '')
  const [interfaceA, setInterfaceA] = useState(edit?.interface_a ?? '')
  const [deviceB, setDeviceB] = useState<number | ''>(edit?.device_b ?? '')
  const [interfaceB, setInterfaceB] = useState(edit?.interface_b ?? '')
  const [linkType, setLinkType] = useState<ManualLinkType>(edit?.link_type ?? 'ethernet')
  const [speed, setSpeed] = useState<string>(edit?.speed_mbps ? String(edit.speed_mbps) : '')
  const [description, setDescription] = useState(edit?.description ?? '')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetchDevices({ page_size: '500' }).then((d) => setDevices(d.results)).catch(() => {})
  }, [])

  const ifaceA = useInterfaceNames(deviceA)
  const ifaceB = useInterfaceNames(deviceB)

  const save = async () => {
    setError(null)
    if (!deviceA || !deviceB) { setError('Select both devices.'); return }
    if (deviceA === deviceB) { setError('A link must connect two different devices.'); return }
    setSaving(true)
    const payload = {
      device_a: Number(deviceA), interface_a: interfaceA,
      device_b: Number(deviceB), interface_b: interfaceB,
      link_type: linkType,
      speed_mbps: speed ? Number(speed) : null,
      description,
    }
    try {
      if (edit) await updateManualLink(edit.id, payload)
      else await createManualLink(payload)
      onSaved()
    } catch (e) {
      const resp = (e as { response?: { data?: Record<string, unknown> } }).response
      const detail = resp?.data ? Object.values(resp.data).flat().join(' ') : ''
      setError(detail || 'Could not save the link.')
    } finally {
      setSaving(false)
    }
  }

  const deviceOptions = devices.map((d) => (
    <option key={d.id} value={d.id}>{d.hostname}</option>
  ))

  return (
    <Modal onClose={onClose} title={edit ? 'Edit Manual Link' : 'Add Manual Link'}>
      <div className="space-y-4">
        {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{error}</div>}

        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
          <p className="text-xs font-semibold text-gray-600 dark:text-gray-300 mb-2">Device A</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={label}>Device</label>
              <select className={inputCls} value={deviceA} onChange={(e) => setDeviceA(e.target.value ? Number(e.target.value) : '')}>
                <option value="">Select…</option>{deviceOptions}
              </select>
            </div>
            <div>
              <label className={label}>Interface</label>
              <input className={inputCls} list="iface-a" value={interfaceA}
                onChange={(e) => setInterfaceA(e.target.value)} placeholder="GigabitEthernet0/0/0" />
              <datalist id="iface-a">{ifaceA.map((n) => <option key={n} value={n} />)}</datalist>
            </div>
          </div>
        </div>

        <div className="text-center text-xs text-gray-400">↕ connects to</div>

        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
          <p className="text-xs font-semibold text-gray-600 dark:text-gray-300 mb-2">Device B</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={label}>Device</label>
              <select className={inputCls} value={deviceB} onChange={(e) => setDeviceB(e.target.value ? Number(e.target.value) : '')}>
                <option value="">Select…</option>{deviceOptions}
              </select>
            </div>
            <div>
              <label className={label}>Interface</label>
              <input className={inputCls} list="iface-b" value={interfaceB}
                onChange={(e) => setInterfaceB(e.target.value)} placeholder="1/1/50" />
              <datalist id="iface-b">{ifaceB.map((n) => <option key={n} value={n} />)}</datalist>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label}>Link Type</label>
            <select className={inputCls} value={linkType} onChange={(e) => setLinkType(e.target.value as ManualLinkType)}>
              {LINK_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
          <div>
            <label className={label}>Speed (Mbps)</label>
            <input className={inputCls} type="number" value={speed} onChange={(e) => setSpeed(e.target.value)} placeholder="1000" />
          </div>
        </div>
        <div>
          <label className={label}>Description</label>
          <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="Firewall uplink" maxLength={256} />
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">Cancel</button>
          <button onClick={save} disabled={saving}
            className="px-4 py-2 text-sm rounded-lg font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50">
            {saving ? 'Saving…' : edit ? 'Save Link' : 'Add Link'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
