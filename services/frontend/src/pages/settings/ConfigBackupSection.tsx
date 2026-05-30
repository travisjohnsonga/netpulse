import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchConfigBackup, saveConfigBackup, testGit, syncConfigNow,
  type ConfigBackupSettings,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const PROVIDERS = [
  ['github', 'GitHub'], ['gitlab_cloud', 'GitLab (cloud)'], ['gitlab_self', 'GitLab (self-hosted)'],
  ['gitea', 'Gitea'], ['bitbucket', 'Bitbucket'], ['generic_https', 'Generic HTTPS'], ['generic_ssh', 'Generic SSH'],
]
const AUTH_METHODS = [['token', 'Personal Access Token'], ['ssh_key', 'SSH Key'], ['deploy_key', 'Deploy Key']]
const FREQUENCIES = [['on_backup', 'On every backup'], ['hourly', 'Hourly'], ['daily', 'Daily']]

function gb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(2)} GB`
}
function relTime(ts: string | null): string {
  if (!ts) return 'never'
  return new Date(ts).toLocaleString()
}

export default function ConfigBackupSection() {
  const [s, setS] = useState<ConfigBackupSettings | null>(null)
  const [credential, setCredential] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [savedLocal, setSavedLocal] = useState(false)
  const [savedGit, setSavedGit] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => { fetchConfigBackup().then(setS).catch(() => setError('Failed to load config-backup settings.')) }, [])

  if (error) return <div className="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:text-yellow-400">{error}</div>
  if (!s) return <div className="flex items-center justify-center py-8"><div className="w-5 h-5 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>

  const set = (patch: Partial<ConfigBackupSettings>) => setS((p) => (p ? { ...p, ...patch } : p))

  const saveLocal = async () => {
    setBusy('local'); setError(null)
    try {
      setS(await saveConfigBackup({ local_enabled: s.local_enabled, local_path: s.local_path, local_retention_days: s.local_retention_days }))
      setSavedLocal(true); setTimeout(() => setSavedLocal(false), 2000)
    } catch { setError('Failed to save local settings.') } finally { setBusy(null) }
  }

  const saveGit = async () => {
    setBusy('git'); setError(null)
    try {
      const payload: Partial<ConfigBackupSettings> & { git_credential?: string } = {
        git_enabled: s.git_enabled, git_provider: s.git_provider, git_repo_url: s.git_repo_url,
        git_branch: s.git_branch, git_auth_method: s.git_auth_method,
        git_commit_author: s.git_commit_author, git_commit_email: s.git_commit_email,
        git_sync_frequency: s.git_sync_frequency,
      }
      if (credential) payload.git_credential = credential
      setS(await saveConfigBackup(payload)); setCredential('')
      setSavedGit(true); setTimeout(() => setSavedGit(false), 2000)
    } catch { setError('Failed to save git settings.') } finally { setBusy(null) }
  }

  const doTest = async () => {
    setBusy('test'); setTestResult(null)
    try { setTestResult(await testGit(s.git_repo_url)) }
    catch { setTestResult({ ok: false, message: 'Test request failed.' }) } finally { setBusy(null) }
  }

  const doSync = async () => {
    setBusy('sync'); setTestResult(null)
    try {
      const r = await syncConfigNow()
      setTestResult({ ok: r.ok, message: r.message })
      setS(await fetchConfigBackup())
    } catch { setTestResult({ ok: false, message: 'Sync request failed.' }) } finally { setBusy(null) }
  }

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100">Configuration Backup</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">Where device configs are stored and optionally mirrored to git.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Local storage */}
        <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-3">
          <label className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-100">
            <input type="checkbox" checked={s.local_enabled} onChange={(e) => set({ local_enabled: e.target.checked })} /> Enable local backup
          </label>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Path</label>
            <input className={inputCls} value={s.local_path} onChange={(e) => set({ local_path: e.target.value })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Retention (days)</label>
            <input type="number" className={inputCls} value={s.local_retention_days} onChange={(e) => set({ local_retention_days: Number(e.target.value) })} />
          </div>
          <p className="text-xs text-gray-400 dark:text-gray-500">Used: {gb(s.local_used_bytes)}</p>
          <button onClick={saveLocal} disabled={busy === 'local'} className={clsx('px-4 py-2 text-sm rounded-lg font-medium text-white', savedLocal ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700')}>{savedLocal ? 'Saved!' : 'Save'}</button>
        </div>

        {/* Git sync */}
        <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-3">
          <label className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-100">
            <input type="checkbox" checked={s.git_enabled} onChange={(e) => set({ git_enabled: e.target.checked })} /> Enable git sync
          </label>

          <div className={clsx('space-y-3', !s.git_enabled && 'opacity-50 pointer-events-none')}>
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Provider</label>
              <select className={inputCls} value={s.git_provider} onChange={(e) => set({ git_provider: e.target.value })}>
                <option value="">Select…</option>
                {PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="flex gap-2">
              <div className="flex-1"><label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Repository URL</label><input className={inputCls} value={s.git_repo_url} onChange={(e) => set({ git_repo_url: e.target.value })} placeholder="https://github.com/org/configs" /></div>
              <div className="w-28"><label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Branch</label><input className={inputCls} value={s.git_branch} onChange={(e) => set({ git_branch: e.target.value })} /></div>
            </div>
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Auth method</label>
              <select className={inputCls} value={s.git_auth_method} onChange={(e) => set({ git_auth_method: e.target.value })}>
                <option value="">Select…</option>
                {AUTH_METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Token / Key</label>
              <input type="password" autoComplete="off" className={inputCls} value={credential} onChange={(e) => setCredential(e.target.value)} placeholder={s.git_vault_path ? '•••••••• (unchanged)' : ''} />
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">🔒 Git credentials stored in OpenBao.</p>
            </div>
            <div className="flex gap-2">
              <div className="flex-1"><label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Commit author</label><input className={inputCls} value={s.git_commit_author} onChange={(e) => set({ git_commit_author: e.target.value })} /></div>
              <div className="flex-1"><label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Commit email</label><input className={inputCls} value={s.git_commit_email} onChange={(e) => set({ git_commit_email: e.target.value })} placeholder="netpulse@company.com" /></div>
            </div>
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Sync frequency</label>
              <select className={inputCls} value={s.git_sync_frequency} onChange={(e) => set({ git_sync_frequency: e.target.value })}>
                {FREQUENCIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-400">
              Last sync: {relTime(s.last_sync_at)} {s.last_sync_success === true ? '✅' : s.last_sync_success === false ? '❌' : ''}
              {s.last_commit_sha && <> · last commit <span className="font-mono">{s.last_commit_sha.slice(0, 7)}</span></>}
            </div>
          </div>

          {testResult && (
            <div className={clsx('rounded-lg px-3 py-2 text-xs border', testResult.ok ? 'bg-green-50 border-green-200 text-green-800 dark:bg-green-900/30 dark:border-gray-700 dark:text-green-400' : 'bg-red-50 border-red-200 text-red-800 dark:bg-red-900/30 dark:border-gray-700 dark:text-red-400')}>
              {testResult.ok ? '✅' : '❌'} {testResult.message}
            </div>
          )}

          <div className="flex gap-2">
            <button onClick={doTest} disabled={busy === 'test'} className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300 disabled:opacity-50">{busy === 'test' ? 'Testing…' : 'Test Connection'}</button>
            <button onClick={doSync} disabled={busy === 'sync'} className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 dark:text-gray-300 disabled:opacity-50">{busy === 'sync' ? 'Syncing…' : 'Sync Now'}</button>
            <button onClick={saveGit} disabled={busy === 'git'} className={clsx('px-4 py-2 text-sm rounded-lg font-medium text-white ml-auto', savedGit ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700')}>{savedGit ? 'Saved!' : 'Save'}</button>
          </div>
        </div>
      </div>
    </section>
  )
}
