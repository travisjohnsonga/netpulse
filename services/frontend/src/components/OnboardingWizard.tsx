import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { checkInfraHealth, type InfraHealth } from '../api/client'

interface Props {
  onComplete: () => void
}

// Display metadata keyed by the service name returned from
// GET /api/health/infrastructure/ ({ services: { postgres, valkey, ... } }).
const INFRA_SERVICES: { key: keyof InfraHealth['services']; name: string; description: string; port: string }[] = [
  { key: 'nats', name: 'NATS', description: 'Internal message bus', port: '4222' },
  { key: 'influxdb', name: 'InfluxDB', description: 'Time-series metrics', port: '8086' },
  { key: 'opensearch', name: 'OpenSearch', description: 'Log storage & search', port: '9200' },
  { key: 'postgres', name: 'PostgreSQL', description: 'Relational data store', port: '5432' },
  { key: 'valkey', name: 'Valkey', description: 'Cache & task queue', port: '6379' },
]

const PLATFORMS = [
  'IOS-XE',
  'IOS-XR',
  'NX-OS',
  'Junos',
  'EOS',
  'FortiOS',
  'Other',
]

const INTEGRATIONS = [
  { id: 'meraki', name: 'Cisco Meraki', icon: '🔵', description: 'Cloud-managed networking' },
  { id: 'mist', name: 'Juniper Mist', icon: '🟠', description: 'AI-driven Wi-Fi & WAN' },
  { id: 'unifi', name: 'Ubiquiti UniFi', icon: '⚫', description: 'Enterprise Wi-Fi & switching' },
  { id: 'slack', name: 'Slack', icon: '💬', description: 'Alert notifications' },
  { id: 'teams', name: 'Microsoft Teams', icon: '🟣', description: 'Alert notifications' },
  { id: 'pagerduty', name: 'PagerDuty', icon: '🔴', description: 'On-call alerting' },
]

export default function OnboardingWizard({ onComplete }: Props) {
  const [step, setStep] = useState(0)
  const [deviceForm, setDeviceForm] = useState({
    hostname: '',
    ip: '',
    platform: '',
    community: '',
  })
  const [connectedIntegrations, setConnectedIntegrations] = useState<Set<string>>(new Set())

  // Live infrastructure health (step 2).
  const [infra, setInfra] = useState<InfraHealth['services'] | null>(null)
  const [infraLoading, setInfraLoading] = useState(false)
  const [infraError, setInfraError] = useState(false)

  const loadInfra = useCallback(() => {
    setInfraLoading(true)
    setInfraError(false)
    checkInfraHealth()
      .then((d) => setInfra(d.services))
      .catch(() => setInfraError(true))
      .finally(() => setInfraLoading(false))
  }, [])

  // Fetch when the infrastructure step becomes active.
  useEffect(() => {
    if (step === 1) loadInfra()
  }, [step, loadInfra])

  const totalSteps = 4

  const toggleIntegration = (id: string) => {
    setConnectedIntegrations((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 to-gray-800 flex items-center justify-center p-4">
      <div className="w-full max-w-lg">
        {/* Progress bar */}
        <div className="mb-6">
          <div className="flex justify-between text-xs text-gray-400 mb-2">
            <span>Step {step + 1} of {totalSteps}</span>
            <span>{Math.round(((step + 1) / totalSteps) * 100)}% complete</span>
          </div>
          <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-300"
              style={{ width: `${((step + 1) / totalSteps) * 100}%` }}
            />
          </div>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl overflow-hidden">
          {/* Step 1: Welcome */}
          {step === 0 && (
            <div className="p-8 text-center">
              <div className="w-16 h-16 bg-blue-100 dark:bg-blue-900/30 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <span className="text-3xl">🌐</span>
              </div>
              <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-3">
                Welcome to NetPulse
              </h1>
              <p className="text-gray-500 dark:text-gray-400 mb-2 leading-relaxed">
                Your open-source network intelligence platform. NetPulse gives you real-time
                visibility into your network infrastructure — telemetry, alerts, CVEs, and lifecycle
                management in one place.
              </p>
              <p className="text-sm text-gray-400 dark:text-gray-500 mb-8">
                This wizard will guide you through initial setup. It takes about 2 minutes.
              </p>
              <div className="grid grid-cols-3 gap-4 mb-8 text-center">
                {[
                  { icon: '📡', label: 'Streaming telemetry' },
                  { icon: '🛡', label: 'CVE intelligence' },
                  { icon: '⚠', label: 'Real-time alerts' },
                ].map((f) => (
                  <div key={f.label} className="p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                    <div className="text-2xl mb-1">{f.icon}</div>
                    <div className="text-xs text-gray-600 dark:text-gray-400 font-medium">{f.label}</div>
                  </div>
                ))}
              </div>
              <button
                onClick={() => setStep(1)}
                className="w-full py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
              >
                Get Started
              </button>
            </div>
          )}

          {/* Step 2: Infrastructure */}
          {step === 1 && (
            <div className="p-8">
              <div className="flex items-start justify-between mb-1">
                <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">Connect Infrastructure</h2>
                <button
                  onClick={loadInfra}
                  disabled={infraLoading}
                  className="text-xs text-blue-600 hover:text-blue-800 font-medium disabled:opacity-50"
                >
                  {infraLoading ? 'Checking…' : 'Re-check'}
                </button>
              </div>
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                Live status from <code className="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">/api/health/infrastructure/</code>.
                All services should be running if you started the platform with{' '}
                <code className="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">docker compose up -d</code>.
              </p>
              {infraError && (
                <div className="mb-4 text-xs text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg p-3">
                  Couldn't reach the API to check service health. Make sure the <code>api</code> service is up, then Re-check.
                </div>
              )}
              <div className="space-y-3 mb-8">
                {INFRA_SERVICES.map((svc) => {
                  const up = infra ? infra[svc.key] : null
                  const dotCls = up == null ? 'bg-yellow-400' : up ? 'bg-green-500' : 'bg-red-500'
                  const labelCls = up == null ? 'text-yellow-600' : up ? 'text-green-600' : 'text-red-600'
                  const label = infraLoading && infra == null ? 'checking…' : up == null ? 'unknown' : up ? 'healthy' : 'down'
                  return (
                    <div
                      key={svc.key}
                      className="flex items-center gap-4 p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700"
                    >
                      <div className={clsx('w-2 h-2 rounded-full flex-shrink-0', dotCls, up && 'animate-pulse')} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm text-gray-800 dark:text-gray-100">{svc.name}</span>
                          <span className="text-xs text-gray-400 dark:text-gray-500">:{svc.port}</span>
                        </div>
                        <span className="text-xs text-gray-500 dark:text-gray-400">{svc.description}</span>
                      </div>
                      <span className={clsx('text-xs font-medium', labelCls)}>{label}</span>
                    </div>
                  )
                })}
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => setStep(0)}
                  className="flex-1 py-3 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                >
                  Back
                </button>
                <button
                  onClick={() => setStep(2)}
                  className="flex-1 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {/* Step 3: Add first device */}
          {step === 2 && (
            <div className="p-8">
              <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100 mb-1">Add Your First Device</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                Enter a network device to start monitoring. You can add more later from the Devices page.
              </p>
              <div className="space-y-4 mb-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Hostname
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. core-router-01"
                    value={deviceForm.hostname}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, hostname: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    IP Address
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. 192.168.1.1"
                    value={deviceForm.ip}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, ip: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Platform
                  </label>
                  <select
                    value={deviceForm.platform}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, platform: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 dark:text-gray-100"
                  >
                    <option value="">Select platform...</option>
                    {PLATFORMS.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    SNMP Community String
                  </label>
                  <input
                    type="password"
                    placeholder="e.g. public"
                    value={deviceForm.community}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, community: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-gray-100"
                  />
                </div>
              </div>
              <div className="text-center mb-6">
                <span className="text-sm text-gray-400 dark:text-gray-500">— or —</span>
                <div className="mt-2">
                  <button
                    type="button"
                    className="text-sm text-blue-600 hover:text-blue-800 font-medium"
                    onClick={() => setStep(3)}
                  >
                    Skip — use auto-discovery instead
                  </button>
                </div>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={() => setStep(1)}
                  className="flex-1 py-3 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                >
                  Back
                </button>
                <button
                  onClick={() => setStep(3)}
                  className="flex-1 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
                >
                  {deviceForm.hostname ? 'Add Device' : 'Skip for Now'}
                </button>
              </div>
            </div>
          )}

          {/* Step 4: Integrations */}
          {step === 3 && (
            <div className="p-8">
              <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100 mb-1">Set Up Integrations</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                Connect cloud-managed platforms and alert channels. You can configure these later
                in Settings.
              </p>
              <div className="grid grid-cols-2 gap-3 mb-8">
                {INTEGRATIONS.map((intg) => (
                  <button
                    key={intg.id}
                    onClick={() => toggleIntegration(intg.id)}
                    className={clsx(
                      'flex flex-col items-start gap-1 p-3 rounded-lg border-2 text-left transition-all',
                      connectedIntegrations.has(intg.id)
                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30'
                        : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 bg-gray-50 dark:bg-gray-900/50',
                    )}
                  >
                    <span className="text-xl">{intg.icon}</span>
                    <span className="font-medium text-sm text-gray-800 dark:text-gray-100">{intg.name}</span>
                    <span className="text-xs text-gray-500 dark:text-gray-400">{intg.description}</span>
                    {connectedIntegrations.has(intg.id) && (
                      <span className="text-xs text-blue-600 font-medium mt-0.5">Selected</span>
                    )}
                  </button>
                ))}
              </div>
              <p className="text-xs text-gray-400 dark:text-gray-500 mb-6">
                API credentials for selected integrations will be configured in Settings after setup.
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setStep(2)}
                  className="flex-1 py-3 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                >
                  Back
                </button>
                <button
                  onClick={onComplete}
                  className="flex-1 py-3 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium transition-colors"
                >
                  Go to Dashboard
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
