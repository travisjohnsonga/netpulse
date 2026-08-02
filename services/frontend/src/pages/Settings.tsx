import { type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import clsx from 'clsx'
import { useCapabilities } from '../store/authStore'

interface SettingsNavItem {
  to: string
  label: string
  icon: string
  // Capability gating it on (its primary API). Omitted → visible to all. Items
  // gate on the relevant :view/:manage cap; the API 403 stays the real boundary.
  requiredCapability?: string | string[]
}

// Grouped to cut clutter: related sections live as tabs under one entry.
// Standalone entries below the divider are complex enough to stand alone.
const SETTINGS_NAV: SettingsNavItem[] = [
  { to: 'users',           label: 'Users & Access',  icon: '👥', requiredCapability: ['user:manage', 'rbac:manage', 'sso:manage'] },
  { to: 'access-roles',    label: 'Access Roles',    icon: '🛡️', requiredCapability: 'rbac:manage' },
  { to: 'alerting',        label: 'Alerting',        icon: '🔔', requiredCapability: 'alert:view' },
  { to: 'network-devices', label: 'Network Devices', icon: '🖥', requiredCapability: 'device:view' },
  { to: 'integrations',    label: 'Integrations',    icon: '🔗', requiredCapability: 'integration:view' },
  { to: 'compliance',      label: 'Compliance',      icon: '✅', requiredCapability: 'compliance:view' },
  { to: 'system',          label: 'System',          icon: '⚙' },
  { to: 'collectors',      label: 'Collectors',      icon: '📡', requiredCapability: 'collector:view' },
  { to: 'agents',          label: 'Agents',          icon: '🤖', requiredCapability: 'agent:view' },
  { to: 'discovery',       label: 'Discovery',       icon: '🔍', requiredCapability: 'device:view' },
  { to: 'hostname-rules',  label: 'Hostname Rules',  icon: '📋', requiredCapability: 'device:view' },
  { to: 'log-filters',     label: 'Log Filters',     icon: '🔇', requiredCapability: 'log:view' },
  { to: 'credentials',     label: 'Credentials',     icon: '🔑', requiredCapability: 'credential:view' },
  { to: 'polling',         label: 'Polling',         icon: '⏱', requiredCapability: 'telemetry:view' },
  { to: 'data-sources',    label: 'Data Sources',    icon: '🗄', requiredCapability: 'config:backup:manage' },
  { to: 'platform-status', label: 'Platform Status', icon: '🩺' },
]

export function hasAnyCapability(caps: string[], required?: string | string[]): boolean {
  if (!required) return true
  const needed = Array.isArray(required) ? required : [required]
  return needed.some((c) => caps.includes(c))
}

export default function Settings() {
  const caps = useCapabilities()
  const visible = SETTINGS_NAV.filter((i) => hasAnyCapability(caps, i.requiredCapability))
  return (
    <div className="flex gap-6">
      {/* Settings sub-navigation — text-only labels (the narrow width is wide
          enough to show the label, so nothing is blank on mobile). */}
      <nav className="shrink-0 w-44 lg:w-56 space-y-1">
        <h1 className="text-lg font-bold text-gray-900 dark:text-gray-100 px-3 mb-3">Settings</h1>
        {visible.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            title={item.label}
            className={({ isActive }) =>
              clsx(
                'flex items-center rounded-lg text-sm font-medium transition-colors px-3 py-2.5',
                isActive
                  ? 'bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300'
                  : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-white',
              )
            }
          >
            <span className="truncate">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Selected section content */}
      <div className="flex-1 min-w-0">
        <Outlet />
      </div>
    </div>
  )
}

/** Shared header used by every settings section. */
export function SectionHeader({ title, description, action }: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
      <div>
        <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">{title}</h2>
        {description && <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{description}</p>}
      </div>
      {action}
    </div>
  )
}

/** Tab bar used by sections with sub-tabs (Users, Alerting). */
export function Tabs({ tabs, active, onChange }: {
  tabs: { id: string; label: string }[]
  active: string
  onChange: (id: string) => void
}) {
  return (
    <div className="flex gap-1 border-b border-gray-200 mb-5">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
            active === t.id
              ? 'border-blue-600 text-blue-700'
              : 'border-transparent text-gray-500 hover:text-gray-800',
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}
