import { useEffect, useState } from 'react'
import clsx from 'clsx'
import Modal from './Modal'
import {
  netboxTestConnection, netboxImport, fetchNetboxImports, netboxPreview,
  type NetBoxImportRecord, type NetBoxPreview,
} from '../api/client'
import { parseApiErrors } from '../api/errors'

const ACTION_META: Record<string, { icon: string; cls: string }> = {
  create: { icon: '✅', cls: 'text-green-600 dark:text-green-400' },
  update: { icon: '🔄', cls: 'text-blue-600 dark:text-blue-400' },
  skip: { icon: '⏭️', cls: 'text-gray-400 dark:text-gray-500' },
}

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-gray-100'

const OPTIONS = [
  { key: 'sites', label: 'Sites' },
  { key: 'devices', label: 'Devices' },
  { key: 'prefixes', label: 'Prefixes (not yet supported)', disabled: true },
  { key: 'credentials', label: 'Credentials (not yet supported)', disabled: true },
]

export default function NetBoxImportModal({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [opts, setOpts] = useState<Record<string, boolean>>({ sites: true, devices: true })
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<NetBoxImportRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<NetBoxImportRecord[]>([])
  const [preview, setPreview] = useState<NetBoxPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [filter, setFilter] = useState<'all' | 'create' | 'update' | 'skip'>('all')

  const loadHistory = () => fetchNetboxImports().then(setHistory).catch(() => {})
  useEffect(() => { loadHistory() }, [])

  const runPreview = async () => {
    if (!url || !token) { setError('NetBox URL and API token are required.'); return }
    setPreviewing(true); setError(null); setPreview(null); setResult(null)
    try { setPreview(await netboxPreview({ netbox_url: url, api_token: token, import_options: opts })) }
    catch (e) { setError(parseApiErrors(e, 'Preview failed.')) }
    finally { setPreviewing(false) }
  }

  const test = async () => {
    setTesting(true); setTestResult(null); setError(null)
    try { setTestResult(await netboxTestConnection(url, token)) }
    catch { setTestResult({ ok: false, message: 'Test request failed.' }) }
    finally { setTesting(false) }
  }

  const runImport = async () => {
    if (!url || !token) { setError('NetBox URL and API token are required.'); return }
    setImporting(true); setError(null); setResult(null)
    try {
      const rec = await netboxImport({ netbox_url: url, api_token: token, import_options: opts })
      setResult(rec)
      loadHistory()
    } catch (e) {
      const data = (e as { response?: { data?: NetBoxImportRecord } })?.response?.data
      if (data && typeof data === 'object' && 'status' in data) setResult(data as NetBoxImportRecord)
      else setError('Import failed.')
    } finally { setImporting(false) }
  }

  return (
    <Modal title="🗄 NetBox Import" onClose={onClose} size="xl"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Close</button>
          <button onClick={test} disabled={testing || !url || !token} className="py-2.5 px-4 border border-gray-300 dark:border-gray-600 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 dark:text-gray-300">{testing ? 'Testing…' : 'Test'}</button>
          <button onClick={runPreview} disabled={previewing || !url || !token} className="py-2.5 px-4 border border-gray-300 dark:border-gray-600 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 dark:text-gray-300">{previewing ? 'Previewing…' : 'Preview'}</button>
          <button onClick={runImport} disabled={importing || !url || !token} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{importing ? 'Importing…' : preview ? `Import ${preview.summary.will_create + preview.summary.will_update} devices` : 'Import Now'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{error}</div>}

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">NetBox URL</label>
          <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://netbox.company.com" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API Token</label>
          <input type="password" autoComplete="off" className={inputCls} value={token} onChange={(e) => setToken(e.target.value)} placeholder="••••••••" />
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">🔒 Stored securely in OpenBao. NetBox v3.x and v4.x are auto-detected.</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Import options</label>
          <div className="flex flex-wrap gap-3">
            {OPTIONS.map((o) => (
              <label key={o.key} className={clsx('flex items-center gap-2 text-sm', o.disabled ? 'text-gray-400 dark:text-gray-500' : 'text-gray-700 dark:text-gray-300')}>
                <input type="checkbox" disabled={o.disabled} checked={!!opts[o.key]} onChange={(e) => setOpts((s) => ({ ...s, [o.key]: e.target.checked }))} />
                {o.label}
              </label>
            ))}
          </div>
        </div>

        {testResult && (
          <div className={clsx('rounded-lg px-3 py-2 text-sm border', testResult.ok ? 'bg-green-50 dark:bg-green-900/30 border-green-200 dark:border-green-700 text-green-800 dark:text-green-400' : 'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-700 text-red-800 dark:text-red-400')}>
            {testResult.ok ? '✅' : '❌'} {testResult.message}
          </div>
        )}

        {preview && (
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 space-y-2">
            <div className="flex items-center gap-4 text-sm">
              <span className="text-green-600 dark:text-green-400 font-medium">{preview.summary.will_create} create</span>
              <span className="text-blue-600 dark:text-blue-400 font-medium">{preview.summary.will_update} update</span>
              <span className="text-gray-500 dark:text-gray-400 font-medium">{preview.summary.will_skip} skip</span>
              <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} className="ml-auto text-xs border border-gray-300 dark:border-gray-600 rounded-md px-2 py-1 dark:bg-gray-900 dark:text-gray-200">
                <option value="all">All</option><option value="create">Create</option><option value="update">Update</option><option value="skip">Skip</option>
              </select>
            </div>
            <div className="max-h-56 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-700 border-t border-gray-100 dark:border-gray-700">
              {preview.devices.filter((d) => filter === 'all' || d.action === filter).map((d, i) => (
                <div key={i} className="py-1.5 text-xs flex items-center gap-2">
                  <span className={ACTION_META[d.action]?.cls}>{ACTION_META[d.action]?.icon} {d.action.toUpperCase()}</span>
                  <span className="font-mono text-gray-700 dark:text-gray-300">{d.hostname}</span>
                  {d.role && <span className="text-gray-500">{d.role}</span>}
                  {d.site && <span className="text-gray-400">{d.site}</span>}
                  {d.credential && <span className="text-purple-600 dark:text-purple-400">🔑 {d.credential}</span>}
                  {d.action === 'update' && d.changes && d.changes.length > 0 && <span className="text-gray-400">Δ {d.changes.join(', ')}</span>}
                  {d.action === 'skip' && d.reason && <span className="text-gray-400 italic ml-auto">{d.reason}</span>}
                </div>
              ))}
            </div>
            <div className="text-xs text-gray-600 dark:text-gray-400 border-t border-gray-100 dark:border-gray-700 pt-2">
              <p className="font-medium mb-1">Credential assignments:</p>
              {Object.entries(preview.credentials.assignments).map(([name, n]) => <div key={name}>🔑 {name} → {n} device(s)</div>)}
              {preview.credentials.no_match > 0 && <div className="text-amber-600 dark:text-amber-400">⚠️ {preview.credentials.no_match} device(s) have no credential match</div>}
            </div>
          </div>
        )}

        {importing && (
          <div className="bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700 rounded-lg px-3 py-3 text-sm text-blue-800 dark:text-blue-400">
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              Importing from NetBox… this may take a moment for large inventories.
            </div>
            <div className="h-1.5 bg-blue-100 dark:bg-blue-900 rounded-full overflow-hidden mt-2">
              <div className="h-full bg-blue-500 rounded-full animate-pulse" style={{ width: '60%' }} />
            </div>
          </div>
        )}

        {result && (
          <div className={clsx('rounded-lg px-4 py-3 text-sm border', result.status === 'completed' ? 'bg-green-50 dark:bg-green-900/30 border-green-200 dark:border-green-700 text-green-800 dark:text-green-400' : 'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-700 text-red-800 dark:text-red-400')}>
            <p className="font-medium">{result.status === 'completed' ? '✅ Import complete' : '❌ Import failed'}{result.netbox_version && ` (NetBox ${result.netbox_version})`}</p>
            <ul className="mt-1 space-y-0.5">
              <li>{result.sites_imported} sites imported</li>
              <li>{result.devices_imported} devices imported</li>
              <li>{result.skipped} skipped (already exist / no IP)</li>
              <li>{result.errors.length} errors</li>
            </ul>
            {result.errors.length > 0 && (
              <details className="mt-2"><summary className="cursor-pointer text-xs">View errors</summary>
                <ul className="mt-1 text-xs list-disc list-inside max-h-32 overflow-y-auto">{result.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
              </details>
            )}
          </div>
        )}

        {/* History */}
        <div>
          <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">Import history</h4>
          {history.length === 0 ? (
            <p className="text-xs text-gray-400 dark:text-gray-500">No imports yet.</p>
          ) : (
            <div className="border border-gray-200 dark:border-gray-700 rounded-lg divide-y divide-gray-100 dark:divide-gray-700 max-h-40 overflow-y-auto">
              {history.map((h) => (
                <div key={h.id} className="flex items-center gap-3 px-3 py-2 text-xs">
                  <span className={clsx('w-1.5 h-1.5 rounded-full', h.status === 'completed' ? 'bg-green-500' : 'bg-red-500')} />
                  <span className="text-gray-600 dark:text-gray-400 truncate flex-1">{h.netbox_url}</span>
                  <span className="text-gray-500 dark:text-gray-400">{h.sites_imported}s / {h.devices_imported}d</span>
                  <span className="text-gray-400 dark:text-gray-500">{new Date(h.created_at).toLocaleString()}</span>
                  <button onClick={() => { setUrl(h.netbox_url); setToken('') }} className="text-blue-600 hover:text-blue-800">Re-import</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
