import { useState } from 'react'
import clsx from 'clsx'

interface Props {
  onComplete: () => void
}

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

        <div className="bg-white rounded-2xl shadow-2xl overflow-hidden">
          {/* Step 1: Welcome */}
          {step === 0 && (
            <div className="p-8 text-center">
              <div className="w-16 h-16 bg-blue-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <span className="text-3xl">🌐</span>
              </div>
              <h1 className="text-2xl font-bold text-gray-900 mb-3">
                Welcome to NetPulse
              </h1>
              <p className="text-gray-500 mb-2 leading-relaxed">
                Your open-source network intelligence platform. NetPulse gives you real-time
                visibility into your network infrastructure — telemetry, alerts, CVEs, and lifecycle
                management in one place.
              </p>
              <p className="text-sm text-gray-400 mb-8">
                This wizard will guide you through initial setup. It takes about 2 minutes.
              </p>
              <div className="grid grid-cols-3 gap-4 mb-8 text-center">
                {[
                  { icon: '📡', label: 'Streaming telemetry' },
                  { icon: '🛡', label: 'CVE intelligence' },
                  { icon: '⚠', label: 'Real-time alerts' },
                ].map((f) => (
                  <div key={f.label} className="p-3 bg-gray-50 rounded-lg">
                    <div className="text-2xl mb-1">{f.icon}</div>
                    <div className="text-xs text-gray-600 font-medium">{f.label}</div>
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
              <h2 className="text-xl font-bold text-gray-900 mb-1">Connect Infrastructure</h2>
              <p className="text-sm text-gray-500 mb-6">
                NetPulse uses Docker Compose to manage these services. They should all be running
                if you started the platform with <code className="bg-gray-100 px-1 rounded text-xs">docker compose up -d</code>.
              </p>
              <div className="space-y-3 mb-8">
                {[
                  { name: 'NATS', description: 'Internal message bus', port: '4222' },
                  { name: 'InfluxDB', description: 'Time-series metrics', port: '8086' },
                  { name: 'OpenSearch', description: 'Log storage & search', port: '9200' },
                  { name: 'PostgreSQL', description: 'Relational data store', port: '5432' },
                  { name: 'Valkey', description: 'Cache & task queue', port: '6379' },
                ].map((svc) => (
                  <div
                    key={svc.name}
                    className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg border border-gray-200"
                  >
                    <div className="w-2 h-2 rounded-full bg-yellow-400 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm text-gray-800">{svc.name}</span>
                        <span className="text-xs text-gray-400">:{svc.port}</span>
                      </div>
                      <span className="text-xs text-gray-500">{svc.description}</span>
                    </div>
                    <span className="text-xs text-yellow-600 font-medium">pending</span>
                  </div>
                ))}
              </div>
              <p className="text-xs text-gray-400 mb-6 bg-blue-50 p-3 rounded-lg border border-blue-100">
                <strong>Tip:</strong> Service health will be checked automatically. Status updates
                appear in the dashboard once you complete setup.
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setStep(0)}
                  className="flex-1 py-3 border border-gray-300 text-gray-700 rounded-lg font-medium hover:bg-gray-50 transition-colors"
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
              <h2 className="text-xl font-bold text-gray-900 mb-1">Add Your First Device</h2>
              <p className="text-sm text-gray-500 mb-6">
                Enter a network device to start monitoring. You can add more later from the Devices page.
              </p>
              <div className="space-y-4 mb-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Hostname
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. core-router-01"
                    value={deviceForm.hostname}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, hostname: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    IP Address
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. 192.168.1.1"
                    value={deviceForm.ip}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, ip: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Platform
                  </label>
                  <select
                    value={deviceForm.platform}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, platform: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                  >
                    <option value="">Select platform...</option>
                    {PLATFORMS.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    SNMP Community String
                  </label>
                  <input
                    type="password"
                    placeholder="e.g. public"
                    value={deviceForm.community}
                    onChange={(e) => setDeviceForm((f) => ({ ...f, community: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>
              <div className="text-center mb-6">
                <span className="text-sm text-gray-400">— or —</span>
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
                  className="flex-1 py-3 border border-gray-300 text-gray-700 rounded-lg font-medium hover:bg-gray-50 transition-colors"
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
              <h2 className="text-xl font-bold text-gray-900 mb-1">Set Up Integrations</h2>
              <p className="text-sm text-gray-500 mb-6">
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
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-gray-200 hover:border-gray-300 bg-gray-50',
                    )}
                  >
                    <span className="text-xl">{intg.icon}</span>
                    <span className="font-medium text-sm text-gray-800">{intg.name}</span>
                    <span className="text-xs text-gray-500">{intg.description}</span>
                    {connectedIntegrations.has(intg.id) && (
                      <span className="text-xs text-blue-600 font-medium mt-0.5">Selected</span>
                    )}
                  </button>
                ))}
              </div>
              <p className="text-xs text-gray-400 mb-6">
                API credentials for selected integrations will be configured in Settings after setup.
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setStep(2)}
                  className="flex-1 py-3 border border-gray-300 text-gray-700 rounded-lg font-medium hover:bg-gray-50 transition-colors"
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
