import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { useWebSocket } from '../hooks/useWebSocket'
import { fetchUndiscoveredLldpCount } from '../api/client'
import { useAuthStore, useCapabilities } from '../store/authStore'
import { useThemeStore } from '../store/themeStore'
import { usePreferencesStore } from '../store/preferencesStore'
import { useSite, useSiteStore } from '../store/siteStore'
import ErrorBoundary from './ErrorBoundary'
import VersionBadge from './VersionBadge'
import ServerClock from './ServerClock'
import HeaderSearch from './HeaderSearch'
import SiteSelector from './SiteSelector'
import LogoMark from './LogoMark'
import ChatOpsPanel from './ChatOpsPanel'
import ForbiddenNotice from './ForbiddenNotice'

// Drop nav leaves the user lacks the capability for (groups with no remaining
// children are dropped too). Convenience gating; the API 403 is the boundary.
function filterNav(entries: NavEntry[], caps: string[]): NavEntry[] {
  const allowed = (i: NavItem) => !i.requiredCapability || caps.includes(i.requiredCapability)
  return entries.flatMap<NavEntry>((e) => {
    if (!isGroup(e)) return allowed(e) ? [e] : []
    const children = e.children.filter(allowed)
    return children.length ? [{ ...e, children }] : []
  })
}

interface NavItem {
  label: string
  href: string
  icon: string
  divider?: boolean // render a section divider above this item
  badge?: 'lldp'    // render a live count badge keyed by this source
  // RBAC Track 2 Phase C: hide this leaf when the user lacks the capability that
  // gates its primary API. Convenience only — the API 403 is the real boundary.
  requiredCapability?: string
}

interface NavGroup {
  label: string
  icon: string
  divider?: boolean
  children: NavItem[]
}

type NavEntry = NavItem | NavGroup

const isGroup = (e: NavEntry): e is NavGroup => 'children' in e

// Sidebar nav: a mix of leaf links and collapsible groups. The Network group
// gathers the device/wireless/topology/flow pages; it auto-expands when the
// current route matches one of its children.
const navEntries: NavEntry[] = [
  { label: 'Dashboard', href: '/dashboard', icon: '▦' },
  // Sites is a top-level organizational concept, not a network-monitoring view.
  { label: 'Sites', href: '/sites', icon: '🏢' },
  {
    label: 'Network', icon: '🌐', children: [
      { label: 'Devices', href: '/devices', icon: '⬡', requiredCapability: 'device:view' },
      { label: 'Wireless', href: '/wireless', icon: '📶' },
      { label: 'Wireless Location', href: '/wireless/location', icon: '📍' },
      { label: 'Topology', href: '/topology', icon: '🗺️' },
      { label: 'Manual Links', href: '/network/manual-links', icon: '🔗' },
      { label: 'Circuits', href: '/circuits', icon: '🔌', requiredCapability: 'circuit:view' },
      { label: 'LLDP Neighbors', href: '/lldp-neighbors', icon: '📡', badge: 'lldp' },
      { label: 'Flow Analytics', href: '/flows', icon: '〰️', requiredCapability: 'flow:view' },
      { label: 'IP/MAC Lookup', href: '/network/lookup', icon: '🔍' },
      { label: 'Compare', href: '/configs/compare', icon: '🔀' },
    ],
  },
  {
    label: 'Servers', icon: '🖥️', children: [
      { label: 'All Servers', href: '/servers', icon: '🖥️', requiredCapability: 'agent:view' },
      { label: 'Agents', href: '/settings/agents', icon: '🛰️', requiredCapability: 'agent:view' },
    ],
  },
  { label: 'Alerts', href: '/alerts', icon: '⚠', divider: true, requiredCapability: 'alert:view' },
  { label: 'Logs', href: '/logs', icon: '🧾', requiredCapability: 'log:view' },
  { label: 'Service Checks', href: '/checks', icon: '✓', requiredCapability: 'check:view' },
  { label: 'CVE', href: '/cve', icon: '🛡', divider: true, requiredCapability: 'cve:view' },
  { label: 'Lifecycle', href: '/lifecycle', icon: '📅', requiredCapability: 'lifecycle:view' },
  { label: 'Compliance', href: '/compliance', icon: '📋', requiredCapability: 'compliance:view' },
  { label: 'Reports', href: '/reports', icon: '📈', requiredCapability: 'report:view' },
  { label: 'API Docs', href: '/api-docs', icon: '📖', divider: true },
  { label: 'Settings', href: '/settings', icon: '⚙' },
  { label: 'TV Dashboards', href: '/tv', icon: '📺', divider: true },
]

// A single leaf nav link (top level or nested inside a group).
function NavLeaf({ item, nested, badge, onNavigate }: {
  item: NavItem; nested?: boolean; badge: number; onNavigate: () => void
}) {
  return (
    <NavLink
      to={item.href}
      onClick={onNavigate}
      className={({ isActive }) =>
        clsx(
          'flex items-center gap-3 text-sm font-medium transition-colors',
          nested ? 'pl-11 pr-5 py-2.5' : 'px-5 py-3',
          isActive ? 'bg-blue-600 text-white' : 'text-gray-300 hover:bg-gray-800 hover:text-white',
        )
      }
    >
      <span className="flex-1">{item.label}</span>
      {badge > 0 && (
        <span
          className="ml-auto min-w-[1.25rem] px-1.5 py-0.5 text-[11px] font-semibold leading-none text-center rounded-full bg-red-500 text-white"
          title={`${badge} not in inventory`}
        >
          {badge}
        </span>
      )}
    </NavLink>
  )
}

// A collapsible group; auto-expands when one of its child routes is active.
function NavGroupItem({ group, badgeFor, onNavigate }: {
  group: NavGroup; badgeFor: (i: NavItem) => number; onNavigate: () => void
}) {
  const location = useLocation()
  const active = group.children.some((c) => location.pathname.startsWith(c.href))
  const [open, setOpen] = useState(active)
  useEffect(() => { if (active) setOpen(true) }, [active])
  const childBadges = group.children.reduce((sum, c) => sum + badgeFor(c), 0)
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className={clsx(
          'w-full flex items-center gap-3 px-5 py-3 text-sm font-medium transition-colors',
          active ? 'text-white' : 'text-gray-300 hover:bg-gray-800 hover:text-white',
        )}
      >
        <span className="flex-1 text-left">{group.label}</span>
        {childBadges > 0 && !open && (
          <span className="min-w-[1.25rem] px-1.5 py-0.5 text-[11px] font-semibold leading-none text-center rounded-full bg-red-500 text-white">
            {childBadges}
          </span>
        )}
        <span className={clsx('text-[10px] text-gray-400 transition-transform', open && 'rotate-90')} aria-hidden>▶</span>
      </button>
      {open && (
        <div className="py-1">
          {group.children.map((child) => (
            <NavLeaf key={child.href} item={child} nested badge={badgeFor(child)} onNavigate={onNavigate} />
          ))}
        </div>
      )}
    </div>
  )
}

interface Props {
  children: ReactNode
}

export default function Layout({ children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { connected } = useWebSocket('/ws/telemetry/')
  const location = useLocation()
  const navigate = useNavigate()
  const { username, name, email, isAuthenticated, logout } = useAuthStore()
  const { theme, toggle } = useThemeStore()
  const loadPrefs = usePreferencesStore((s) => s.load)
  const loadSites = useSiteStore((s) => s.loadSites)
  const { selectedSite, selectedSiteName, setSelectedSite } = useSite()

  // Load preferences once (syncs theme from the backend) for the session.
  useEffect(() => { loadPrefs() }, [loadPrefs])

  // Load the site list once so the global site selector is populated.
  useEffect(() => { if (isAuthenticated) loadSites() }, [isAuthenticated, loadSites])

  // Live count of LLDP neighbors not yet in inventory → sidebar badge.
  // Refresh every 5 minutes; the badge hides itself when the count is 0.
  const [lldpCount, setLldpCount] = useState(0)
  useEffect(() => {
    if (!isAuthenticated) return
    let active = true
    const tick = () => fetchUndiscoveredLldpCount().then((n) => { if (active) setLldpCount(n) }).catch(() => {})
    tick()
    const id = setInterval(tick, 5 * 60 * 1000)
    return () => { active = false; clearInterval(id) }
  }, [isAuthenticated])

  const badgeFor = (item: NavItem): number => (item.badge === 'lldp' ? lldpCount : 0)

  // Works for both local and SSO users: prefer full name, then username, then
  // the email local-part. SSO tokens may not carry a username.
  const displayName = name || username || email?.split('@')[0] || 'Account'
  const initials = displayName
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join('') || '?'

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  const isDark = theme === 'dark' || (theme === 'system' && typeof document !== 'undefined' && document.documentElement.classList.contains('dark'))

  // Capability-aware nav (RBAC Track 2 Phase C). `currentPage` still derives from
  // the full set so the header titles a page the user reached directly.
  const caps = useCapabilities()
  const entries = filterNav(navEntries, caps)
  const allLeaves = navEntries.flatMap((e) => (isGroup(e) ? e.children : [e]))
  const currentPage = allLeaves.find((n) => location.pathname.startsWith(n.href))?.label ?? 'spane'

  return (
    <div className="h-screen overflow-hidden bg-gray-50 dark:bg-gray-950 flex">
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
          <LogoMark className="w-8 h-8 text-blue-500" />
          <span className="font-semibold text-lg tracking-tight">spane</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-4 overflow-y-auto">
          {entries.map((entry) => (
            <div key={isGroup(entry) ? entry.label : entry.href}>
              {entry.divider && <div className="my-2 mx-5 border-t border-gray-800" aria-hidden />}
              {isGroup(entry)
                ? <NavGroupItem group={entry} badgeFor={badgeFor} onNavigate={() => setSidebarOpen(false)} />
                : <NavLeaf item={entry} badge={badgeFor(entry)} onNavigate={() => setSidebarOpen(false)} />}
            </div>
          ))}
        </nav>

        {/* User + connection status */}
        <div className="px-5 py-4 border-t border-gray-800 space-y-3">
          {/* Shown for any authenticated user — local OR SSO. Gating on
              isAuthenticated (not username) keeps the toggle + sign out visible
              even when an SSO token carries no username. */}
          {isAuthenticated && (
            <div className="flex items-center justify-between gap-2">
              <NavLink
                to="/profile"
                onClick={() => setSidebarOpen(false)}
                className="flex items-center gap-2 text-xs text-gray-300 hover:text-white truncate min-w-0"
                title={email || displayName}
              >
                <span
                  aria-hidden
                  className="shrink-0 w-6 h-6 rounded-full bg-blue-600 text-white text-[10px] font-semibold flex items-center justify-center"
                >
                  {initials}
                </span>
                <span className="truncate">{displayName}</span>
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
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="flex items-center gap-2">
              <span
                className={clsx(
                  'w-2 h-2 rounded-full',
                  connected ? 'bg-green-400 animate-pulse' : 'bg-gray-500',
                )}
              />
              <span className={connected ? 'text-green-400' : 'text-gray-500'}>
                {connected ? 'Live' : 'Disconnected'}
              </span>
            </span>
            <VersionBadge />
          </div>
          <div className="mt-1.5 flex justify-center">
            <ServerClock />
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Top bar — page title (mobile menu toggle) + IP/MAC quick-search */}
        <header className="flex items-center gap-3 px-4 py-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 shadow-sm">
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden p-1.5 rounded-md text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
            aria-label="Open menu"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="font-semibold text-gray-800 dark:text-gray-100">{currentPage}</span>
          <div className="ml-auto flex items-center gap-3 text-xs">
            <SiteSelector />
            <HeaderSearch />
            <span className="lg:hidden flex items-center gap-2">
              <span
                className={clsx(
                  'w-2 h-2 rounded-full',
                  connected ? 'bg-green-400 animate-pulse' : 'bg-gray-400',
                )}
              />
              <span className={connected ? 'text-green-600' : 'text-gray-400'}>
                {connected ? 'Live' : 'Offline'}
              </span>
            </span>
          </div>
        </header>

        {/* Active site-filter banner (Option B): a clear, dismissible reminder
            that every site-aware page is scoped to one site. */}
        {selectedSite && (
          <div className="flex items-center gap-2 px-4 py-1.5 bg-blue-50 dark:bg-blue-900/30 border-b border-blue-200 dark:border-blue-800 text-sm text-blue-800 dark:text-blue-300">
            <span aria-hidden>📍</span>
            <span>Filtered to: <strong>{selectedSiteName}</strong></span>
            <button
              onClick={() => setSelectedSite(null)}
              className="ml-auto px-2 py-0.5 text-xs font-medium rounded border border-blue-300 dark:border-blue-700 hover:bg-blue-100 dark:hover:bg-blue-800/50 transition-colors"
            >
              Clear
            </button>
          </div>
        )}

        {/* Page content — the only scroll region; sidebar + header stay fixed */}
        <main className="flex-1 min-h-0 overflow-auto p-4 lg:p-6">
          <ErrorBoundary>{children}</ErrorBoundary>
        </main>
      </div>

      {/* In-UI ChatOps chat — a sibling to {children} (outside the page Routes),
          so it overlays every page and persists across navigation. */}
      <ChatOpsPanel />

      {/* Global "Not authorized" banner for any 403 the API client surfaces. */}
      <ForbiddenNotice />
    </div>
  )
}
