import { useState } from 'react'

interface IntegrationCard {
  id: string
  name: string
  description: string
  status: 'connected' | 'not_connected'
}

const VENDOR_INTEGRATIONS: IntegrationCard[] = [
  { id: 'meraki', name: 'Cisco Meraki',   description: 'Cloud-managed networking',   status: 'not_connected' },
  { id: 'mist',   name: 'Juniper Mist',   description: 'AI-driven Wi-Fi & WAN',      status: 'not_connected' },
  { id: 'unifi',  name: 'Ubiquiti UniFi', description: 'Self-hosted UniFi Network',  status: 'not_connected' },
]

const ALERT_CHANNELS: IntegrationCard[] = [
  { id: 'slack',      name: 'Slack',          description: 'Post alerts to Slack channels', status: 'not_connected' },
  { id: 'teams',      name: 'Microsoft Teams', description: 'Send alerts to Teams channels', status: 'not_connected' },
  { id: 'pagerduty',  name: 'PagerDuty',       description: 'Create incidents in PagerDuty', status: 'not_connected' },
  { id: 'email',      name: 'Email',           description: 'Send alert emails via SMTP',    status: 'not_connected' },
]

function IntegrationCardUI({ card }: { card: IntegrationCard }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 flex items-center justify-between">
      <div>
        <p className="font-medium text-gray-900">{card.name}</p>
        <p className="text-sm text-gray-500">{card.description}</p>
      </div>
      <div className="flex items-center gap-3">
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
          card.status === 'connected'
            ? 'bg-green-100 text-green-700'
            : 'bg-gray-100 text-gray-500'
        }`}>
          {card.status === 'connected' ? 'Connected' : 'Not Connected'}
        </span>
        <button className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">
          {card.status === 'connected' ? 'Manage' : 'Connect'}
        </button>
      </div>
    </div>
  )
}

function ApiKeyInput({ label, placeholder }: { label: string; placeholder: string }) {
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)

  const handleSave = () => {
    if (value) { setSaved(true); setTimeout(() => setSaved(false), 2000) }
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <div className="flex gap-2">
        <input
          type="password"
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder={placeholder}
          className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={handleSave}
          className={`px-4 py-2 text-sm rounded-lg font-medium transition-colors ${
            saved
              ? 'bg-green-600 text-white'
              : 'bg-blue-600 text-white hover:bg-blue-700'
          }`}
        >
          {saved ? 'Saved!' : 'Save'}
        </button>
      </div>
    </div>
  )
}

export default function Settings() {
  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-gray-900">Settings</h1>

      {/* Vendor Integrations */}
      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Vendor Integrations</h2>
          <p className="text-sm text-gray-500">Connect cloud-managed networking platforms</p>
        </div>
        {VENDOR_INTEGRATIONS.map(c => <IntegrationCardUI key={c.id} card={c} />)}
      </section>

      {/* Alert Channels */}
      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Alert Channels</h2>
          <p className="text-sm text-gray-500">Configure where alerts are sent</p>
        </div>
        {ALERT_CHANNELS.map(c => <IntegrationCardUI key={c.id} card={c} />)}
      </section>

      {/* API Keys */}
      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">API Keys</h2>
          <p className="text-sm text-gray-500">Third-party API credentials for CVE and advisory feeds</p>
        </div>
        <ApiKeyInput label="NVD API Key" placeholder="Enter NVD API key..." />
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          <p className="text-sm font-medium text-gray-700">Cisco PSIRT OAuth</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Client ID</label>
              <input type="password" placeholder="Client ID"
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Client Secret</label>
              <input type="password" placeholder="Client Secret"
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <button className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">Save</button>
        </div>
      </section>
    </div>
  )
}
