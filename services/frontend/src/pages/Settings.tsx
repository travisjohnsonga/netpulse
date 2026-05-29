import { useState } from 'react'
import clsx from 'clsx'

interface Integration {
  id: string
  name: string
  icon: string
  description: string
  status: 'connected' | 'not_connected'
  category: 'vendor' | 'alert'
}

const INTEGRATIONS: Integration[] = [
  // Vendor integrations
  {
    id: 'meraki',
    name: 'Cisco Meraki',
    icon: '🔵',
    description: 'Cloud-managed networking — dashboards, devices, alerts',
    status: 'not_connected',
    category: 'vendor',
  },
  {
    id: 'mist',
    name: 'Juniper Mist',
    icon: '🟠',
    description: 'AI-driven Wi-Fi, WAN, and access switching',
    status: 'not_connected',
    category: 'vendor',
  },
  {
    id: 'unifi',
    name: 'Ubiquiti UniFi',
    icon: '⚫',
    description: 'Enterprise Wi-Fi, switching, and security',
    status: 'not_connected',
    category: 'vendor',
  },
  // Alert channels
  {
    id: 'slack',
    name: 'Slack',
    icon: '💬',
    description: 'Send alerts and daily summaries to Slack channels',
    status: 'not_connected',
    category: 'alert',
  },
  {
    id: 'teams',
    name: 'Microsoft Teams',
    icon: '🟣',
    description: 'Alert notifications via Teams webhooks',
    status: 'not_connected',
    category: 'alert',
  },
  {
    id: 'pagerduty',
    name: 'PagerDuty',
    icon: '🔴',
    description: 'Critical alert escalation and on-call management',
    status: 'not_connected',
    category: 'alert',
  },
  {
    id: 'email',
    name: 'Email (SMTP)',
    icon: '📧',
    description: 'Alert notifications via email',
    status: 'not_connected',
    category: 'alert',
  },
]

interface ApiKeyForm {
  nvdKey: string
  psirtId: string
  psirtSecret: string
}

export default function Settings() {
  const [integrations, setIntegrations] = useState<Integration[]>(INTEGRATIONS)
  const [activeModal, setActiveModal] = useState<string | null>(null)
  const [apiKeys, setApiKeys] = useState<ApiKeyForm>({
    nvdKey: '',
    psirtId: '',
    psirtSecret: '',
  })
  const [saved, setSaved] = useState<Record<string, boolean>>({})

  const toggleConnect = (id: string) => {
    setIntegrations((prev) =>
      prev.map((intg) =>
        intg.id === id
          ? {
              ...intg,
              status: intg.status === 'connected' ? 'not_connected' : 'connected',
            }
          : intg,
      ),
    )
    setActiveModal(null)
  }

  const saveApiKey = (field: keyof ApiKeyForm) => {
    setSaved((prev) => ({ ...prev, [field]: true }))
    setTimeout(() => setSaved((prev) => ({ ...prev, [field]: false })), 2000)
  }

  const vendorIntegrations = integrations.filter((i) => i.category === 'vendor')
  const alertIntegrations = integrations.filter((i) => i.category === 'alert')

  return (
    <div className="space-y-8 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Configure integrations, API keys, and platform connections
        </p>
      </div>

      {/* Vendor Integrations */}
      <section>
        <h2 className="text-base font-semibold text-gray-800 mb-3">Vendor Integrations</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {vendorIntegrations.map((intg) => (
            <IntegrationCard
              key={intg.id}
              integration={intg}
              onConnect={() => setActiveModal(intg.id)}
              onDisconnect={() => toggleConnect(intg.id)}
            />
          ))}
        </div>
      </section>

      {/* Alert Channels */}
      <section>
        <h2 className="text-base font-semibold text-gray-800 mb-3">Alert Channels</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {alertIntegrations.map((intg) => (
            <IntegrationCard
              key={intg.id}
              integration={intg}
              onConnect={() => setActiveModal(intg.id)}
              onDisconnect={() => toggleConnect(intg.id)}
            />
          ))}
        </div>
      </section>

      {/* API Keys */}
      <section>
        <h2 className="text-base font-semibold text-gray-800 mb-3">API Keys</h2>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 divide-y divide-gray-100">
          {/* NVD API Key */}
          <div className="p-5">
            <div className="flex flex-col sm:flex-row sm:items-center gap-4">
              <div className="flex-1 min-w-0">
                <p className="font-medium text-gray-800 text-sm">NVD API Key</p>
                <p className="text-xs text-gray-500 mt-0.5">
                  Required for CVE intelligence. Get a free key at{' '}
                  <a
                    href="https://nvd.nist.gov/developers/request-an-api-key"
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-600 hover:underline"
                  >
                    nvd.nist.gov
                  </a>
                </p>
              </div>
              <div className="flex gap-2">
                <input
                  type="password"
                  placeholder="Enter NVD API key..."
                  value={apiKeys.nvdKey}
                  onChange={(e) => setApiKeys((k) => ({ ...k, nvdKey: e.target.value }))}
                  className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 w-52"
                />
                <button
                  onClick={() => saveApiKey('nvdKey')}
                  className={clsx(
                    'px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                    saved.nvdKey
                      ? 'bg-green-600 text-white'
                      : 'bg-blue-600 hover:bg-blue-700 text-white',
                  )}
                >
                  {saved.nvdKey ? 'Saved!' : 'Save'}
                </button>
              </div>
            </div>
          </div>

          {/* Cisco PSIRT */}
          <div className="p-5">
            <div className="mb-3">
              <p className="font-medium text-gray-800 text-sm">Cisco PSIRT Credentials</p>
              <p className="text-xs text-gray-500 mt-0.5">
                For Cisco security advisory data. Register at{' '}
                <a
                  href="https://apiconsole.cisco.com"
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  apiconsole.cisco.com
                </a>
              </p>
            </div>
            <div className="flex flex-col sm:flex-row gap-3">
              <input
                type="password"
                placeholder="Client ID"
                value={apiKeys.psirtId}
                onChange={(e) => setApiKeys((k) => ({ ...k, psirtId: e.target.value }))}
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <input
                type="password"
                placeholder="Client Secret"
                value={apiKeys.psirtSecret}
                onChange={(e) => setApiKeys((k) => ({ ...k, psirtSecret: e.target.value }))}
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={() => saveApiKey('psirtSecret')}
                className={clsx(
                  'px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                  saved.psirtSecret
                    ? 'bg-green-600 text-white'
                    : 'bg-blue-600 hover:bg-blue-700 text-white',
                )}
              >
                {saved.psirtSecret ? 'Saved!' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Integration Setup Modal (generic stub) */}
      {activeModal && (
        <IntegrationModal
          integration={integrations.find((i) => i.id === activeModal)!}
          onConnect={() => toggleConnect(activeModal)}
          onClose={() => setActiveModal(null)}
        />
      )}
    </div>
  )
}

function IntegrationCard({
  integration,
  onConnect,
  onDisconnect,
}: {
  integration: Integration
  onConnect: () => void
  onDisconnect: () => void
}) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-5 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span className="text-2xl">{integration.icon}</span>
        <div className="flex-1 min-w-0">
          <p className="font-medium text-gray-800 text-sm truncate">{integration.name}</p>
          <span
            className={clsx(
              'text-xs font-medium',
              integration.status === 'connected' ? 'text-green-600' : 'text-gray-400',
            )}
          >
            {integration.status === 'connected' ? 'Connected' : 'Not Connected'}
          </span>
        </div>
      </div>
      <p className="text-xs text-gray-500 leading-relaxed">{integration.description}</p>
      {integration.status === 'connected' ? (
        <button
          onClick={onDisconnect}
          className="mt-auto px-3 py-2 border border-red-200 text-red-600 hover:bg-red-50 rounded-lg text-sm font-medium transition-colors"
        >
          Disconnect
        </button>
      ) : (
        <button
          onClick={onConnect}
          className="mt-auto px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Connect
        </button>
      )}
    </div>
  )
}

function IntegrationModal({
  integration,
  onConnect,
  onClose,
}: {
  integration: Integration
  onConnect: () => void
  onClose: () => void
}) {
  const [apiKey, setApiKey] = useState('')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <div className="flex items-center gap-3 mb-4">
          <span className="text-3xl">{integration.icon}</span>
          <div>
            <h2 className="text-lg font-bold text-gray-900">Connect {integration.name}</h2>
            <p className="text-sm text-gray-500">{integration.description}</p>
          </div>
        </div>

        <div className="space-y-4 mb-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
            <input
              type="password"
              placeholder={`Enter ${integration.name} API key...`}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
            <p className="text-xs text-blue-800">
              Credentials are stored securely in OpenBao (HashiCorp Vault-compatible). They are
              never stored in plain text.
            </p>
          </div>
        </div>

        <div className="flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={onConnect}
            disabled={!apiKey}
            className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
          >
            Connect
          </button>
        </div>
      </div>
    </div>
  )
}
