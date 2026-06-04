import { useEffect, useState } from 'react'
import Modal from './Modal'
import { fetchSites, fetchDeviceRoles, updateDevice, fetchDevicePlatforms, type DeviceDetail, type Site, type DeviceRole, type PlatformOption } from '../api/client'
import RoleBubble from './RoleBubble'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-gray-100'

// Fallback list (covers the model's platforms incl. SonicWall/AOS-CX/Aruba)
// used until the dynamic /devices/platforms/ list loads or if it fails.
const PLATFORMS_FALLBACK: PlatformOption[] = [
  { value: 'ios', label: 'Cisco IOS' }, { value: 'ios_xe', label: 'Cisco IOS-XE' },
  { value: 'ios_xr', label: 'Cisco IOS-XR' }, { value: 'nxos', label: 'Cisco NX-OS' },
  { value: 'eos', label: 'Arista EOS' }, { value: 'junos', label: 'Juniper JunOS' },
  { value: 'fortios', label: 'Fortinet FortiOS' }, { value: 'panos', label: 'Palo Alto PAN-OS' },
  { value: 'sonicwall', label: 'SonicWall SonicOS' }, { value: 'aos_cx', label: 'HPE AOS-CX' },
  { value: 'aruba', label: 'Aruba AOS' }, { value: 'sonic', label: 'SONiC' },
  { value: 'other', label: 'Other' },
]

// Tags live inside notes (no dedicated model field). Parse them out so they can
// be edited, and preserve any other note lines. (Role is now a real FK; any
// legacy "Role:" line in notes is dropped on save in favour of the FK.)
function parseNotes(notes: string): { tags: string[]; rest: string } {
  let tags: string[] = []
  const rest: string[] = []
  for (const ln of (notes || '').split('\n')) {
    const r = ln.match(/^\s*Role:\s*(.*)$/i)
    const t = ln.match(/^\s*Tags:\s*(.*)$/i)
    if (r) continue  // drop legacy notes-based role
    else if (t) tags = t[1].split(',').map((s) => s.trim()).filter(Boolean)
    else rest.push(ln)
  }
  return { tags, rest: rest.join('\n').trim() }
}

function buildNotes(tags: string[], rest: string): string {
  const parts: string[] = []
  if (tags.length) parts.push(`Tags: ${tags.join(', ')}`)
  if (rest.trim()) parts.push(rest.trim())
  return parts.join('\n')
}

export default function DeviceEditModal({ device, onClose, onSaved }: {
  device: DeviceDetail
  onClose: () => void
  onSaved: () => void
}) {
  const parsed = parseNotes(device.notes)
  const [hostname, setHostname] = useState(device.hostname)
  const [ip, setIp] = useState(device.ip_address)
  const [mgmtIp, setMgmtIp] = useState(device.management_ip ?? '')
  const [vendor, setVendor] = useState(device.vendor)
  const [platform, setPlatform] = useState(device.platform || 'other')
  const [osVersion, setOsVersion] = useState(device.os_version)
  const [model, setModel] = useState(device.model)
  const [serial, setSerial] = useState(device.serial_number)
  const [siteId, setSiteId] = useState<number | ''>(device.site ?? '')
  const [roleId, setRoleId] = useState<number | ''>(device.role?.id ?? '')
  const [tags, setTags] = useState<string[]>(parsed.tags)
  const [tagInput, setTagInput] = useState('')
  const [notes, setNotes] = useState(parsed.rest)
  const [sites, setSites] = useState<Site[]>([])
  const [roles, setRoles] = useState<DeviceRole[]>([])
  const [platforms, setPlatforms] = useState<PlatformOption[]>(PLATFORMS_FALLBACK)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { fetchSites().then(setSites).catch(() => {}) }, [])
  useEffect(() => { fetchDeviceRoles().then(setRoles).catch(() => {}) }, [])
  useEffect(() => {
    fetchDevicePlatforms().then((p) => { if (p.length) setPlatforms(p) }).catch(() => {})
  }, [])

  const selectedRole = roles.find((r) => r.id === roleId) ?? null

  const addTag = () => {
    const t = tagInput.trim()
    if (t && !tags.includes(t)) setTags((x) => [...x, t])
    setTagInput('')
  }

  const save = async () => {
    if (!hostname.trim() || !ip.trim()) { setErr('Hostname and IP are required.'); return }
    setSaving(true); setErr(null)
    try {
      await updateDevice(device.id, {
        hostname: hostname.trim(),
        ip_address: ip.trim(),
        management_ip: mgmtIp.trim() || null,
        vendor,
        platform,
        os_version: osVersion,
        model,
        serial_number: serial,
        status: device.status,                  // preserve
        site: siteId === '' ? null : Number(siteId),
        role_id: roleId === '' ? null : Number(roleId),
        credential_profile: device.credential_profile,  // preserve
        groups: device.groups,                  // preserve
        notes: buildNotes(tags, notes),
      })
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to save device.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title={`Edit: ${device.hostname}`}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save Changes'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <Field label="Hostname"><input className={inputCls} value={hostname} onChange={(e) => setHostname(e.target.value)} /></Field>
        <Row>
          <Field label="IP Address"><input className={inputCls} value={ip} onChange={(e) => setIp(e.target.value)} /></Field>
          <Field label="Management IP"><input className={inputCls} value={mgmtIp} onChange={(e) => setMgmtIp(e.target.value)} placeholder="optional" /></Field>
        </Row>
        <Row>
          <Field label="Vendor"><input className={inputCls} value={vendor} onChange={(e) => setVendor(e.target.value)} /></Field>
          <Field label="Platform">
            <select className={inputCls} value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {platforms.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
          </Field>
        </Row>
        <Row>
          <Field label="OS Version"><input className={inputCls} value={osVersion} onChange={(e) => setOsVersion(e.target.value)} /></Field>
          <Field label="Hardware Model"><input className={inputCls} value={model} onChange={(e) => setModel(e.target.value)} /></Field>
        </Row>
        <Row>
          <Field label="Serial Number"><input className={inputCls} value={serial} onChange={(e) => setSerial(e.target.value)} /></Field>
        </Row>
        <Row>
          <Field label="Site">
            <select className={inputCls} value={siteId} onChange={(e) => setSiteId(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">— None —</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Role">
            <div className="flex items-center gap-2">
              <select
                className={inputCls}
                value={roleId}
                onChange={(e) => setRoleId(e.target.value === '' ? '' : Number(e.target.value))}
              >
                <option value="">— No role —</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
              {selectedRole && <RoleBubble role={selectedRole} />}
            </div>
          </Field>
        </Row>
        <Field label="Tags">
          <div className="flex flex-wrap gap-1.5 mb-2">
            {tags.map((t) => (
              <span key={t} className="inline-flex items-center gap-1 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-xs px-2 py-1 rounded-md">
                {t}<button onClick={() => setTags((x) => x.filter((v) => v !== t))} className="text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">×</button>
              </span>
            ))}
          </div>
          <input className={inputCls} value={tagInput} onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag() } }}
            placeholder="Type a tag and press Enter" />
        </Field>
        <Field label="Notes"><textarea className={`${inputCls} h-20`} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      </div>
    </Modal>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex-1 min-w-0"><label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{label}</label>{children}</div>
}
function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col sm:flex-row gap-3">{children}</div>
}
