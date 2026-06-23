import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import SetupRequired, { OpenBaoDegradedBanner } from './pages/SetupRequired'
import { fetchSetupStatus, fetchOnboardingStatus, completeOnboarding, fetchMe, type SetupStatus } from './api/client'
import Dashboard from './pages/Dashboard'
import Devices from './pages/Devices'
import Profile from './pages/Profile'
import DeviceDetail from './pages/DeviceDetail'
import Sites from './pages/Sites'
import SiteDetail from './pages/SiteDetail'
import ConfigCompare from './pages/ConfigCompare'
import Alerts from './pages/Alerts'
import Logs from './pages/Logs'
import Flows from './pages/Flows'
import Checks from './pages/Checks'
import CVE from './pages/CVE'
import Lifecycle from './pages/Lifecycle'
import Compliance from './pages/Compliance'
import Reports from './pages/Reports'
import Settings from './pages/Settings'
import Polling from './pages/settings/Polling'
import HostnameRules from './pages/settings/HostnameRules'
import LogFilters from './pages/settings/LogFilters'
import Credentials from './pages/settings/Credentials'
import Integrations from './pages/settings/Integrations'
import Discovery from './pages/settings/Discovery'
import Collectors from './pages/settings/Collectors'
import Agents from './pages/settings/Agents'
import DataSources from './pages/settings/DataSources'
import PlatformStatus from './pages/settings/PlatformStatus'
import AccessRoles from './pages/settings/RbacRoles'
import RequireCapability from './components/RequireCapability'
import {
  UsersAccessSettings, AlertingSettings, NetworkDeviceSettings,
  ComplianceSettings, SystemSettings,
} from './pages/settings/groups'
import Servers from './pages/Servers'
import ServerDetail from './pages/ServerDetail'
import Topology from './pages/Topology'
import NetworkManualLinks from './pages/NetworkManualLinks'
import Circuits from './pages/Circuits'
import Wireless from './pages/Wireless'
import WirelessLocation from './pages/WirelessLocation'
import TVLauncher from './pages/tv/TVLauncher'
import TVNetwork from './pages/tv/TVNetwork'
import TVWireless from './pages/tv/TVWireless'
import TVWirelessMist from './pages/tv/TVWirelessMist'
import TVSecurity from './pages/tv/TVSecurity'
import TVOps from './pages/tv/TVOps'
import TVSites from './pages/tv/TVSites'
import TVServers from './pages/tv/TVServers'
import TVCompliance from './pages/tv/TVCompliance'
import TVRotate from './pages/tv/TVRotate'
import LldpNeighbors from './pages/LldpNeighbors'
import NetworkLookup from './pages/NetworkLookup'
import ApiDocs from './pages/ApiDocs'
import Login from './pages/Login'
import ForcePasswordChange from './pages/ForcePasswordChange'
import OnboardingWizard from './components/OnboardingWizard'
import { useAuthStore } from './store/authStore'
import { ChatOpsProvider } from './store/chatOpsStore'

function RequireAuth({ children }: { children: ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const location = useLocation()
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return <>{children}</>
}

function AppRoutes() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword)
  // Onboarding visibility is decided by the backend (no devices AND this user
  // hasn't dismissed it) — not a localStorage flag, which made the wizard
  // reappear on every new browser/session even on a configured system.
  const [onboarding, setOnboarding] = useState<'loading' | 'show' | 'skip'>('loading')

  useEffect(() => {
    if (!isAuthenticated) { setOnboarding('skip'); return }
    setOnboarding('loading')
    fetchOnboardingStatus()
      .then((s) => setOnboarding(s.show_onboarding ? 'show' : 'skip'))
      .catch(() => setOnboarding('skip'))   // fail open → straight to the app
  }, [isAuthenticated])

  // RBAC Track 2 Phase C: the JWT only carries the legacy role, so resolve the
  // user's effective capabilities from /me on auth-init (and re-resolve on every
  // mount/reload so capability changes + custom roles repopulate). Failures
  // leave capabilities empty (deny-by-default in the UI; the API stays the boundary).
  useEffect(() => {
    if (!isAuthenticated) return
    fetchMe()
      .then((me) => useAuthStore.getState().setCapabilities(me.capabilities ?? [], me.rbac_role ?? null))
      .catch(() => { /* keep whatever was persisted; API 403s remain authoritative */ })
  }, [isAuthenticated])

  // Forced password change takes precedence over onboarding and the app: an
  // account on the default password (must_change_password) is confined to
  // /change-password until it picks a new one (which mints fresh tokens that
  // clear the flag). Placed after all hooks to keep their call order stable.
  if (isAuthenticated && mustChangePassword) {
    return (
      <Routes>
        <Route path="/change-password" element={<ForcePasswordChange />} />
        <Route path="*" element={<Navigate to="/change-password" replace />} />
      </Routes>
    )
  }

  if (isAuthenticated && onboarding === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
        <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (isAuthenticated && onboarding === 'show') {
    return (
      <OnboardingWizard
        onComplete={() => {
          completeOnboarding().finally(() => {
            setOnboarding('skip')
            window.location.replace('/dashboard')
          })
        }}
      />
    )
  }

  return (
    <Routes>
      <Route path="/login" element={isAuthenticated ? <Navigate to="/dashboard" replace /> : <Login />} />
      {/* TV dashboards: auth-required but NO app chrome (no sidebar/topnav) so a
          NOC monitor can be pointed at a bookmarked /tv/* URL fullscreen. */}
      <Route
        path="/tv/*"
        element={
          <RequireAuth>
            <Routes>
              <Route path="/" element={<TVLauncher />} />
              <Route path="network" element={<TVNetwork />} />
              <Route path="wireless" element={<TVWireless />} />
              <Route path="wireless-mist" element={<TVWirelessMist />} />
              <Route path="security" element={<TVSecurity />} />
              <Route path="ops" element={<TVOps />} />
              <Route path="sites" element={<TVSites />} />
              <Route path="servers" element={<TVServers />} />
              <Route path="compliance" element={<TVCompliance />} />
              <Route path="rotate" element={<TVRotate />} />
            </Routes>
          </RequireAuth>
        }
      />
      <Route
        path="/*"
        element={
          <RequireAuth>
            {/* ChatOpsProvider wraps the authenticated app (above the page
                Routes) so the chat panel's open state + message history survive
                navigation between pages. */}
            <ChatOpsProvider>
            <Layout>
              <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/profile" element={<Profile />} />
                <Route path="/devices" element={<Devices />} />
                <Route path="/devices/:id" element={<DeviceDetail />} />
                <Route path="/servers" element={<Servers />} />
                <Route path="/servers/:id" element={<ServerDetail />} />
                <Route path="/sites" element={<Sites />} />
                <Route path="/sites/:id" element={<SiteDetail />} />
                <Route path="/configs/compare" element={<ConfigCompare />} />
                <Route path="/topology" element={<Topology />} />
                <Route path="/network/manual-links" element={<NetworkManualLinks />} />
                <Route path="/circuits" element={<Circuits />} />
                <Route path="/wireless" element={<Wireless />} />
                <Route path="/wireless/location" element={<WirelessLocation />} />
                <Route path="/lldp-neighbors" element={<LldpNeighbors />} />
                <Route path="/api-docs" element={<ApiDocs />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/logs" element={<Logs />} />
                <Route path="/flows" element={<Flows />} />
                <Route path="/checks" element={<Checks />} />
                <Route path="/network/lookup" element={<NetworkLookup />} />
                <Route path="/lookup" element={<Navigate to="/network/lookup" replace />} />
                <Route path="/cve" element={<CVE />} />
                <Route path="/lifecycle" element={<Lifecycle />} />
                <Route path="/compliance" element={<Compliance />} />
                <Route path="/reports" element={<Reports />} />
                <Route path="/settings" element={<Settings />}>
                  <Route index element={<Navigate to="users" replace />} />
                  {/* Grouped (tabbed) settings */}
                  <Route path="users" element={<UsersAccessSettings />} />
                  <Route path="alerting" element={<AlertingSettings />} />
                  <Route path="network-devices" element={<NetworkDeviceSettings />} />
                  <Route path="compliance" element={<ComplianceSettings />} />
                  <Route path="system" element={<SystemSettings />} />
                  {/* Standalone settings */}
                  {/* RBAC role management — its own route, guarded by rbac:manage
                      (NotAuthorized for anyone else, incl. deep-links). Distinct
                      from the device-roles tab under network-devices. */}
                  <Route path="access-roles" element={<RequireCapability capability="rbac:manage"><AccessRoles /></RequireCapability>} />
                  <Route path="integrations" element={<Integrations />} />
                  <Route path="collectors" element={<Collectors />} />
                  <Route path="agents" element={<Agents />} />
                  <Route path="discovery" element={<Discovery />} />
                  <Route path="hostname-rules" element={<HostnameRules />} />
                  <Route path="log-filters" element={<LogFilters />} />
                  <Route path="credentials" element={<Credentials />} />
                  <Route path="polling" element={<Polling />} />
                  <Route path="data-sources" element={<DataSources />} />
                  <Route path="platform-status" element={<PlatformStatus />} />
                  {/* Redirects from old flat paths → grouped tabs (keep bookmarks working) */}
                  <Route path="general" element={<Navigate to="/settings/system?tab=general" replace />} />
                  <Route path="sso" element={<Navigate to="/settings/users?tab=sso" replace />} />
                  <Route path="roles" element={<Navigate to="/settings/network-devices?tab=roles" replace />} />
                  <Route path="mibs" element={<Navigate to="/settings/network-devices?tab=mibs" replace />} />
                  <Route path="alert-routing" element={<Navigate to="/settings/alerting?tab=routing" replace />} />
                  <Route path="compliance-templates" element={<Navigate to="/settings/compliance?tab=templates" replace />} />
                  <Route path="os-versions" element={<Navigate to="/settings/compliance?tab=os-versions" replace />} />
                  <Route path="fleet-inventory" element={<Navigate to="/settings/compliance?tab=fleet-inventory" replace />} />
                  <Route path="certificates" element={<Navigate to="/settings/system?tab=certificates" replace />} />
                  <Route path="audit-log" element={<Navigate to="/settings/system?tab=audit-log" replace />} />
                  <Route path="data-retention" element={<Navigate to="/settings/system?tab=data-retention" replace />} />
                </Route>
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Routes>
            </Layout>
            </ChatOpsProvider>
          </RequireAuth>
        }
      />
    </Routes>
  )
}

/**
 * Gates the whole app on first-run setup. Checks /api/setup/status/ (no auth)
 * before rendering anything else:
 *   - setup_complete === false → show the SetupRequired welcome page only.
 *   - setup_complete === true but OpenBao unreachable → render the app with a
 *     persistent degraded banner (don't lock admins out of a working system).
 *   - backend unreachable → fail open (let the app/login surface the error).
 */
function SetupGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [loaded, setLoaded] = useState(false)

  const check = useCallback(() => {
    return fetchSetupStatus()
      .then(setStatus)
      .catch(() => setStatus(null))   // backend down → fail open
      .finally(() => setLoaded(true))
  }, [])

  useEffect(() => { check() }, [check])

  if (!loaded) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
        <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (status && !status.setup_complete) {
    // On completion, re-check; flips into the app (lands on /login).
    return <SetupRequired status={status} onComplete={() => { setLoaded(false); check() }} />
  }

  const degraded = !!status && status.setup_complete && !status.openbao_healthy
  return (
    <>
      {degraded && <OpenBaoDegradedBanner />}
      {children}
    </>
  )
}

export default function App() {
  return (
    <SetupGate>
      <AppRoutes />
    </SetupGate>
  )
}
