import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import RoleBubble, { RoleDot } from '../../components/RoleBubble'
import ColorPicker from '../../components/ColorPicker'
import { SectionHeader } from '../Settings'
import {
  fetchDeviceRoles, createDeviceRole, updateDeviceRole, deleteDeviceRole,
  type DeviceRole,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

export default function Roles() {
  const [roles, setRoles] = useState<DeviceRole[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<DeviceRole | null>(null)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<DeviceRole | null>(null)

  const load = () => {
    setLoading(true)
    fetchDeviceRoles()
      .then((r) => { setRoles(r); setError(null) })
      .catch(() => setError('Failed to load device roles.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  return (
    <div>
      <SectionHeader
        title="Device Roles"
        description="Colour-coded classifications shown as bubbles in the device list and detail pages."
        action={
          <button onClick={() => setCreating(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
            + Add Role
          </button>
        }
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : roles.length === 0 ? (
          <div className="py-16 text-center text-sm text-gray-500 dark:text-gray-400">No roles yet. Add your first device role.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-5 py-3 font-medium">Color</th>
                <th className="px-5 py-3 font-medium">Devices</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {roles.map((r) => (
                <tr key={r.id}>
                  <td className="px-5 py-3"><RoleBubble role={r} /></td>
                  <td className="px-5 py-3">
                    <span className="inline-flex items-center gap-2 font-mono text-xs text-gray-600 dark:text-gray-400">
                      <RoleDot color={r.color} />{r.color}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">
                    {r.device_count ?? 0} device{(r.device_count ?? 0) !== 1 ? 's' : ''}
                  </td>
                  <td className="px-5 py-3 text-right whitespace-nowrap">
                    <button onClick={() => setEditing(r)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 mr-1">Edit</button>
                    <button onClick={() => setDeleting(r)} className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {(creating || editing) && (
        <RoleModal
          role={editing}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSaved={() => { setCreating(false); setEditing(null); load() }}
        />
      )}
      {deleting && (
        <DeleteRoleModal
          role={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => { setDeleting(null); load() }}
        />
      )}
    </div>
  )
}

function RoleModal({ role, onClose, onSaved }: {
  role: DeviceRole | null
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(role?.name ?? '')
  const [color, setColor] = useState(role?.color ?? '#6366f1')
  const [description, setDescription] = useState(role?.description ?? '')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const save = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    try {
      const payload = { name: name.trim(), color: color.trim(), description: description.trim() }
      if (role) await updateDeviceRole(role.id, payload)
      else await createDeviceRole(payload)
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to save role.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title={role ? `Edit Role: ${role.name}` : 'Add Device Role'}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Name</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Core Switch" />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Color</label>
          <ColorPicker value={color} onChange={setColor} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Description</label>
          <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="optional" />
        </div>
        <div className="pt-1">
          <span className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Preview</span>
          <RoleBubble role={{ id: 0, name: name || 'Role name', slug: '', color, description, icon: '' }} />
        </div>
      </div>
    </Modal>
  )
}

function DeleteRoleModal({ role, onClose, onDeleted }: {
  role: DeviceRole
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const count = role.device_count ?? 0

  const remove = async () => {
    setDeleting(true); setErr(null)
    try { await deleteDeviceRole(role.id); onDeleted() }
    catch (e) {
      const detail = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
      setErr(detail || 'Failed to delete role.')
      setDeleting(false)
    }
  }

  return (
    <Modal
      title="Delete Role"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={remove} disabled={deleting || count > 0} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{deleting ? 'Deleting…' : 'Delete'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        {count > 0 ? (
          <p className="text-sm text-gray-700 dark:text-gray-300">
            <RoleBubble role={role} /> is assigned to <strong>{count} device{count !== 1 ? 's' : ''}</strong>.
            Reassign those devices to another role before deleting it.
          </p>
        ) : (
          <p className="text-sm text-gray-700 dark:text-gray-300">
            Delete the role <RoleBubble role={role} />? This cannot be undone.
          </p>
        )}
      </div>
    </Modal>
  )
}
