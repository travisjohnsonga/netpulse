import { type ReactNode } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Devices from './pages/Devices'
import Alerts from './pages/Alerts'
import CVE from './pages/CVE'
import Lifecycle from './pages/Lifecycle'
import Settings from './pages/Settings'
import Topology from './pages/Topology'
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
                <Route path="/topology" element={<Topology />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/cve" element={<CVE />} />
                <Route path="/lifecycle" element={<Lifecycle />} />
                <Route path="/settings" element={<Settings />} />
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
