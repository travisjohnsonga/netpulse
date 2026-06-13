import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import NetBoxImportModal from '../../components/NetBoxImportModal'
import EmailSettingsModal from '../../components/EmailSettingsModal'
import UnifiSettingsModal from '../../components/UnifiSettingsModal'
import MistSettingsModal from '../../components/MistSettingsModal'
import {
  fetchMist, fetchUnifiCloud, fetchUnifiControllers, fetchNetboxImports, fetchEmailSettings,
  type MistIntegration, type UnifiCloudAccount, type UnifiController,
  type NetBoxImportRecord, type EmailSettings,
} from '../../api/client'
import { SectionHeader } from '../Settings'

// ── Real per-integration status (fetched from each integration's API) ─────────
type CardStatus = 'connected' | 'configured' | 'error' | 'not_configured'
interface IntegStatus { status: CardStatus; summary: string; lastSync?: string | null }

const BADGE: Record<CardStatus, { label: string; icon: string; cls: string }> = {
  connected: { label: 'Connected', icon: '✅', cls: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
  configured: { label: 'Configured', icon: '◐', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  error: { label: 'Error', icon: '⚠️', cls: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  not_configured: { label: 'Not configured', icon: '○', cls: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400' },
}

function relTime(iso?: string | null): string {
  if (!iso) return ''
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}
const hostOf = (url: string): string => url.replace(/^https?:\/\//, '').replace(/\/$/, '')
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

function mistStatus(m: MistIntegration | null): IntegStatus {
  if (!m || !(m.org_id && m.api_host)) return { status: 'not_configured', summary: 'Not configured' }
  if (m.last_error) return { status: 'error', summary: `❌ ${m.last_error}`, lastSync: m.last_sync }
  if (m.last_sync) return { status: 'connected', summary: `${m.org_name || m.org_id} · ${plural(m.device_count, 'device')}`, lastSync: m.last_sync }
  return { status: 'configured', summary: `${m.org_name || m.org_id} · not yet synced` }
}

function unifiStatus(cloud: UnifiCloudAccount | null, controllers: UnifiController[]): IntegStatus {
  const count = controllers.length
  const hostCount = cloud?.host_count ?? 0
  if (count > 0) {
    const lastSync = cloud?.last_sync ?? controllers.find((c) => c.last_sync)?.last_sync ?? null
    return { status: 'connected', summary: `${plural(count, 'controller')}${hostCount ? ` · ${plural(hostCount, 'cloud host')}` : ''}`, lastSync }
  }
  if (cloud?.api_key_set || hostCount > 0) {
    if (cloud?.last_error) return { status: 'error', summary: `❌ ${cloud.last_error}`, lastSync: cloud?.last_sync }
    return { status: 'configured', summary: hostCount ? `${plural(hostCount, 'cloud host')} discovered` : 'Cloud API key set', lastSync: cloud?.last_sync }
  }
  return { status: 'not_configured', summary: 'Not configured' }
}

function netboxStatus(rows: NetBoxImportRecord[]): IntegStatus {
  if (!rows.length) return { status: 'not_configured', summary: 'No imports yet' }
  const latest = rows[0] // viewset orders by -created_at
  const host = hostOf(latest.netbox_url)
  const when = latest.finished_at || latest.created_at
  if (latest.status === 'failed') return { status: 'error', summary: `❌ ${host} — import failed`, lastSync: when }
  if (latest.status === 'completed') return { status: 'connected', summary: `${host} · ${plural(latest.devices_imported, 'device')} imported`, lastSync: when }
  return { status: 'configured', summary: `${host} · ${latest.status}`, lastSync: when }
}

function emailStatus(e: EmailSettings): IntegStatus {
  if (!e.host) return { status: 'not_configured', summary: 'Not configured' }
  const who = e.provider && e.provider !== 'custom' ? e.provider : e.host
  if (e.enabled) return { status: 'connected', summary: `${who}${e.from_email ? ` · ${e.from_email}` : ''}` }
  return { status: 'configured', summary: `${e.host} · disabled` }
}

// Integration catalog. Connection state isn't persisted to a backend yet — the
// integrations service (ingest-api-poller / alert channels) is a later phase —
// so status here is local and illustrative of the intended UX.

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
      { id: 'mist', name: 'Juniper Mist', description: 'AI-driven Wi-Fi & WAN', icon: '🤖', fields: [] },
      { id: 'unifi', name: 'Ubiquiti UniFi', description: 'Self-hosted UniFi Network', icon: '📶', fields: [{ key: 'host', label: 'Controller URL', placeholder: 'https://unifi.local' }, { key: 'user', label: 'Username' }, { key: 'pass', label: 'Password', secret: true }] },
      { id: 'cradlepoint', name: 'Cradlepoint', description: 'NetCloud cellular routers', icon: '📡', fields: [{ key: 'cp_api_id', label: 'CP-API-ID', secret: true }, { key: 'cp_api_key', label: 'CP-API-KEY', secret: true }] },
      { id: 'netbox', name: 'NetBox', description: 'Import sites & devices from NetBox (v3/v4)', icon: '🗄', fields: [] },
    ],
  },
  {
    title: 'Alert Channels',
    description: 'Where spane delivers alert notifications.',
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
    description: 'Conversational queries against spane data.',
    items: [
      { id: 'slack_bot', name: 'Slack Bot', description: 'Query devices & alerts from Slack', icon: '🤖', fields: [{ key: 'bot_token', label: 'Bot Token', secret: true }, { key: 'signing_secret', label: 'Signing Secret', secret: true }] },
      { id: 'teams_bot', name: 'Teams Bot', description: 'Query from Microsoft Teams', icon: '🤖', fields: [{ key: 'app_id', label: 'App ID' }, { key: 'app_password', label: 'App Password', secret: true }] },
      { id: 'discord', name: 'Discord', description: 'Query from a Discord server', icon: '🎮', fields: [{ key: 'bot_token', label: 'Bot Token', secret: true }] },
    ],
  },
]

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

export default function Integrations() {
  // id → summary string when connected (local-only for now).
  const [connected, setConnected] = useState<Record<string, string>>({})
  const [setup, setSetup] = useState<Integration | null>(null)
  const [netboxOpen, setNetboxOpen] = useState(false)
  const [emailOpen, setEmailOpen] = useState(false)
  const [unifiOpen, setUnifiOpen] = useState(false)
  const [mistOpen, setMistOpen] = useState(false)
  // Real backend status per integration (unifi, mist, netbox, email). Other
  // cards (alert channels / ChatOps) still use the local placeholder state.
  const [statuses, setStatuses] = useState<Record<string, IntegStatus>>({})
  const setStatus = (id: string, s: IntegStatus) => setStatuses((prev) => ({ ...prev, [id]: s }))

  const loadAll = () => {
    fetchMist().then((m) => setStatus('mist', mistStatus(m))).catch(() => {})
    Promise.all([fetchUnifiCloud().catch(() => null), fetchUnifiControllers().catch(() => [])])
      .then(([cloud, controllers]) => setStatus('unifi', unifiStatus(cloud, controllers))).catch(() => {})
    fetchNetboxImports().then((rows) => setStatus('netbox', netboxStatus(rows))).catch(() => {})
    fetchEmailSettings().then((e) => setStatus('email', emailStatus(e))).catch(() => {})
  }
  useEffect(() => { loadAll() }, [])

  // Per-card status + summary. unifi/mist/netbox/email reflect real backend
  // state; the rest use the local placeholder state until their backends land.
  const cardState = (id: string): IntegStatus => {
    if (statuses[id]) return statuses[id]
    const connectedNow = id in connected
    return { status: connectedNow ? 'connected' : 'not_configured', summary: connectedNow ? connected[id] : 'Not configured' }
  }

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
              <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{cat.title}</h3>
              <p className="text-xs text-gray-500 dark:text-gray-400">{cat.description}</p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {cat.items.map((it) => {
                const { status, summary, lastSync } = cardState(it.id)
                const badge = BADGE[status]
                const showSync = lastSync && (status === 'connected' || status === 'error')
                return (
                  <div key={it.id} className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 flex flex-col gap-3">
                    <div className="flex items-start gap-3">
                      <span className="text-2xl leading-none" aria-hidden>{it.icon}</span>
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-gray-900 dark:text-gray-100">{it.name}</p>
                        <p className="text-xs text-gray-500 dark:text-gray-400">{it.description}</p>
                      </div>
                      <span className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full ${badge.cls}`} title={badge.label}>
                        <span aria-hidden>{badge.icon}</span> {badge.label}
                      </span>
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs text-gray-600 dark:text-gray-300 truncate" title={summary}>{summary}</p>
                      {showSync && <p className="text-[11px] text-gray-400 dark:text-gray-500">Last sync: {relTime(lastSync)}</p>}
                    </div>
                    <div className="flex items-center justify-end mt-auto">
                      <button
                        onClick={() => (it.id === 'netbox' ? setNetboxOpen(true) : it.id === 'email' ? setEmailOpen(true) : it.id === 'unifi' ? setUnifiOpen(true) : it.id === 'mist' ? setMistOpen(true) : setSetup(it))}
                        className="px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300"
                      >
                        {it.id === 'netbox' ? 'Import' : it.id === 'email' || it.id === 'unifi' || it.id === 'mist' ? 'Configure' : status === 'connected' ? 'Configure' : 'Connect'}
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

      {netboxOpen && <NetBoxImportModal onClose={() => { setNetboxOpen(false); loadAll() }} />}
      {emailOpen && <EmailSettingsModal onClose={() => { setEmailOpen(false); loadAll() }} />}
      {unifiOpen && <UnifiSettingsModal onClose={() => { setUnifiOpen(false); loadAll() }} />}
      {mistOpen && <MistSettingsModal onClose={() => { setMistOpen(false); loadAll() }} />}
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
            <button onClick={onDisconnect} className="flex-1 py-2.5 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-lg text-sm font-medium hover:bg-red-50 dark:hover:bg-red-900/30">Disconnect</button>
          ) : (
            <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          )}
          <button onClick={onConnect} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
            {connected ? 'Save' : 'Test & Connect'}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-gray-500 dark:text-gray-400">{integration.description}</p>
        {integration.fields.map((f) => (
          <div key={f.key}>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{f.label}</label>
            <input type={f.secret ? 'password' : 'text'} placeholder={f.placeholder} className={inputCls} autoComplete="off" />
          </div>
        ))}
        <p className="text-xs text-gray-400 dark:text-gray-500">🔒 Credentials are stored securely in OpenBao.</p>
      </div>
    </Modal>
  )
}
