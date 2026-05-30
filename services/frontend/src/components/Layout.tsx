import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { useWebSocket } from '../hooks/useWebSocket'
import { useAuthStore } from '../store/authStore'
import { useThemeStore } from '../store/themeStore'
import { usePreferencesStore } from '../store/preferencesStore'
import ErrorBoundary from './ErrorBoundary'

interface NavItem {
  label: string
  href: string
  icon: string
}

const navItems: NavItem[] = [
  { label: 'Dashboard', href: '/dashboard', icon: '▦' },
  { label: 'Devices', href: '/devices', icon: '⬡' },
  { label: 'Sites', href: '/sites', icon: '🏢' },
  { label: 'Compare', href: '/configs/compare', icon: '🔀' },
  { label: 'Topology', href: '/topology', icon: '🌐' },
  { label: 'Alerts', href: '/alerts', icon: '⚠' },
  { label: 'Logs', href: '/logs', icon: '🧾' },
  { label: 'CVE', href: '/cve', icon: '🛡' },
  { label: 'Lifecycle', href: '/lifecycle', icon: '📅' },
  { label: 'API Docs', href: '/api-docs', icon: '📖' },
  { label: 'Settings', href: '/settings', icon: '⚙' },
]

interface Props {
  children: ReactNode
}

export default function Layout({ children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { connected } = useWebSocket('/ws/telemetry/')
  const location = useLocation()
  const navigate = useNavigate()
  const { username, logout } = useAuthStore()
  const { theme, toggle } = useThemeStore()
  const loadPrefs = usePreferencesStore((s) => s.load)

  // Load preferences once (syncs theme from the backend) for the session.
  useEffect(() => { loadPrefs() }, [loadPrefs])

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  const isDark = theme === 'dark' || (theme === 'system' && typeof document !== 'undefined' && document.documentElement.classList.contains('dark'))

  const currentPage = navItems.find((n) => location.pathname.startsWith(n.href))?.label ?? 'NetPulse'

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-20 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={clsx(
          'fixed inset-y-0 left-0 z-30 w-60 bg-gray-900 text-white flex flex-col transition-transform duration-200',
          'lg:static lg:translate-x-0',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 px-5 py-5 border-b border-gray-800">
          <div className="w-8 h-8 bg-blue-500 rounded-lg flex items-center justify-center text-white font-bold text-sm">
            NP
          </div>
          <span className="font-semibold text-lg tracking-tight">NetPulse</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-4 overflow-y-auto">
          {navItems.map((item) => (
            <NavLink
              key={item.href}
              to={item.href}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-5 py-3 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-300 hover:bg-gray-800 hover:text-white',
                )
              }
            >
              <span className="text-base w-5 text-center" aria-hidden>
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* User + connection status */}
        <div className="px-5 py-4 border-t border-gray-800 space-y-3">
          {username && (
            <div className="flex items-center justify-between gap-2">
              <NavLink
                to="/profile"
                onClick={() => setSidebarOpen(false)}
                className="flex items-center gap-2 text-xs text-gray-300 hover:text-white truncate"
                title="Profile & preferences"
              >
                <span aria-hidden>👤</span>
                <span className="truncate">{username}</span>
              </NavLink>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={toggle}
                  title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
                  className="text-sm text-gray-400 hover:text-white transition-colors"
                >
                  {isDark ? '☀️' : '🌙'}
                </button>
                <button
                  onClick={handleLogout}
                  className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
                >
                  Sign out
                </button>
              </div>
            </div>
          )}
          <div className="flex items-center gap-2 text-xs">
            <span
              className={clsx(
                'w-2 h-2 rounded-full',
                connected ? 'bg-green-400 animate-pulse' : 'bg-gray-500',
              )}
            />
            <span className={connected ? 'text-green-400' : 'text-gray-500'}>
              {connected ? 'Live' : 'Disconnected'}
            </span>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar (mobile) */}
        <header className="lg:hidden flex items-center gap-3 px-4 py-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 shadow-sm">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-1.5 rounded-md text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
            aria-label="Open menu"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="font-semibold text-gray-800 dark:text-gray-100">{currentPage}</span>
          <div className="ml-auto flex items-center gap-2 text-xs">
            <span
              className={clsx(
                'w-2 h-2 rounded-full',
                connected ? 'bg-green-400 animate-pulse' : 'bg-gray-400',
              )}
            />
            <span className={connected ? 'text-green-600' : 'text-gray-400'}>
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto p-4 lg:p-6">
          <ErrorBoundary>{children}</ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
