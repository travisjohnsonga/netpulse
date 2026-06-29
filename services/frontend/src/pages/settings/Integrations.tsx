import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import NetBoxImportModal from '../../components/NetBoxImportModal'
import EmailSettingsModal from '../../components/EmailSettingsModal'
import UnifiSettingsModal from '../../components/UnifiSettingsModal'
import MistSettingsModal from '../../components/MistSettingsModal'
import {
  fetchMist, fetchUnifiCloud, fetchUnifiControllers, fetchNetboxImports, fetchEmailSettings,
  fetchAlertChannels, createAlertChannel, updateAlertChannel, deleteAlertChannel, testAlertChannel,
  type MistIntegration, type UnifiCloudAccount, type UnifiController,
  type NetBoxImportRecord, type EmailSettings, type AlertChannel,
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

// ── Alert-channel integrations (slack/teams/pagerduty/webhook) ─────────────────
// These map onto the real AlertChannel API (validate → save → secret-to-OpenBao).
// Each defines its AlertChannel.channel_type, the form field carrying its secret,
// and how to build the channel config from the modal's field values.
interface AlertChannelDef {
  type: AlertChannel['channel_type']
  secretField: string
  config: (v: Record<string, string>) => Record<string, unknown>
  summary: string
}
const ALERT_CHANNELS: Record<string, AlertChannelDef> = {
  slack: { type: 'slack', secretField: 'webhook', summary: 'Incoming webhook configured',
    config: (v) => ({ webhook_url: v.webhook }) },
  teams: { type: 'teams', secretField: 'webhook', summary: 'Incoming webhook configured',
    config: (v) => ({ webhook_url: v.webhook, card_format: 'adaptive' }) },
  pagerduty: { type: 'pagerduty', secretField: 'routing_key', summary: 'Routing key configured',
    config: (v) => ({ routing_key: v.routing_key }) },
  webhook: { type: 'webhook', secretField: 'url', summary: 'Endpoint configured',
    config: (v) => ({ url: v.url, ...(v.secret ? { token: v.secret } : {}) }) },
}

function channelStatus(def: AlertChannelDef, ch?: AlertChannel): IntegStatus {
  if (!ch) return { status: 'not_configured', summary: 'Not configured' }
  return { status: ch.is_active ? 'connected' : 'configured', summary: def.summary }
}

function errMsg(e: unknown): string {
  const data = (e as { response?: { data?: unknown } }).response?.data
  if (data && typeof data === 'object') {
    const vals = Object.values(data as Record<string, unknown>).flat()
    if (vals.length) return String(vals[0])
  }
  return 'Request failed'
}

// Integration catalog. Cloud/email/alert-channel cards now reflect real backend
// state; the remaining placeholder cards (meraki, cradlepoint, ChatOps bots) use
// local state until their backends land.

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
  // id → summary string when connected (local-only; placeholder cards).
  const [connected, setConnected] = useState<Record<string, string>>({})
  const [setup, setSetup] = useState<Integration | null>(null)
  const [netboxOpen, setNetboxOpen] = useState(false)
  const [emailOpen, setEmailOpen] = useState(false)
  const [unifiOpen, setUnifiOpen] = useState(false)
  const [mistOpen, setMistOpen] = useState(false)
  // Real backend status per integration (unifi, mist, netbox, email, alert channels).
  const [statuses, setStatuses] = useState<Record<string, IntegStatus>>({})
  // Existing AlertChannel per alert-channel integration id (for update/test/disconnect).
  const [channels, setChannels] = useState<Record<string, AlertChannel>>({})
  const setStatus = (id: string, s: IntegStatus) => setStatuses((prev) => ({ ...prev, [id]: s }))

  const loadAll = () => {
    fetchMist().then((m) => setStatus('mist', mistStatus(m))).catch(() => {})
    Promise.all([fetchUnifiCloud().catch(() => null), fetchUnifiControllers().catch(() => [])])
      .then(([cloud, controllers]) => setStatus('unifi', unifiStatus(cloud, controllers))).catch(() => {})
    fetchNetboxImports().then((rows) => setStatus('netbox', netboxStatus(rows))).catch(() => {})
    fetchEmailSettings().then((e) => setStatus('email', emailStatus(e))).catch(() => {})
    fetchAlertChannels().then((chs) => {
      // First active (else first) channel per type backs each alert-channel card.
      const byType: Record<string, AlertChannel> = {}
      for (const ch of chs) {
        if (!byType[ch.channel_type] || (ch.is_active && !byType[ch.channel_type].is_active)) byType[ch.channel_type] = ch
      }
      const nextChannels: Record<string, AlertChannel> = {}
      setStatuses((prev) => {
        const s = { ...prev }
        for (const [id, def] of Object.entries(ALERT_CHANNELS)) {
          const ch = byType[def.type]
          if (ch) nextChannels[id] = ch
          s[id] = channelStatus(def, ch)
        }
        return s
      })
      setChannels(nextChannels)
    }).catch(() => {})
  }
  useEffect(() => { loadAll() }, [])

  // Per-card status. Backend-backed cards use `statuses`; placeholder cards fall
  // back to local `connected` state.
  const cardState = (id: string): IntegStatus => {
    if (statuses[id]) return statuses[id]
    const connectedNow = id in connected
    return { status: connectedNow ? 'connected' : 'not_configured', summary: connectedNow ? connected[id] : 'Not configured' }
  }

  // Create/update the AlertChannel + send a live test through it. Returns a
  // result the modal renders inline (this is the real "Test & Connect").
  const submitAlertChannel = async (integration: Integration, values: Record<string, string>):
    Promise<{ ok: boolean; message: string }> => {
    const def = ALERT_CHANNELS[integration.id]
    const existing = channels[integration.id]
    const secret = (values[def.secretField] || '').trim()
    try {
      let ch: AlertChannel
      if (!existing) {
        if (!secret) return { ok: false, message: `Please enter the ${integration.fields[0]?.label || 'required field'}.` }
        ch = await createAlertChannel({ name: integration.name, channel_type: def.type, is_active: true, config: def.config(values) })
      } else {
        // Update: only overwrite the secret/config when a new value was entered;
        // otherwise PATCH just keeps it active (config — incl. the OpenBao secret — untouched).
        const payload: Partial<AlertChannel> = { is_active: true }
        if (secret) payload.config = def.config(values)
        ch = await updateAlertChannel(existing.id, payload)
      }
      const test = await testAlertChannel(ch.id)
      loadAll()
      return test.ok
        ? { ok: true, message: `Connected — test ${def.type === 'pagerduty' ? 'event enqueued' : 'notification sent'}. ${test.detail}` }
        : { ok: false, message: `Saved, but the test send failed: ${test.detail}` }
    } catch (e) {
      return { ok: false, message: errMsg(e) }
    }
  }

  const disconnectAlertChannel = async (integration: Integration): Promise<void> => {
    const existing = channels[integration.id]
    if (existing) { try { await deleteAlertChannel(existing.id) } catch { /* ignore */ } }
    loadAll()
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
          isAlertChannel={setup.id in ALERT_CHANNELS}
          hasChannel={Boolean(channels[setup.id])}
          onClose={() => setSetup(null)}
          onSubmit={async (values) => {
            if (setup.id in ALERT_CHANNELS) return submitAlertChannel(setup, values)
            // Placeholder integrations (no backend yet): keep local connected state.
            setConnected((c) => ({ ...c, [setup.id]: setup.summary ?? 'Configured' }))
            return { ok: true, message: 'Saved.' }
          }}
          onDisconnect={async () => {
            if (setup.id in ALERT_CHANNELS) await disconnectAlertChannel(setup)
            else setConnected((c) => { const n = { ...c }; delete n[setup.id]; return n })
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

function SetupModal({ integration, isAlertChannel, hasChannel, onClose, onSubmit, onDisconnect }: {
  integration: Integration
  isAlertChannel: boolean
  hasChannel: boolean
  onClose: () => void
  onSubmit: (values: Record<string, string>) => Promise<{ ok: boolean; message: string }>
  onDisconnect: () => void
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)
  const connected = hasChannel

  const submit = async () => {
    setBusy(true)
    setResult(null)
    const r = await onSubmit(values)
    setResult(r)
    setBusy(false)
  }

  return (
    <Modal
      title={`${integration.icon} ${integration.name}`}
      onClose={onClose}
      footer={
        <>
          {connected ? (
            <button disabled={busy} onClick={onDisconnect} className="flex-1 py-2.5 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-lg text-sm font-medium hover:bg-red-50 dark:hover:bg-red-900/30 disabled:opacity-50">Disconnect</button>
          ) : (
            <button disabled={busy} onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50">Cancel</button>
          )}
          <button disabled={busy} onClick={submit} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50">
            {busy ? 'Working…' : connected ? 'Save & Test' : 'Test & Connect'}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-gray-500 dark:text-gray-400">{integration.description}</p>
        {integration.fields.map((f) => (
          <div key={f.key}>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{f.label}</label>
            <input
              type={f.secret ? 'password' : 'text'}
              placeholder={connected && f.secret ? '•••••••• stored in OpenBao (leave blank to keep)' : f.placeholder}
              value={values[f.key] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              className={inputCls}
              autoComplete="off"
            />
          </div>
        ))}
        {result && (
          <p className={`text-xs rounded-md px-3 py-2 ${result.ok
            ? 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400'
            : 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
            {result.ok ? '✅ ' : '⚠️ '}{result.message}
          </p>
        )}
        <p className="text-xs text-gray-400 dark:text-gray-500">🔒 Credentials are stored securely in OpenBao.</p>
        {isAlertChannel && (
          <p className="text-[11px] text-gray-400 dark:text-gray-500">“Test &amp; Connect” saves the channel and sends a live test notification through it.</p>
        )}
      </div>
    </Modal>
  )
}
