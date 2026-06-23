/**
 * Access Roles — RBAC Track 2 role management (Settings → Access Roles).
 *
 * NOTE: distinct from the "Device Roles" screen (pages/settings/Roles.tsx), which
 * classifies devices by colour. This manages capability-based access roles.
 *
 * System roles are read-only (viewed in a drawer); custom roles get a full
 * editor with the capability catalog as grouped checkboxes. Anti-escalation is
 * mirrored client-side — you can only tick capabilities you yourself hold — but
 * the server's 403 is the real boundary (surfaced inline if it fires).
 */
import { useEffect, useMemo, useState } from 'react'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import NotAuthorized from '../../components/NotAuthorized'
import { SectionHeader } from '../Settings'
import { useCapabilities, useHasCapability } from '../../store/authStore'
import { parseApiErrors } from '../../api/errors'
import {
  fetchRbacRoles, fetchCapabilityCatalog, createRbacRole, updateRbacRole, deleteRbacRole,
  type RbacRole, type CapabilityGroup,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

export default function AccessRoles() {
  const canManage = useHasCapability('rbac:manage')
  const [roles, setRoles] = useState<RbacRole[]>([])
  const [catalog, setCatalog] = useState<CapabilityGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<RbacRole | null>(null)
  const [creating, setCreating] = useState(false)
  const [viewing, setViewing] = useState<RbacRole | null>(null)
  const [deleting, setDeleting] = useState<RbacRole | null>(null)

  const load = () => {
    setLoading(true)
    Promise.all([fetchRbacRoles(), fetchCapabilityCatalog()])
      .then(([r, c]) => { setRoles(r); setCatalog(c); setError(null) })
      .catch(() => setError('Failed to load roles.'))
      .finally(() => setLoading(false))
  }
  useEffect(() => { if (canManage) load() }, [canManage])

  // Defense-in-depth: the route is already guarded, but never render the editor
  // to someone without rbac:manage.
  if (!canManage) return <NotAuthorized />

  return (
    <div>
      <SectionHeader
        title="Access Roles"
        description="Capability-based roles that govern what each user can do. System roles are built in and read-only; create custom roles for finer-grained access."
        action={
          <button onClick={() => setCreating(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
            + New Role
          </button>
        }
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : roles.length === 0 ? (
          <EmptyState title="No roles" description="Create a custom role to grant a tailored set of capabilities." icon="🛡️" action={{ label: 'New Role', onClick: () => setCreating(true) }} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">Role</th>
                  <th className="px-5 py-3 font-medium">Capabilities</th>
                  <th className="px-5 py-3 font-medium">Users</th>
                  <th className="px-5 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {roles.map((r) => (
                  <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-gray-800 dark:text-gray-100">{r.name}</span>
                        {r.is_system && (
                          <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300" title={r.is_immutable ? 'Built-in, immutable role' : 'Built-in system role (read-only)'}>
                            {r.is_immutable ? 'System · locked' : 'System'}
                          </span>
                        )}
                      </div>
                      {r.description && <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{r.description}</p>}
                    </td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{r.capabilities.length}</td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{r.user_count}</td>
                    <td className="px-5 py-3 text-right whitespace-nowrap">
                      {r.is_system ? (
                        <button onClick={() => setViewing(r)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">View</button>
                      ) : (
                        <>
                          <button onClick={() => setEditing(r)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300 mr-1">Edit</button>
                          <button
                            onClick={() => setDeleting(r)}
                            disabled={r.user_count > 0}
                            title={r.user_count > 0 ? `${r.user_count} user${r.user_count !== 1 ? 's' : ''} assigned — reassign before deleting` : 'Delete role'}
                            className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30 disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {(creating || editing) && (
        <RoleEditorModal
          role={editing}
          catalog={catalog}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSaved={() => { setCreating(false); setEditing(null); load() }}
        />
      )}
      {viewing && <RoleViewModal role={viewing} catalog={catalog} onClose={() => setViewing(null)} />}
      {deleting && (
        <DeleteRoleModal role={deleting} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); load() }} />
      )}
    </div>
  )
}

// ── Custom-role editor ─────────────────────────────────────────────────────────

function RoleEditorModal({ role, catalog, onClose, onSaved }: {
  role: RbacRole | null
  catalog: CapabilityGroup[]
  onClose: () => void
  onSaved: () => void
}) {
  const myCaps = useCapabilities()
  const grantable = useMemo(() => new Set(myCaps), [myCaps])
  const [name, setName] = useState(role?.name ?? '')
  const [description, setDescription] = useState(role?.description ?? '')
  const [selected, setSelected] = useState<Set<string>>(new Set(role?.capabilities ?? []))
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const toggle = (cap: string) => {
    if (!grantable.has(cap)) return
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(cap) ? next.delete(cap) : next.add(cap)
      return next
    })
  }

  const toggleGroup = (group: CapabilityGroup, on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const c of group.capabilities) {
        if (!grantable.has(c.name)) continue
        on ? next.add(c.name) : next.delete(c.name)
      }
      return next
    })
  }

  const save = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    setSaving(true); setErr(null)
    try {
      const payload = { name: name.trim(), description: description.trim(), capabilities: [...selected] }
      if (role) await updateRbacRole(role.id, payload)
      else await createRbacRole(payload)
      onSaved()
    } catch (e) {
      setSaving(false)
      setErr(parseApiErrors(e, 'Failed to save role.'))
    }
  }

  return (
    <Modal
      title={role ? `Edit role — ${role.name}` : 'New role'}
      onClose={onClose}
      size="xl"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save role'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Name</label>
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. NOC Operator" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Description</label>
            <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="optional" />
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400">Capabilities ({selected.size})</label>
            <span className="text-[11px] text-gray-400 dark:text-gray-500">Greyed capabilities are ones you don't hold and can't grant.</span>
          </div>
          <div className="space-y-3 max-h-[48vh] overflow-y-auto pr-1">
            {catalog.map((group) => {
              const grantableInGroup = group.capabilities.filter((c) => grantable.has(c.name))
              const allOn = grantableInGroup.length > 0 && grantableInGroup.every((c) => selected.has(c.name))
              return (
                <fieldset key={group.group} className="border border-gray-200 dark:border-gray-700 rounded-lg p-3">
                  <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 flex items-center gap-2">
                    {group.group}
                    {grantableInGroup.length > 0 && (
                      <button
                        type="button"
                        onClick={() => toggleGroup(group, !allOn)}
                        className="text-[10px] font-medium normal-case text-blue-600 dark:text-blue-400 hover:underline"
                      >
                        {allOn ? 'clear' : 'all'}
                      </button>
                    )}
                  </legend>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5 mt-1">
                    {group.capabilities.map((c) => {
                      const canGrant = grantable.has(c.name)
                      return (
                        <label
                          key={c.name}
                          title={canGrant ? c.name : "You can't grant a capability you don't hold"}
                          className={`flex items-center gap-2 text-sm ${canGrant ? 'text-gray-700 dark:text-gray-300 cursor-pointer' : 'text-gray-400 dark:text-gray-600 cursor-not-allowed'}`}
                        >
                          <input
                            type="checkbox"
                            className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
                            checked={selected.has(c.name)}
                            disabled={!canGrant}
                            onChange={() => toggle(c.name)}
                          />
                          <span className="font-mono text-xs">{c.name}</span>
                        </label>
                      )
                    })}
                  </div>
                </fieldset>
              )
            })}
          </div>
        </div>
      </div>
    </Modal>
  )
}

// ── System-role viewer (read-only) ─────────────────────────────────────────────

function RoleViewModal({ role, catalog, onClose }: {
  role: RbacRole
  catalog: CapabilityGroup[]
  onClose: () => void
}) {
  const held = new Set(role.capabilities)
  return (
    <Modal title={`${role.name} — capabilities`} onClose={onClose} size="lg"
      footer={<button onClick={onClose} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Close</button>}>
      <div className="space-y-3">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {role.is_immutable ? 'Built-in, immutable role.' : 'Built-in system role.'} Read-only — create a custom role to define your own access.
        </p>
        {catalog.map((group) => {
          const inRole = group.capabilities.filter((c) => held.has(c.name))
          if (inRole.length === 0) return null
          return (
            <div key={group.group}>
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">{group.group}</p>
              <div className="flex flex-wrap gap-1.5">
                {inRole.map((c) => (
                  <span key={c.name} className="font-mono text-[11px] px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">{c.name}</span>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </Modal>
  )
}

// ── Delete ──────────────────────────────────────────────────────────────────

function DeleteRoleModal({ role, onClose, onDeleted }: {
  role: RbacRole
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const remove = async () => {
    setDeleting(true); setErr(null)
    try { await deleteRbacRole(role.id); onDeleted() }
    catch (e) { setDeleting(false); setErr(parseApiErrors(e, 'Failed to delete role.')) }
  }

  return (
    <Modal title="Delete role" onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={remove} disabled={deleting || role.user_count > 0} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{deleting ? 'Deleting…' : 'Delete'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        {role.user_count > 0 ? (
          <p className="text-sm text-gray-700 dark:text-gray-300">
            <span className="font-semibold">{role.name}</span> is assigned to <strong>{role.user_count} user{role.user_count !== 1 ? 's' : ''}</strong>. Reassign them to another role before deleting it.
          </p>
        ) : (
          <p className="text-sm text-gray-700 dark:text-gray-300">Delete the role <span className="font-semibold">{role.name}</span>? This cannot be undone.</p>
        )}
      </div>
    </Modal>
  )
}
