import { useState } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import { useAuthStore } from '../../store/authStore'
import { SectionHeader, Tabs } from '../Settings'

// Note: a users management API is a later backend phase. This screen operates on
// local state seeded with the signed-in user so the intended UX is reviewable.

type RoleId = 'admin' | 'engineer' | 'viewer' | 'api'

interface UiUser {
  id: number
  name: string
  email: string
  role: RoleId
  lastLogin: string | null
  active: boolean
}

const ROLE_BADGE: Record<RoleId, string> = {
  admin: 'bg-purple-100 text-purple-700',
  engineer: 'bg-blue-100 text-blue-700',
  viewer: 'bg-gray-100 text-gray-600',
  api: 'bg-amber-100 text-amber-700',
}

const ROLE_LABEL: Record<RoleId, string> = {
  admin: 'Admin', engineer: 'Engineer', viewer: 'Viewer', api: 'API',
}

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

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
  const { username, role } = useAuthStore()
  const [users, setUsers] = useState<UiUser[]>(() => [
    {
      id: 1,
      name: username ?? 'You',
      email: 'you@example.com',
      role: (role as RoleId) || 'admin',
      lastLogin: 'Just now',
      active: true,
    },
    { id: 2, name: 'Dana Engineer', email: 'dana@example.com', role: 'engineer', lastLogin: '2h ago', active: true },
    { id: 3, name: 'Sam Viewer', email: 'sam@example.com', role: 'viewer', lastLogin: '3d ago', active: true },
    { id: 4, name: 'telemetry-svc', email: '—', role: 'api', lastLogin: '5m ago', active: true },
  ])
  const [inviting, setInviting] = useState(false)
  const [editing, setEditing] = useState<UiUser | null>(null)

  const setRole = (id: number, r: RoleId) =>
    setUsers((u) => u.map((x) => (x.id === id ? { ...x, role: r } : x)))
  const toggleActive = (id: number) =>
    setUsers((u) => u.map((x) => (x.id === id ? { ...x, active: !x.active } : x)))

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

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                <th className="px-5 py-3 font-medium">User</th>
                <th className="px-5 py-3 font-medium">Role</th>
                <th className="px-5 py-3 font-medium">Last login</th>
                <th className="px-5 py-3 font-medium">Status</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-3">
                      <Avatar name={u.name} />
                      <div className="min-w-0">
                        <p className="font-medium text-gray-800 truncate">{u.name}</p>
                        <p className="text-xs text-gray-500 truncate">{u.email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', ROLE_BADGE[u.role])}>
                      {ROLE_LABEL[u.role]}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-gray-600">{u.lastLogin ?? 'Never'}</td>
                  <td className="px-5 py-3">
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium',
                      u.active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500')}>
                      {u.active ? 'Active' : 'Deactivated'}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center justify-end gap-2">
                      <button onClick={() => setEditing(u)} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Edit Role</button>
                      <button onClick={() => toggleActive(u.id)} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">
                        {u.active ? 'Deactivate' : 'Reactivate'}
                      </button>
                      <button className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">Reset Password</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {inviting && (
        <InviteModal
          onClose={() => setInviting(false)}
          onInvite={(email, r) => {
            setUsers((u) => [...u, { id: Date.now(), name: email.split('@')[0], email, role: r, lastLogin: null, active: true }])
            setInviting(false)
          }}
        />
      )}
      {editing && (
        <EditRoleModal
          user={editing}
          onClose={() => setEditing(null)}
          onSave={(r) => { setRole(editing.id, r); setEditing(null) }}
        />
      )}
    </div>
  )
}

function Avatar({ name }: { name: string }) {
  const initials = name.slice(0, 2).toUpperCase()
  return (
    <span className="w-9 h-9 shrink-0 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-xs font-semibold">
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

function InviteModal({ onClose, onInvite }: { onClose: () => void; onInvite: (email: string, role: RoleId) => void }) {
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<RoleId>('viewer')
  return (
    <Modal
      title="Invite User"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          <button onClick={() => email && onInvite(email, role)} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Send Invite</button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Email address</label>
          <input className={inputCls} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="person@company.com" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
          <RoleSelect value={role} onChange={setRole} />
        </div>
      </div>
    </Modal>
  )
}

function EditRoleModal({ user, onClose, onSave }: { user: UiUser; onClose: () => void; onSave: (r: RoleId) => void }) {
  const [role, setRole] = useState<RoleId>(user.role)
  return (
    <Modal
      title={`Edit role — ${user.name}`}
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          <button onClick={() => onSave(role)} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Save</button>
        </>
      }
    >
      <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
      <RoleSelect value={role} onChange={setRole} />
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
  admin: 'bg-purple-100 text-purple-700',
  write: 'bg-blue-100 text-blue-700',
  read: 'bg-gray-100 text-gray-600',
  none: 'bg-red-50 text-red-400',
}

function RolesTab() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {ROLES.map((r) => (
          <div key={r.id} className="bg-white border border-gray-200 rounded-lg p-4">
            <span className={clsx('inline-block px-2 py-0.5 rounded-full text-xs font-medium mb-2', ROLE_BADGE[r.id])}>
              {ROLE_LABEL[r.id]}
            </span>
            <p className="text-sm text-gray-600">{r.summary}</p>
            <p className="text-xs text-gray-400 mt-2">{r.adminPanel ? '✓ Django admin panel' : '— No admin panel'}</p>
          </div>
        ))}
      </div>

      <div>
        <h3 className="text-sm font-semibold text-gray-800 mb-2">Permissions matrix</h3>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                <th className="px-5 py-3 font-medium">Feature</th>
                {ROLES.map((r) => <th key={r.id} className="px-5 py-3 font-medium">{ROLE_LABEL[r.id]}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {FEATURES.map((feat, i) => (
                <tr key={feat} className="hover:bg-gray-50">
                  <td className="px-5 py-3 font-medium text-gray-700">{feat}</td>
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
        <p className="text-xs text-gray-400 mt-2">Built-in roles. Custom roles are planned for a future release.</p>
      </div>
    </div>
  )
}
