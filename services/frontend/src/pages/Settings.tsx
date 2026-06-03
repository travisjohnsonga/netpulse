import { type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import clsx from 'clsx'

interface SettingsNavItem {
  to: string
  label: string
  icon: string
}

const SETTINGS_NAV: SettingsNavItem[] = [
  { to: 'general',      label: 'General',        icon: '⚙' },
  { to: 'polling',      label: 'Polling',        icon: '⏱' },
  { to: 'users',        label: 'Users & Access', icon: '👥' },
  { to: 'credentials',  label: 'Credentials',    icon: '🔑' },
  { to: 'integrations', label: 'Integrations',   icon: '🔌' },
  { to: 'alerting',     label: 'Alerting',       icon: '⚠' },
  { to: 'alert-routing', label: 'Alert Routing', icon: '🧭' },
  { to: 'discovery',    label: 'Discovery',      icon: '🔎' },
  { to: 'collectors',   label: 'Collectors',     icon: '📡' },
  { to: 'data-sources', label: 'Data Sources',   icon: '🗄' },
  { to: 'mibs',         label: 'MIB Files',      icon: '📚' },
  { to: 'certificates', label: 'Certificates',   icon: '🔒' },
  { to: 'sso',          label: 'SSO / Login',    icon: '🔐' },
  { to: 'platform-status', label: 'Platform Status', icon: '🩺' },
  { to: 'system',       label: 'System',         icon: '🖥' },
]

export default function Settings() {
  return (
    <div className="flex gap-6">
      {/* Settings sub-navigation — icons only on narrow screens, full on desktop */}
      <nav className="shrink-0 w-14 lg:w-56 space-y-1">
        <h1 className="hidden lg:block text-lg font-bold text-gray-900 dark:text-gray-100 px-3 mb-3">Settings</h1>
        {SETTINGS_NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            title={item.label}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-3 rounded-lg text-sm font-medium transition-colors',
                'justify-center lg:justify-start px-0 lg:px-3 py-2.5',
                isActive
                  ? 'bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300'
                  : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-white',
              )
            }
          >
            <span className="text-base w-5 text-center" aria-hidden>{item.icon}</span>
            <span className="hidden lg:inline">{item.label}</span>
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
