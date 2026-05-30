import { type ReactNode } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Devices from './pages/Devices'
import DeviceDetail from './pages/DeviceDetail'
import Sites from './pages/Sites'
import SiteDetail from './pages/SiteDetail'
import ConfigCompare from './pages/ConfigCompare'
import Alerts from './pages/Alerts'
import Logs from './pages/Logs'
import CVE from './pages/CVE'
import Lifecycle from './pages/Lifecycle'
import Settings from './pages/Settings'
import General from './pages/settings/General'
import Polling from './pages/settings/Polling'
import Users from './pages/settings/Users'
import Credentials from './pages/settings/Credentials'
import Integrations from './pages/settings/Integrations'
import Alerting from './pages/settings/Alerting'
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
                <Route path="/devices" element={<Devices />} />
                <Route path="/devices/:id" element={<DeviceDetail />} />
                <Route path="/sites" element={<Sites />} />
                <Route path="/sites/:id" element={<SiteDetail />} />
                <Route path="/configs/compare" element={<ConfigCompare />} />
                <Route path="/topology" element={<Topology />} />
                <Route path="/api-docs" element={<ApiDocs />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/logs" element={<Logs />} />
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

export default function App() {
  return <AppRoutes />
}
