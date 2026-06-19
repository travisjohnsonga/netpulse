// BackupPanel — self-contained Settings panel for platform backup/restore.
//
// Rendered under Settings → System → "Backup & Restore" (see System.tsx).
// Self-contained: talks to the backend directly via the shared axios `api`
// instance rather than api/client.ts helpers:
//   GET/PUT /api/backup/config/        — schedule + includes + destination
//   POST    /api/backup/run/           — Backup Now (mandatory >=12-char password
//                                        when OpenBao/certs/postgres are included)
//   GET     /api/backup/records/       — history
//   GET     /api/backup/download/{id}/ — download a local artifact
//   POST    /api/backup/test-connection/
import { useEffect, useState } from 'react'
import { api } from '../../api/client'

const input =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const btn =
  'px-4 py-2 text-sm font-medium rounded-lg disabled:opacity-50'

type BackupConfig = {
  schedule: 'disabled' | 'daily' | 'weekly' | 'monthly'
  schedule_time: string
  schedule_day: number | null
  retention_days: number
  include_postgres: boolean
  include_influxdb: boolean
  include_openbao: boolean
  include_config_files: boolean
  include_ssl_certs: boolean
  include_influxdb_days: number
  local_path: string
  destination: 'local' | 'scp' | 'git' | 's3'
  scp_host: string; scp_port: number; scp_username: string; scp_path: string
  git_repo_url: string; git_branch: string; git_path: string
  s3_bucket: string; s3_prefix: string; s3_endpoint: string; s3_region: string
  encryption_required: boolean
  scp_password_set: boolean; scp_key_set: boolean; git_ssh_key_set: boolean
  s3_access_key_set: boolean; s3_secret_set: boolean; encryption_password_set: boolean
}

type BackupRecord = {
  id: number
  started_at: string
  completed_at: string | null
  status: 'running' | 'success' | 'failed' | 'partial'
  triggered_by: string
  components: Record<string, boolean>
  filename: string
  file_size_bytes: number | null
  error_message: string
  duration_seconds: number | null
  encrypted: boolean
  encryption_hint: string
}

const MIN_PW = 12

function humanSize(n: number | null): string {
  if (!n) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

export default function BackupPanel() {
  const [cfg, setCfg] = useState<BackupConfig | null>(null)
  const [records, setRecords] = useState<BackupRecord[]>([])
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string>('')
  const [err, setErr] = useState<string>('')

  // Backup Now state
  const [running, setRunning] = useState(false)
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [hint, setHint] = useState('')

  // Secrets to (re)write on save
  const [scpPassword, setScpPassword] = useState('')
  const [s3Access, setS3Access] = useState('')
  const [s3Secret, setS3Secret] = useState('')
  const [schedPw, setSchedPw] = useState('')

  const load = async () => {
    const [c, r] = await Promise.all([
      api.get('/backup/config/'),
      api.get('/backup/records/'),
    ])
    setCfg(c.data)
    const rows = Array.isArray(r.data) ? r.data : r.data.results
    setRecords(rows || [])
  }

  useEffect(() => { load().catch((e) => setErr(String(e))) }, [])

  if (!cfg) return <div className="p-4 text-sm text-gray-500">Loading…</div>

  const sensitive = cfg.include_openbao || cfg.include_ssl_certs || cfg.include_postgres
  const update = (patch: Partial<BackupConfig>) => setCfg({ ...cfg, ...patch })

  const saveConfig = async () => {
    setSaving(true); setMsg(''); setErr('')
    try {
      const body: Record<string, unknown> = { ...cfg }
      // Don't echo back the read-only *_set flags.
      Object.keys(body).forEach((k) => { if (k.endsWith('_set')) delete body[k] })
      if (scpPassword) body.scp_password = scpPassword
      if (s3Access) body.s3_access_key = s3Access
      if (s3Secret) body.s3_secret = s3Secret
      if (schedPw) body.encryption_password = schedPw
      const resp = await api.put('/backup/config/', body)
      setCfg(resp.data)
      setScpPassword(''); setS3Access(''); setS3Secret(''); setSchedPw('')
      setMsg('Saved.')
    } catch (e: any) {
      setErr(e?.response?.data?.error || 'Could not save the configuration.')
    } finally { setSaving(false) }
  }

  const runNow = async () => {
    setErr(''); setMsg('')
    if (sensitive) {
      if (pw.length < MIN_PW) { setErr(`Encryption password must be at least ${MIN_PW} characters.`); return }
      if (pw !== pw2) { setErr('Passwords do not match.'); return }
    }
    setRunning(true)
    try {
      await api.post('/backup/run/', {
        include_postgres: cfg.include_postgres,
        include_openbao: cfg.include_openbao,
        include_config: cfg.include_config_files,
        include_certs: cfg.include_ssl_certs,
        include_influxdb: cfg.include_influxdb,
        password: pw || undefined,
        password_hint: hint || undefined,
      })
      setPw(''); setPw2(''); setHint('')
      setMsg('Backup completed.')
      await load()
    } catch (e: any) {
      setErr(e?.response?.data?.error || 'Backup failed.')
    } finally { setRunning(false) }
  }

  const testConnection = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api.post('/backup/test-connection/', {})
      setMsg(`${r.data.ok ? 'OK' : 'Failed'}: ${r.data.detail}`)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Could not reach the destination.')
    }
  }

  const download = (id: number) => {
    // Authed download — open in a new tab (axios adds the auth header on XHR; for
    // a simple link, wiring a token query/blob fetch is left to integration).
    api.get(`/backup/download/${id}/`, { responseType: 'blob' }).then((r) => {
      const url = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = url
      const rec = records.find((x) => x.id === id)
      a.download = rec?.filename || `backup-${id}`
      a.click()
      URL.revokeObjectURL(url)
    }).catch(() => setErr('Download is no longer available.'))
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Platform Backup</h2>

      {msg && <div className="text-sm text-green-600 dark:text-green-400">{msg}</div>}
      {err && <div className="text-sm text-red-600 dark:text-red-400">{err}</div>}

      {/* Schedule */}
      <section className="space-y-3 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="font-medium text-gray-900 dark:text-gray-100">Schedule</h3>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">Frequency
            <select className={input} value={cfg.schedule}
              onChange={(e) => update({ schedule: e.target.value as BackupConfig['schedule'] })}>
              <option value="disabled">Disabled</option>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </label>
          <label className="text-sm">Time
            <input type="time" className={input} value={cfg.schedule_time}
              onChange={(e) => update({ schedule_time: e.target.value })} />
          </label>
          {cfg.schedule === 'weekly' && (
            <label className="text-sm">Day of week (0=Mon)
              <input type="number" min={0} max={6} className={input} value={cfg.schedule_day ?? 0}
                onChange={(e) => update({ schedule_day: Number(e.target.value) })} />
            </label>
          )}
          {cfg.schedule === 'monthly' && (
            <label className="text-sm">Day of month (1-28)
              <input type="number" min={1} max={28} className={input} value={cfg.schedule_day ?? 1}
                onChange={(e) => update({ schedule_day: Number(e.target.value) })} />
            </label>
          )}
          <label className="text-sm">Retention (days)
            <input type="number" min={1} className={input} value={cfg.retention_days}
              onChange={(e) => update({ retention_days: Number(e.target.value) })} />
          </label>
        </div>
        {cfg.schedule !== 'disabled' && sensitive && (
          <div className="space-y-1">
            <label className="text-sm">Scheduled-backup encryption password
              <input type="password" className={input} value={schedPw}
                placeholder={cfg.encryption_password_set ? '•••••••• (stored)' : 'Required for scheduled secret backups'}
                onChange={(e) => setSchedPw(e.target.value)} />
            </label>
            {!cfg.encryption_password_set && (
              <p className="text-xs text-amber-600">
                Scheduled backups that include secrets are skipped until an encryption password is stored.
              </p>
            )}
          </div>
        )}
      </section>

      {/* Includes */}
      <section className="space-y-2 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="font-medium text-gray-900 dark:text-gray-100">What to include</h3>
        {([
          ['include_postgres', 'PostgreSQL database'],
          ['include_openbao', 'OpenBao secrets (snapshot)'],
          ['include_ssl_certs', 'SSL certificates'],
          ['include_config_files', 'Config files (.env, compose, nginx)'],
          ['include_influxdb', 'InfluxDB time-series (large)'],
        ] as [keyof BackupConfig, string][]).map(([k, label]) => (
          <label key={k} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={Boolean(cfg[k])}
              onChange={(e) => update({ [k]: e.target.checked } as Partial<BackupConfig>)} />
            {label}
          </label>
        ))}
        {sensitive && (
          <p className="text-xs text-amber-600">
            Includes sensitive data — backups will be encrypted (AES-256, mandatory).
          </p>
        )}
      </section>

      {/* Destination */}
      <section className="space-y-3 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="font-medium text-gray-900 dark:text-gray-100">Destination</h3>
        <label className="text-sm">Type
          <select className={input} value={cfg.destination}
            onChange={(e) => update({ destination: e.target.value as BackupConfig['destination'] })}>
            <option value="local">Local filesystem</option>
            <option value="scp">SCP / SFTP</option>
            <option value="git">Git repository</option>
            <option value="s3">S3 / object storage</option>
          </select>
        </label>
        <label className="text-sm">Local path
          <input className={input} value={cfg.local_path}
            onChange={(e) => update({ local_path: e.target.value })} />
        </label>
        {cfg.destination === 'scp' && (
          <div className="grid grid-cols-2 gap-3">
            <input className={input} placeholder="Host" value={cfg.scp_host}
              onChange={(e) => update({ scp_host: e.target.value })} />
            <input className={input} type="number" placeholder="Port" value={cfg.scp_port}
              onChange={(e) => update({ scp_port: Number(e.target.value) })} />
            <input className={input} placeholder="Username" value={cfg.scp_username}
              onChange={(e) => update({ scp_username: e.target.value })} />
            <input className={input} placeholder="Remote path" value={cfg.scp_path}
              onChange={(e) => update({ scp_path: e.target.value })} />
            <input className={input} type="password"
              placeholder={cfg.scp_password_set ? '•••••••• (stored)' : 'Password'}
              value={scpPassword} onChange={(e) => setScpPassword(e.target.value)} />
          </div>
        )}
        {cfg.destination === 'git' && (
          <div className="grid grid-cols-2 gap-3">
            <input className={input} placeholder="Repo URL" value={cfg.git_repo_url}
              onChange={(e) => update({ git_repo_url: e.target.value })} />
            <input className={input} placeholder="Branch" value={cfg.git_branch}
              onChange={(e) => update({ git_branch: e.target.value })} />
            <input className={input} placeholder="Path" value={cfg.git_path}
              onChange={(e) => update({ git_path: e.target.value })} />
          </div>
        )}
        {cfg.destination === 's3' && (
          <div className="grid grid-cols-2 gap-3">
            <input className={input} placeholder="Bucket" value={cfg.s3_bucket}
              onChange={(e) => update({ s3_bucket: e.target.value })} />
            <input className={input} placeholder="Prefix" value={cfg.s3_prefix}
              onChange={(e) => update({ s3_prefix: e.target.value })} />
            <input className={input} placeholder="Endpoint (optional)" value={cfg.s3_endpoint}
              onChange={(e) => update({ s3_endpoint: e.target.value })} />
            <input className={input} placeholder="Region" value={cfg.s3_region}
              onChange={(e) => update({ s3_region: e.target.value })} />
            <input className={input} type="password"
              placeholder={cfg.s3_access_key_set ? '•••• (stored)' : 'Access key'}
              value={s3Access} onChange={(e) => setS3Access(e.target.value)} />
            <input className={input} type="password"
              placeholder={cfg.s3_secret_set ? '•••• (stored)' : 'Secret key'}
              value={s3Secret} onChange={(e) => setS3Secret(e.target.value)} />
          </div>
        )}
        <div className="flex gap-2">
          <button className={`${btn} bg-blue-600 text-white`} disabled={saving} onClick={saveConfig}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button className={`${btn} border border-gray-300 dark:border-gray-600`} onClick={testConnection}>
            Test connection
          </button>
        </div>
      </section>

      {/* Backup Now */}
      <section className="space-y-3 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="font-medium text-gray-900 dark:text-gray-100">Backup now</h3>
        {sensitive && (
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm">Encryption password (min {MIN_PW})
              <input type="password" className={input} value={pw}
                onChange={(e) => setPw(e.target.value)} />
            </label>
            <label className="text-sm">Confirm password
              <input type="password" className={input} value={pw2}
                onChange={(e) => setPw2(e.target.value)} />
            </label>
            <label className="text-sm col-span-2">Password hint (not secret — stored to help you remember)
              <input className={input} value={hint} onChange={(e) => setHint(e.target.value)} />
            </label>
          </div>
        )}
        <button className={`${btn} bg-green-600 text-white`} disabled={running} onClick={runNow}>
          {running ? 'Running…' : 'Backup now'}
        </button>
        {running && <p className="text-xs text-gray-500">A full backup can take a minute or more…</p>}
      </section>

      {/* History */}
      <section className="space-y-2">
        <h3 className="font-medium text-gray-900 dark:text-gray-100">Recent backups</h3>
        <table className="w-full text-sm">
          <thead className="text-left text-gray-500">
            <tr>
              <th className="py-1">Started</th><th>Status</th><th>Trigger</th>
              <th>Encrypted</th><th>Size</th><th></th>
            </tr>
          </thead>
          <tbody>
            {records.length === 0 && (
              <tr><td colSpan={6} className="py-2 text-gray-400">No backups yet.</td></tr>
            )}
            {records.map((r) => (
              <tr key={r.id} className="border-t border-gray-100 dark:border-gray-800">
                <td className="py-1">{new Date(r.started_at).toLocaleString()}</td>
                <td className={r.status === 'success' ? 'text-green-600'
                  : r.status === 'failed' ? 'text-red-600' : 'text-gray-500'}>{r.status}</td>
                <td>{r.triggered_by}</td>
                <td>{r.encrypted ? '🔒' : '—'}</td>
                <td>{humanSize(r.file_size_bytes)}</td>
                <td>
                  {r.status === 'success' && r.filename && (
                    <button className="text-blue-600 hover:underline" onClick={() => download(r.id)}>
                      Download
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Restore note */}
      <section className="text-xs text-gray-500 border-t border-gray-200 dark:border-gray-700 pt-3">
        <p>
          Restore is performed from the server CLI for safety:
          <code className="mx-1">./netpulse.sh restore &lt;backup-file&gt;</code>.
          Encrypted archives (.enc.tar.gz) prompt for the password.
        </p>
      </section>
    </div>
  )
}
