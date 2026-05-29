import { useState } from 'react'
import Modal from '../../components/Modal'
import NetBoxImportModal from '../../components/NetBoxImportModal'
import { SectionHeader } from '../Settings'

// Integration catalog. Connection state isn't persisted to a backend yet — the
// integrations service (ingest-api-poller / alert channels) is a later phase —
// so status here is local and illustrative of the intended UX.

type Status = 'connected' | 'not_configured'

interface Integration {
  id: string
  name: string
  description: string
  icon: string
  /** Example summary shown when connected. */
  summary?: string
  /** Fields shown in the setup modal. */
  fields: { key: string; label: string; secret?: boolean; placeholder?: string }[]
}

interface Category {
  title: string
  description: string
  items: Integration[]
}

const CATEGORIES: Category[] = [
  {
    title: 'Cloud Platforms',
    description: 'Poll cloud-managed networking platforms via their APIs.',
    items: [
      { id: 'meraki', name: 'Cisco Meraki', description: 'Cloud-managed networking', icon: '☁️', summary: '3 orgs synced', fields: [{ key: 'api_key', label: 'API Key', secret: true }, { key: 'org', label: 'Organization ID(s)' }] },
      { id: 'mist', name: 'Juniper Mist', description: 'AI-driven Wi-Fi & WAN', icon: '🤖', summary: '1 org synced', fields: [{ key: 'token', label: 'API Token', secret: true }, { key: 'org', label: 'Org ID' }] },
      { id: 'unifi', name: 'Ubiquiti UniFi', description: 'Self-hosted UniFi Network', icon: '📶', fields: [{ key: 'host', label: 'Controller URL', placeholder: 'https://unifi.local' }, { key: 'user', label: 'Username' }, { key: 'pass', label: 'Password', secret: true }] },
      { id: 'cradlepoint', name: 'Cradlepoint', description: 'NetCloud cellular routers', icon: '📡', fields: [{ key: 'cp_api_id', label: 'CP-API-ID', secret: true }, { key: 'cp_api_key', label: 'CP-API-KEY', secret: true }] },
      { id: 'netbox', name: 'NetBox', description: 'Import sites & devices from NetBox (v3/v4)', icon: '🗄', fields: [] },
    ],
  },
  {
    title: 'Alert Channels',
    description: 'Where NetPulse delivers alert notifications.',
    items: [
      { id: 'slack', name: 'Slack', description: 'Post alerts to Slack channels', icon: '💬', summary: '#netops', fields: [{ key: 'webhook', label: 'Incoming Webhook URL', secret: true }] },
      { id: 'teams', name: 'Microsoft Teams', description: 'Send alerts to Teams channels', icon: '👥', fields: [{ key: 'webhook', label: 'Incoming Webhook URL', secret: true }] },
      { id: 'pagerduty', name: 'PagerDuty', description: 'Create incidents in PagerDuty', icon: '🚨', fields: [{ key: 'routing_key', label: 'Integration/Routing Key', secret: true }] },
      { id: 'email', name: 'Email / SMTP', description: 'Send alert emails via SMTP', icon: '✉️', fields: [{ key: 'host', label: 'SMTP Host' }, { key: 'port', label: 'Port', placeholder: '587' }, { key: 'user', label: 'Username' }, { key: 'pass', label: 'Password', secret: true }] },
      { id: 'webhook', name: 'Generic Webhook', description: 'POST alerts to any HTTP endpoint', icon: '🔗', fields: [{ key: 'url', label: 'Endpoint URL' }, { key: 'secret', label: 'Signing Secret (optional)', secret: true }] },
    ],
  },
  {
    title: 'ChatOps',
    description: 'Conversational queries against NetPulse data.',
    items: [
      { id: 'slack_bot', name: 'Slack Bot', description: 'Query devices & alerts from Slack', icon: '🤖', fields: [{ key: 'bot_token', label: 'Bot Token', secret: true }, { key: 'signing_secret', label: 'Signing Secret', secret: true }] },
      { id: 'teams_bot', name: 'Teams Bot', description: 'Query from Microsoft Teams', icon: '🤖', fields: [{ key: 'app_id', label: 'App ID' }, { key: 'app_password', label: 'App Password', secret: true }] },
      { id: 'discord', name: 'Discord', description: 'Query from a Discord server', icon: '🎮', fields: [{ key: 'bot_token', label: 'Bot Token', secret: true }] },
    ],
  },
]

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

export default function Integrations() {
  // id → summary string when connected (local-only for now).
  const [connected, setConnected] = useState<Record<string, string>>({})
  const [setup, setSetup] = useState<Integration | null>(null)
  const [netboxOpen, setNetboxOpen] = useState(false)

  const statusOf = (id: string): Status => (id in connected ? 'connected' : 'not_configured')

  return (
    <div>
      <SectionHeader
        title="Integrations"
        description="Connect cloud platforms, alert channels and ChatOps bots."
      />

      <div className="space-y-8">
        {CATEGORIES.map((cat) => (
          <section key={cat.title}>
            <div className="mb-3">
              <h3 className="text-sm font-semibold text-gray-800">{cat.title}</h3>
              <p className="text-xs text-gray-500">{cat.description}</p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {cat.items.map((it) => {
                const status = statusOf(it.id)
                return (
                  <div key={it.id} className="bg-white border border-gray-200 rounded-lg p-4 flex flex-col gap-3">
                    <div className="flex items-start gap-3">
                      <span className="text-2xl leading-none" aria-hidden>{it.icon}</span>
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-gray-900">{it.name}</p>
                        <p className="text-xs text-gray-500">{it.description}</p>
                      </div>
                      <span className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full ${
                        status === 'connected' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                      }`}>
                        {status === 'connected' ? 'Connected' : 'Not configured'}
                      </span>
                    </div>
                    <div className="flex items-center justify-between mt-auto">
                      <span className="text-xs text-gray-500 truncate">
                        {status === 'connected' ? connected[it.id] : ''}
                      </span>
                      <button
                        onClick={() => (it.id === 'netbox' ? setNetboxOpen(true) : setSetup(it))}
                        className="px-3 py-1.5 text-xs border border-gray-300 rounded-lg hover:bg-gray-50"
                      >
                        {it.id === 'netbox' ? 'Import' : status === 'connected' ? 'Configure' : 'Connect'}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        ))}
      </div>

      {setup && (
        <SetupModal
          integration={setup}
          connected={setup.id in connected}
          onClose={() => setSetup(null)}
          onConnect={() => {
            setConnected((c) => ({ ...c, [setup.id]: setup.summary ?? 'Configured' }))
            setSetup(null)
          }}
          onDisconnect={() => {
            setConnected((c) => { const n = { ...c }; delete n[setup.id]; return n })
            setSetup(null)
          }}
        />
      )}

      {netboxOpen && <NetBoxImportModal onClose={() => setNetboxOpen(false)} />}
    </div>
  )
}

function SetupModal({ integration, connected, onClose, onConnect, onDisconnect }: {
  integration: Integration
  connected: boolean
  onClose: () => void
  onConnect: () => void
  onDisconnect: () => void
}) {
  return (
    <Modal
      title={`${integration.icon} ${integration.name}`}
      onClose={onClose}
      footer={
        <>
          {connected ? (
            <button onClick={onDisconnect} className="flex-1 py-2.5 border border-red-200 text-red-600 rounded-lg text-sm font-medium hover:bg-red-50">Disconnect</button>
          ) : (
            <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Cancel</button>
          )}
          <button onClick={onConnect} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
            {connected ? 'Save' : 'Test & Connect'}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-gray-500">{integration.description}</p>
        {integration.fields.map((f) => (
          <div key={f.key}>
            <label className="block text-sm font-medium text-gray-700 mb-1">{f.label}</label>
            <input type={f.secret ? 'password' : 'text'} placeholder={f.placeholder} className={inputCls} autoComplete="off" />
          </div>
        ))}
        <p className="text-xs text-gray-400">🔒 Credentials are stored securely in OpenBao.</p>
      </div>
    </Modal>
  )
}
