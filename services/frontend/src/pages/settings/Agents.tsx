import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { SectionHeader } from '../Settings'
import {
  fetchAgents, revokeAgent, fetchAgentTokens, createAgentToken, deleteAgentToken,
  fetchServerRoles, deleteServerRole, fetchSites,
  type Agent, type AgentToken, type ServerRole, type TargetOS, type Site,
} from '../../api/client'

const EXPIRY_OPTS = [
  { label: '1 day', hours: 24 },
  { label: '7 days', hours: 168 },
  { label: '30 days', hours: 720 },
  { label: 'Never', hours: 0 },
]

function GenerateTokenModal({ onClose, onCreated, serverUrl }: {
  onClose: () => void; onCreated: () => void; serverUrl: string
}) {
  const [description, setDescription] = useState('')
  const [maxUses, setMaxUses] = useState(1)
  const [expiryHours, setExpiryHours] = useState(168)
  const [targetOs, setTargetOs] = useState<TargetOS>('linux')
  const [siteId, setSiteId] = useState<number | ''>('')
  const [sites, setSites] = useState<Site[]>([])
  const [created, setCreated] = useState<AgentToken | null>(null)
  const [selfSigned, setSelfSigned] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Agents enrolled with this token inherit this site (optional — leave blank to
  // enroll unassigned and set the site later on the server's detail page).
  useEffect(() => { fetchSites().then(setSites).catch(() => {}) }, [])

  const generate = async () => {
    setBusy(true); setError(null)
    try {
      const expires_at = expiryHours
        ? new Date(Date.now() + expiryHours * 3600_000).toISOString() : null
      setCreated(await createAgentToken({
        description, max_uses: maxUses, expires_at, target_os: targetOs,
        site: siteId === '' ? null : siteId,
      }))
      onCreated()
    } catch { setError('Failed to generate token.') } finally { setBusy(false) }
  }

  const copy = (t: string) => navigator.clipboard?.writeText(t)
  // For a self-signed server cert: -k skips verification on the curl download,
  // --insecure / -Insecure does the same for the agent's enrollment request.
  const linuxCmd = created
    ? `curl -fsSL${selfSigned ? ' -k' : ''} ${serverUrl}/agent/install | sudo bash -s -- --server ${serverUrl} --token ${created.token}${selfSigned ? ' --insecure' : ''}`
    : ''
  const windowsCmd = created
    ? `[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12\n`
      + `Invoke-WebRequest -Uri "${serverUrl}/agent/install.ps1" -OutFile "$env:TEMP\\install.ps1"\n`
      + `powershell -ExecutionPolicy Bypass -File "$env:TEMP\\install.ps1" `
      + `-Server "${serverUrl}" -Token "${created.token}"${selfSigned ? ' -Insecure' : ''}`
    : ''
  // 'any' = "Both" → show both blocks.
  const showLinux = targetOs === 'linux' || targetOs === 'any'
  const showWindows = targetOs === 'windows' || targetOs === 'any'

  const OS_OPTS: { val: TargetOS; label: string }[] = [
    { val: 'linux', label: 'Linux' },
    { val: 'windows', label: 'Windows' },
    { val: 'any', label: 'Both' },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-4">Generate enrollment token</h3>
        {error && <div className="mb-3 text-sm text-red-600">{error}</div>}
        {!created ? (
          <div className="space-y-3">
            <label className="block text-sm">
              <span className="text-gray-700 dark:text-gray-300">Description</span>
              <input className="mt-1 w-full px-3 py-2 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600"
                value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Production web servers" />
            </label>
            <label className="block text-sm">
              <span className="text-gray-700 dark:text-gray-300">Max uses (0 = unlimited)</span>
              <input type="number" min={0} className="mt-1 w-full px-3 py-2 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600"
                value={maxUses} onChange={(e) => setMaxUses(Number(e.target.value))} />
            </label>
            <label className="block text-sm">
              <span className="text-gray-700 dark:text-gray-300">Expires</span>
              <select className="mt-1 w-full px-3 py-2 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600"
                value={expiryHours} onChange={(e) => setExpiryHours(Number(e.target.value))}>
                {EXPIRY_OPTS.map((o) => <option key={o.label} value={o.hours}>{o.label}</option>)}
              </select>
            </label>
            <div className="block text-sm">
              <span className="text-gray-700 dark:text-gray-300">Target OS</span>
              <div className="mt-1 inline-flex rounded-lg border dark:border-gray-600 overflow-hidden">
                {OS_OPTS.map((o) => (
                  <button key={o.val} type="button" onClick={() => setTargetOs(o.val)}
                    className={clsx('px-4 py-2 text-sm',
                      targetOs === o.val
                        ? 'bg-blue-600 text-white'
                        : 'bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300')}>
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
            <label className="block text-sm">
              <span className="text-gray-700 dark:text-gray-300">Site <span className="text-gray-400">(optional)</span></span>
              <select className="mt-1 w-full px-3 py-2 text-sm border rounded-lg dark:bg-gray-900 dark:border-gray-600"
                value={siteId} onChange={(e) => setSiteId(e.target.value === '' ? '' : Number(e.target.value))}>
                <option value="">Unassigned — set later</option>
                {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">
                Agents enrolled with this token are assigned to this site automatically.
              </span>
            </label>
            <div className="flex gap-2 justify-end pt-2">
              <button onClick={onClose} className="px-4 py-2 text-sm border rounded-lg dark:border-gray-600 dark:text-gray-300">Cancel</button>
              <button onClick={generate} disabled={busy} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50">
                {busy ? 'Generating…' : 'Generate'}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700 rounded-lg p-3 text-xs text-amber-800 dark:text-amber-300">
              ⚠️ Copy this token now — it won't be shown again.
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2 text-xs font-mono bg-gray-100 dark:bg-gray-900 rounded-lg break-all">{created.token}</code>
              <button onClick={() => copy(created.token)} className="px-3 py-2 text-xs border rounded-lg dark:border-gray-600 dark:text-gray-300">Copy</button>
            </div>
            <label className="flex items-start gap-2 text-xs text-gray-700 dark:text-gray-300">
              <input type="checkbox" className="mt-0.5" checked={selfSigned}
                onChange={(e) => setSelfSigned(e.target.checked)} />
              <span>Server uses self-signed certificate
                <span className="text-gray-500 dark:text-gray-400"> (adds <code>--insecure</code> to install command)</span>
              </span>
            </label>
            {showLinux && (
              <div>
                <p className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Linux install command:</p>
                <div className="flex items-start gap-2">
                  <code className="flex-1 px-3 py-2 text-xs font-mono bg-gray-100 dark:bg-gray-900 rounded-lg break-all">{linuxCmd}</code>
                  <button onClick={() => copy(linuxCmd)} className="px-3 py-2 text-xs border rounded-lg dark:border-gray-600 dark:text-gray-300">Copy</button>
                </div>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">ℹ️ Supports linux/amd64, linux/arm64 — auto-detected during install.</p>
              </div>
            )}
            {showWindows && (
              <div>
                <p className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Windows install command (PowerShell):</p>
                <div className="flex items-start gap-2">
                  <code className="flex-1 px-3 py-2 text-xs font-mono bg-gray-100 dark:bg-gray-900 rounded-lg whitespace-pre-wrap break-all">{windowsCmd}</code>
                  <button onClick={() => copy(windowsCmd)} className="px-3 py-2 text-xs border rounded-lg dark:border-gray-600 dark:text-gray-300">Copy</button>
                </div>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">ℹ️ Supports windows/amd64 — run PowerShell as Administrator.</p>
              </div>
            )}
            <div className="flex justify-end pt-2">
              <button onClick={onClose} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg">Done</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function statusDot(status: string) {
  return clsx('inline-block w-2 h-2 rounded-full mr-1.5',
    status === 'active' ? 'bg-green-500' : status === 'revoked' ? 'bg-red-500' : 'bg-gray-400')
}

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [tokens, setTokens] = useState<AgentToken[]>([])
  const [roles, setRoles] = useState<ServerRole[]>([])
  const [showModal, setShowModal] = useState(false)

  const load = useCallback(() => {
    fetchAgents().then(setAgents).catch(() => {})
    fetchAgentTokens().then(setTokens).catch(() => {})
    fetchServerRoles().then(setRoles).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const serverUrl = window.location.origin

  return (
    <div className="space-y-8">
      <SectionHeader title="Agents" description="Lightweight server-monitoring agents (Linux/Windows)."
        action={<button onClick={() => setShowModal(true)} className="px-3 py-2 text-sm bg-blue-600 text-white rounded-lg">+ Generate Token</button>} />

      {/* Enrollment tokens */}
      <div>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Enrollment Tokens</h3>
        <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800/50 text-gray-500 dark:text-gray-400">
              <tr><th className="text-left px-3 py-2 font-medium">Description</th><th className="text-left px-3 py-2 font-medium">Uses</th><th className="text-left px-3 py-2 font-medium">Active</th><th className="px-3 py-2"></th></tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {tokens.length === 0 && <tr><td colSpan={4} className="px-3 py-4 text-gray-400 text-center">No tokens.</td></tr>}
              {tokens.map((t) => (
                <tr key={t.id} className="text-gray-700 dark:text-gray-300">
                  <td className="px-3 py-2">{t.description || <span className="font-mono text-xs">{t.token}</span>}</td>
                  <td className="px-3 py-2">{t.use_count}/{t.max_uses || '∞'}</td>
                  <td className="px-3 py-2">{t.is_active ? '✅' : '—'}</td>
                  <td className="px-3 py-2 text-right">
                    <button onClick={() => deleteAgentToken(t.id).then(load)} className="text-red-600 hover:text-red-800 text-xs">Revoke</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Active agents */}
      <div>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Active Agents</h3>
        <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800/50 text-gray-500 dark:text-gray-400">
              <tr><th className="text-left px-3 py-2 font-medium">Hostname</th><th className="text-left px-3 py-2 font-medium">OS</th><th className="text-left px-3 py-2 font-medium">Version</th><th className="text-left px-3 py-2 font-medium">Roles</th><th className="text-left px-3 py-2 font-medium">Status</th><th className="px-3 py-2"></th></tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {agents.length === 0 && <tr><td colSpan={6} className="px-3 py-4 text-gray-400 text-center">No agents enrolled.</td></tr>}
              {agents.map((a) => (
                <tr key={a.id} className="text-gray-700 dark:text-gray-300">
                  <td className="px-3 py-2 font-medium">{a.hostname}</td>
                  <td className="px-3 py-2">{a.os} {a.arch}</td>
                  <td className="px-3 py-2">{a.version || '—'}</td>
                  <td className="px-3 py-2">{a.role_types.join(', ') || '—'}</td>
                  <td className="px-3 py-2"><span className={statusDot(a.status)} />{a.status}</td>
                  <td className="px-3 py-2 text-right">
                    {a.status !== 'revoked' && <button onClick={() => revokeAgent(a.id).then(load)} className="text-red-600 hover:text-red-800 text-xs">Revoke</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Server role profiles */}
      <div>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Server Role Profiles</h3>
        <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800/50 text-gray-500 dark:text-gray-400">
              <tr><th className="text-left px-3 py-2 font-medium">Role</th><th className="text-left px-3 py-2 font-medium">Services</th><th className="text-left px-3 py-2 font-medium">Agents</th><th className="px-3 py-2"></th></tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {roles.map((r) => (
                <tr key={r.id} className="text-gray-700 dark:text-gray-300">
                  <td className="px-3 py-2 font-medium">{r.name}{r.is_builtin && <span className="ml-2 text-xs text-gray-400">built-in</span>}</td>
                  <td className="px-3 py-2 text-xs">{r.windows_services.length} Win · {r.linux_services.length} Lin</td>
                  <td className="px-3 py-2">{r.agent_count}</td>
                  <td className="px-3 py-2 text-right">
                    {!r.is_builtin && <button onClick={() => deleteServerRole(r.id).then(load)} className="text-red-600 hover:text-red-800 text-xs">Delete</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && <GenerateTokenModal onClose={() => setShowModal(false)} onCreated={load} serverUrl={serverUrl} />}
    </div>
  )
}
