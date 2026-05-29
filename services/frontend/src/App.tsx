import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Devices from './pages/Devices'
import Alerts from './pages/Alerts'
import CVE from './pages/CVE'
import Lifecycle from './pages/Lifecycle'
import Settings from './pages/Settings'
import OnboardingWizard from './components/OnboardingWizard'

export default function App() {
  const onboarded = localStorage.getItem('netpulse_onboarded')

  if (!onboarded) {
    return (
      <OnboardingWizard
        onComplete={() => {
          localStorage.setItem('netpulse_onboarded', '1')
          window.location.reload()
        }}
      />
    )
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/devices" element={<Devices />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/cve" element={<CVE />} />
        <Route path="/lifecycle" element={<Lifecycle />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Layout>
  )
}
