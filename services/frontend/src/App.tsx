import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import SetupRequired, { OpenBaoDegradedBanner } from './pages/SetupRequired'
import { fetchSetupStatus, type SetupStatus } from './api/client'
import Dashboard from './pages/Dashboard'
import Devices from './pages/Devices'
import Profile from './pages/Profile'
import DeviceDetail from './pages/DeviceDetail'
import Sites from './pages/Sites'
import SiteDetail from './pages/SiteDetail'
import ConfigCompare from './pages/ConfigCompare'
import Alerts from './pages/Alerts'
import Logs from './pages/Logs'
import Checks from './pages/Checks'
import CVE from './pages/CVE'
import Lifecycle from './pages/Lifecycle'
import Settings from './pages/Settings'
import General from './pages/settings/General'
import Polling from './pages/settings/Polling'
import Users from './pages/settings/Users'
import Credentials from './pages/settings/Credentials'
import Integrations from './pages/settings/Integrations'
import Alerting from './pages/settings/Alerting'
import AlertRouting from './pages/settings/AlertRouting'
import Discovery from './pages/settings/Discovery'
import Collectors from './pages/settings/Collectors'
import DataSources from './pages/settings/DataSources'
import Certificates from './pages/settings/Certificates'
import System from './pages/settings/System'
import Topology from './pages/Topology'
import ApiDocs from './pages/ApiDocs'
import Login from './pages/Login'
import OnboardingWizard from './components/OnboardingWizard'
import { useAuthStore } from './store/authStore'

function RequireAuth({ children }: { children: ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const location = useLocation()
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return <>{children}</>
}

function AppRoutes() {
  const onboarded = localStorage.getItem('netpulse_onboarded')
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)

  if (isAuthenticated && !onboarded) {
    return (
      <OnboardingWizard
        onComplete={() => {
          localStorage.setItem('netpulse_onboarded', '1')
          window.location.replace('/dashboard')
        }}
      />
    )
  }

  return (
    <Routes>
      <Route path="/login" element={isAuthenticated ? <Navigate to="/dashboard" replace /> : <Login />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <Layout>
              <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/profile" element={<Profile />} />
                <Route path="/devices" element={<Devices />} />
                <Route path="/devices/:id" element={<DeviceDetail />} />
                <Route path="/sites" element={<Sites />} />
                <Route path="/sites/:id" element={<SiteDetail />} />
                <Route path="/configs/compare" element={<ConfigCompare />} />
                <Route path="/topology" element={<Topology />} />
                <Route path="/api-docs" element={<ApiDocs />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/logs" element={<Logs />} />
                <Route path="/checks" element={<Checks />} />
                <Route path="/cve" element={<CVE />} />
                <Route path="/lifecycle" element={<Lifecycle />} />
                <Route path="/settings" element={<Settings />}>
                  <Route index element={<Navigate to="general" replace />} />
                  <Route path="general" element={<General />} />
                  <Route path="polling" element={<Polling />} />
                  <Route path="users" element={<Users />} />
                  <Route path="credentials" element={<Credentials />} />
                  <Route path="integrations" element={<Integrations />} />
                  <Route path="alerting" element={<Alerting />} />
                  <Route path="alert-routing" element={<AlertRouting />} />
                  <Route path="discovery" element={<Discovery />} />
                  <Route path="collectors" element={<Collectors />} />
                  <Route path="data-sources" element={<DataSources />} />
                  <Route path="certificates" element={<Certificates />} />
                  <Route path="system" element={<System />} />
                </Route>
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Routes>
            </Layout>
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
