import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { useAuthStore } from '../../store/authStore'
import { SectionHeader, Tabs } from '../Settings'
import {
  fetchUsers, createUser, updateUser, deleteUser,
  type AdminUser, type UserRole as RoleId,
} from '../../api/client'

function apiError(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { error?: string; detail?: string; password?: string[] } } }
  const d = e?.response?.data
  return d?.error || d?.detail || d?.password?.[0] || fallback
}

function relTime(iso: string | null): string {
  if (!iso) return 'Never'
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'Just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

const ROLE_BADGE: Record<RoleId, string> = {
  admin: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  engineer: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  viewer: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
  api: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
}

const ROLE_LABEL: Record<RoleId, string> = {
  admin: 'Admin', engineer: 'Engineer', viewer: 'Viewer', api: 'API',
}

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const TABS = [{ id: 'users', label: 'Users' }, { id: 'roles', label: 'Roles' }]

export default function Users() {
  const [tab, setTab] = useState('users')
  return (
    <div>
      <SectionHeader title="Users & Access" description="Manage users, roles and permissions." />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'users' ? <UsersTab /> : <RolesTab />}
    </div>
  )
}

// ── Users tab ────────────────────────────────────────────────────────────────

function UsersTab() {
  const { username: me } = useAuthStore()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [inviting, setInviting] = useState(false)
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const [deleting, setDeleting] = useState<AdminUser | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchUsers()
      .then((data) => { setUsers(data); setError(null) })
      .catch(() => setError('Failed to load users.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const toggleActive = async (u: AdminUser) => {
    setBusyId(u.id); setError(null)
    try {
      const updated = await updateUser(u.id, { is_active: !u.is_active })
      setUsers((us) => us.map((x) => (x.id === u.id ? updated : x)))
    } catch (err) {
      setError(apiError(err, 'Failed to update user.'))
    } finally {
      setBusyId(null)
    }
  }

  const displayName = (u: AdminUser) =>
    [u.first_name, u.last_name].filter(Boolean).join(' ') || u.username

  return (
    <div>
      <div className="flex justify-end mb-3">
        <button
          onClick={() => setInviting(true)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
        >
          + Add User
        </button>
      </div>

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : users.length === 0 ? (
          <EmptyState title="No users" description="Add a user to grant access to NetPulse." action={{ label: 'Add User', onClick: () => setInviting(true) }} icon="👤" />
        ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">User</th>
                <th className="px-5 py-3 font-medium">Role</th>
                <th className="px-5 py-3 font-medium">Last login</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {users.map((u) => {
                const isSelf = u.username === me
                return (
                <tr key={u.id} className={clsx('hover:bg-gray-50 dark:hover:bg-gray-700/50', busyId === u.id && 'opacity-50')}>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-3">
                      <Avatar name={displayName(u)} />
                      <div className="min-w-0">
                        <p className="font-medium text-gray-800 dark:text-gray-100 truncate flex items-center gap-2">
                          {displayName(u)}
                          {isSelf && <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">You</span>}
                        </p>
                        <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{u.email || '—'}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', ROLE_BADGE[u.role])}>
                      {ROLE_LABEL[u.role]}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{relTime(u.last_login)}</td>
                  <td className="px-5 py-3">
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium',
                      u.is_active ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400')}>
                      {u.is_active ? 'Active' : 'Deactivated'}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center justify-end gap-2">
                      <button onClick={() => setEditing(u)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300">Edit Role</button>
                      <button disabled={busyId === u.id} onClick={() => toggleActive(u)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300 disabled:opacity-50">
                        {u.is_active ? 'Deactivate' : 'Reactivate'}
                      </button>
                      <button
                        disabled={isSelf}
                        title={isSelf ? 'You cannot delete your own account.' : 'Delete user'}
                        onClick={() => setDeleting(u)}
                        className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-900/50 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        )}
      </div>

      {inviting && (
        <AddUserModal
          onClose={() => setInviting(false)}
          onCreated={() => { setInviting(false); load() }}
        />
      )}
      {editing && (
        <EditRoleModal
          user={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
        />
      )}
      {deleting && (
        <DeleteUserModal
          user={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => { setDeleting(null); load() }}
        />
      )}
    </div>
  )
}

function Avatar({ name }: { name: string }) {
  const initials = name.slice(0, 2).toUpperCase()
  return (
    <span className="w-9 h-9 shrink-0 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 flex items-center justify-center text-xs font-semibold">
      {initials}
    </span>
  )
}

function RoleSelect({ value, onChange }: { value: RoleId; onChange: (r: RoleId) => void }) {
  return (
    <select className={inputCls} value={value} onChange={(e) => onChange(e.target.value as RoleId)}>
      {(Object.keys(ROLE_LABEL) as RoleId[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
    </select>
  )
}

function AddUserModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<RoleId>('viewer')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (!username.trim()) { setErr('Username is required.'); return }
    if (password.length < 8) { setErr('Password must be at least 8 characters.'); return }
    setSaving(true); setErr(null)
    try {
      await createUser({ username: username.trim(), email: email.trim() || undefined, role, password })
      onCreated()
    } catch (e) {
      setSaving(false); setErr(apiError(e, 'Failed to create user.'))
    }
  }

  return (
    <Modal
      title="Add User"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Creating…' : 'Create User'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Username</label>
          <input className={inputCls} value={username} onChange={(e) => setUsername(e.target.value)} placeholder="jdoe" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Email <span className="text-gray-400">(optional)</span></label>
          <input className={inputCls} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="person@company.com" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Password</label>
          <input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Role</label>
          <RoleSelect value={role} onChange={setRole} />
        </div>
      </div>
    </Modal>
  )
}

function EditRoleModal({ user, onClose, onSaved }: { user: AdminUser; onClose: () => void; onSaved: () => void }) {
  const [role, setRole] = useState<RoleId>(user.role)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username

  const submit = async () => {
    setSaving(true); setErr(null)
    try {
      await updateUser(user.id, { role })
      onSaved()
    } catch (e) {
      setSaving(false); setErr(apiError(e, 'Failed to update role.'))
    }
  }

  return (
    <Modal
      title={`Edit role — ${name}`}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save'}</button>
        </>
      }
    >
      {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-3">{err}</div>}
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Role</label>
      <RoleSelect value={role} onChange={setRole} />
    </Modal>
  )
}

function DeleteUserModal({ user, onClose, onDeleted }: { user: AdminUser; onClose: () => void; onDeleted: () => void }) {
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username

  const submit = async () => {
    setSaving(true); setErr(null)
    try {
      await deleteUser(user.id)
      onDeleted()
    } catch (e) {
      setSaving(false); setErr(apiError(e, 'Failed to delete user.'))
    }
  }

  return (
    <Modal
      title="Delete user"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={submit} disabled={saving} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Deleting…' : 'Delete'}</button>
        </>
      }
    >
      {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-3">{err}</div>}
      <p className="text-sm text-gray-600 dark:text-gray-300">
        Permanently delete <span className="font-semibold">{name}</span> ({user.username})? This cannot be undone.
      </p>
    </Modal>
  )
}

// ── Roles tab ────────────────────────────────────────────────────────────────

interface RoleDef {
  id: RoleId
  summary: string
  adminPanel: boolean
}

const ROLES: RoleDef[] = [
  { id: 'admin', summary: 'Full access to all endpoints and the Django admin panel.', adminPanel: true },
  { id: 'engineer', summary: 'Read/write on all operational endpoints.', adminPanel: false },
  { id: 'api', summary: 'Service-account tokens — read/write, no admin panel.', adminPanel: false },
  { id: 'viewer', summary: 'Read-only (safe HTTP methods only).', adminPanel: false },
]

type Access = 'admin' | 'write' | 'read' | 'none'
const FEATURES = ['Devices', 'Telemetry', 'Alerts', 'Credentials', 'Discovery', 'Users & Settings']

// Mirrors apps.core.permissions: write roles = admin/engineer/api, read = all,
// user/settings administration = admin only.
const MATRIX: Record<RoleId, Access[]> = {
  // Devices, Telemetry, Alerts, Credentials, Discovery, Users & Settings
  admin:    ['admin', 'admin', 'admin', 'admin', 'admin', 'admin'],
  engineer: ['write', 'write', 'write', 'write', 'write', 'read'],
  api:      ['write', 'write', 'write', 'write', 'write', 'none'],
  viewer:   ['read', 'read', 'read', 'read', 'read', 'none'],
}

const ACCESS_BADGE: Record<Access, string> = {
  admin: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  write: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  read: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
  none: 'bg-red-50 text-red-400 dark:bg-red-900/30 dark:text-red-400',
}

function RolesTab() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {ROLES.map((r) => (
          <div key={r.id} className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <span className={clsx('inline-block px-2 py-0.5 rounded-full text-xs font-medium mb-2', ROLE_BADGE[r.id])}>
              {ROLE_LABEL[r.id]}
            </span>
            <p className="text-sm text-gray-600 dark:text-gray-400">{r.summary}</p>
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">{r.adminPanel ? '✓ Django admin panel' : '— No admin panel'}</p>
          </div>
        ))}
      </div>

      <div>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">Permissions matrix</h3>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Feature</th>
                {ROLES.map((r) => <th key={r.id} className="px-5 py-3 font-medium">{ROLE_LABEL[r.id]}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {FEATURES.map((feat, i) => (
                <tr key={feat} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-5 py-3 font-medium text-gray-700 dark:text-gray-300">{feat}</td>
                  {ROLES.map((r) => {
                    const access = MATRIX[r.id][i]
                    return (
                      <td key={r.id} className="px-5 py-3">
                        <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', ACCESS_BADGE[access])}>
                          {access === 'none' ? '—' : access}
                        </span>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">Built-in roles. Custom roles are planned for a future release.</p>
      </div>
    </div>
  )
}
