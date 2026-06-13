import { useEffect, useState } from 'react'
import Modal from './Modal'
import {
  fetchMist, saveMist, testMist, syncMist, fetchMistSites,
  type MistIntegration, type MistSite, type MistTestResult,
} from '../api/client'
import { parseApiErrors } from '../api/errors'

const input =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

// Mist regional API endpoints — a token is only valid against its own region's
// host (wrong host → 401). Find yours from the portal URL (manage.XX.mist.com).
const MIST_REGIONS: { host: string; label: string }[] = [
  { host: 'api.mist.com', label: 'Global 01 — Americas' },
  { host: 'api.gc1.mist.com', label: 'Global 02 — Americas' },
  { host: 'api.ac2.mist.com', label: 'Global 03 — Americas' },
  { host: 'api.eu.mist.com', label: 'EMEA 01 — Germany' },
  { host: 'api.eu2.mist.com', label: 'EMEA 02 — England' },
  { host: 'api.ap.mist.com', label: 'APAC 01 — Australia' },
]
const KNOWN_HOSTS = MIST_REGIONS.map((r) => r.host)

function relTime(iso: string | null): string {
  if (!iso) return 'Never'
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'Just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export default function MistSettingsModal({ onClose }: { onClose: () => void }) {
  const [acct, setAcct] = useState<MistIntegration | null>(null)
  const [sites, setSites] = useState<MistSite[]>([])
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [test, setTest] = useState<MistTestResult | null>(null)
  // Regional API host; `custom` toggles a free-text entry for an unlisted host.
  const [host, setHost] = useState('api.mist.com')
  const [custom, setCustom] = useState(false)

  const load = () => fetchMist().then((a) => {
    setAcct(a)
    setHost(a.api_host || 'api.mist.com')
    setCustom(!!a.api_host && !KNOWN_HOSTS.includes(a.api_host))
  }).catch((e) => setError(parseApiErrors(e, 'Failed to load Mist settings.')))
  const loadSites = () => fetchMistSites().then(setSites).catch(() => {})
  useEffect(() => { load(); loadSites() }, [])

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 4000) }

  const save = async () => {
    setBusy(true); setError(null); setTest(null)
    try {
      await saveMist({ api_token: token || undefined, api_host: host, enabled: true })
      setToken(''); flash('Mist settings saved.'); load()
    } catch (e) { setError(parseApiErrors(e, 'Failed to save settings.')) }
    finally { setBusy(false) }
  }

  const runTest = async () => {
    setBusy(true); setError(null); setTest(null)
    try {
      // Persist token + region first so the test uses the current values.
      await saveMist({ api_token: token || undefined, api_host: host })
      const r = await testMist(token || undefined, host)
      setToken(''); setTest(r); load()
      if (!r.connected) setError(r.error || 'Connection failed.')
    } catch (e) { setError(parseApiErrors(e, 'Connection failed.')) }
    finally { setBusy(false) }
  }

  const sync = async () => {
    setBusy(true); setError(null); setTest(null)
    try {
      const r = await syncMist()
      flash(`Synced ${r.sites} site(s): ${r.imported} imported, ${r.updated} updated, ${r.skipped} skipped.`)
      load(); loadSites()
    } catch (e) { setError(parseApiErrors(e, 'Sync failed.')) }
    finally { setBusy(false) }
  }

  const connected = !!acct?.api_token_set && !acct?.last_error
  const orgLabel = test?.orgs?.[0]?.name || acct?.org_name

  return (
    <Modal title="🤖 Juniper Mist Wireless" onClose={onClose} size="lg"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Close</button>
          <button onClick={runTest} disabled={busy} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50">{busy ? '…' : 'Test Connection'}</button>
          <button onClick={sync} disabled={busy || !acct?.api_token_set} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? '…' : 'Sync Now'}</button>
        </>
      }>
      <div className="space-y-3">
        {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300 whitespace-pre-line">{error}</div>}
        {msg && <div className="bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-900 rounded-lg px-3 py-2 text-sm text-green-700 dark:text-green-300">{msg}</div>}

        <p className="text-sm text-gray-500 dark:text-gray-400">
          Juniper Mist is a cloud-managed wireless platform. One org API token discovers all your sites and devices.
        </p>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API Endpoint</label>
          <select className={input}
            value={custom ? 'custom' : host}
            onChange={(e) => {
              if (e.target.value === 'custom') { setCustom(true) }
              else { setCustom(false); setHost(e.target.value) }
            }}>
            {MIST_REGIONS.map((r) => (
              <option key={r.host} value={r.host}>{r.host}  ({r.label})</option>
            ))}
            <option value="custom">Custom…  (enter manually)</option>
          </select>
          {custom && (
            <input className={`${input} mt-2`} value={host} onChange={(e) => setHost(e.target.value)}
              placeholder="api.xx.mist.com" autoComplete="off" />
          )}
          <p className="text-xs text-gray-400 mt-1">
            Find your region: log into <span className="font-mono">manage.mist.com</span> and check the URL. If it shows
            <span className="font-mono"> manage.ac2.mist.com</span> use <span className="font-mono">api.ac2.mist.com</span>.
            A token only works against its own region (wrong host → 401).
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API Token</label>
          <input type="password" autoComplete="new-password" value={token} onChange={(e) => setToken(e.target.value)}
            placeholder={acct?.api_token_set ? '•••••••• (leave blank to keep)' : 'Authorization: Token …'}
            className={input} />
          <p className="text-xs text-gray-400 mt-1">
            ℹ️ Generate at <span className="font-mono">manage.mist.com → My Account → API Tokens</span>. 🔒 Stored securely in OpenBao.
          </p>
          <button onClick={save} disabled={busy || (!token && host === (acct?.api_host ?? ''))} className="mt-2 px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 dark:text-gray-200">Save</button>
        </div>

        {/* Status panel */}
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-1 bg-gray-50/50 dark:bg-gray-900/30 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-gray-500 dark:text-gray-400">Status</span>
            <span className={connected ? 'text-green-600 dark:text-green-400 font-medium' : 'text-gray-500 dark:text-gray-400'}>
              {acct?.last_error ? `❌ ${acct.last_error}` : connected ? '✅ Connected' : 'Not configured'}
            </span>
          </div>
          {test?.email && (
            <div className="flex items-center justify-between"><span className="text-gray-500 dark:text-gray-400">Account</span><span className="text-gray-700 dark:text-gray-200">{test.email}</span></div>
          )}
          {orgLabel && (
            <div className="flex items-center justify-between">
              <span className="text-gray-500 dark:text-gray-400">Organization</span>
              <span className="text-gray-700 dark:text-gray-200">{orgLabel}{(acct?.site_count ?? 0) > 0 ? ` · ${acct?.site_count} site(s)` : ''}</span>
            </div>
          )}
          {(acct?.device_count ?? 0) > 0 && (
            <div className="flex items-center justify-between"><span className="text-gray-500 dark:text-gray-400">Devices</span><span className="text-gray-700 dark:text-gray-200">{acct?.device_count}</span></div>
          )}
          <div className="flex items-center justify-between"><span className="text-gray-500 dark:text-gray-400">Last sync</span><span className="text-gray-700 dark:text-gray-200" title={acct?.last_sync || undefined}>{relTime(acct?.last_sync ?? null)}</span></div>
        </div>

        {test?.connected && (
          <div className="bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200">
            ✅ Connected as {test.full_name || test.email || 'token'}{test.full_name && test.email ? ` (${test.email})` : ''} · {test.org_count ?? test.orgs?.length ?? 0} org(s){test.orgs?.[0] ? ` · ${test.orgs[0].name}` : ''}
          </div>
        )}

        {/* Discovered sites */}
        {sites.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm">
              <thead><tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
                <th className="px-3 py-2 font-medium">Mist Site</th>
                <th className="px-3 py-2 font-medium">Location</th>
                <th className="px-3 py-2 font-medium text-right">Devices</th>
                <th className="px-3 py-2 font-medium">Last Sync</th>
              </tr></thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {sites.map((s) => (
                  <tr key={s.id} className="text-gray-700 dark:text-gray-300">
                    <td className="px-3 py-2 font-medium">{s.name}</td>
                    <td className="px-3 py-2 text-gray-500 dark:text-gray-400 truncate">{[s.address, s.country_code].filter(Boolean).join(', ') || '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{s.device_count}</td>
                    <td className="px-3 py-2 whitespace-nowrap text-gray-500 dark:text-gray-400" title={s.last_sync || undefined}>{relTime(s.last_sync)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  )
}
