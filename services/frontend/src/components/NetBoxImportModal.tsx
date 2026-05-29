import { useEffect, useState } from 'react'
import clsx from 'clsx'
import Modal from './Modal'
import {
  netboxTestConnection, netboxImport, fetchNetboxImports,
  type NetBoxImportRecord,
} from '../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

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

  const loadHistory = () => fetchNetboxImports().then(setHistory).catch(() => {})
  useEffect(() => { loadHistory() }, [])

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
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50">Close</button>
          <button onClick={test} disabled={testing || !url || !token} className="py-2.5 px-4 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50">{testing ? 'Testing…' : 'Test Connection'}</button>
          <button onClick={runImport} disabled={importing || !url || !token} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{importing ? 'Importing…' : 'Import Now'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">{error}</div>}

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">NetBox URL</label>
          <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://netbox.company.com" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">API Token</label>
          <input type="password" autoComplete="off" className={inputCls} value={token} onChange={(e) => setToken(e.target.value)} placeholder="••••••••" />
          <p className="text-xs text-gray-400 mt-1">🔒 Stored securely in OpenBao. NetBox v3.x and v4.x are auto-detected.</p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Import options</label>
          <div className="flex flex-wrap gap-3">
            {OPTIONS.map((o) => (
              <label key={o.key} className={clsx('flex items-center gap-2 text-sm', o.disabled ? 'text-gray-400' : 'text-gray-700')}>
                <input type="checkbox" disabled={o.disabled} checked={!!opts[o.key]} onChange={(e) => setOpts((s) => ({ ...s, [o.key]: e.target.checked }))} />
                {o.label}
              </label>
            ))}
          </div>
        </div>

        {testResult && (
          <div className={clsx('rounded-lg px-3 py-2 text-sm border', testResult.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
            {testResult.ok ? '✅' : '❌'} {testResult.message}
          </div>
        )}

        {importing && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-3 text-sm text-blue-800">
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              Importing from NetBox… this may take a moment for large inventories.
            </div>
            <div className="h-1.5 bg-blue-100 rounded-full overflow-hidden mt-2">
              <div className="h-full bg-blue-500 rounded-full animate-pulse" style={{ width: '60%' }} />
            </div>
          </div>
        )}

        {result && (
          <div className={clsx('rounded-lg px-4 py-3 text-sm border', result.status === 'completed' ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800')}>
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
          <h4 className="text-sm font-semibold text-gray-800 mb-2">Import history</h4>
          {history.length === 0 ? (
            <p className="text-xs text-gray-400">No imports yet.</p>
          ) : (
            <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 max-h-40 overflow-y-auto">
              {history.map((h) => (
                <div key={h.id} className="flex items-center gap-3 px-3 py-2 text-xs">
                  <span className={clsx('w-1.5 h-1.5 rounded-full', h.status === 'completed' ? 'bg-green-500' : 'bg-red-500')} />
                  <span className="text-gray-600 truncate flex-1">{h.netbox_url}</span>
                  <span className="text-gray-500">{h.sites_imported}s / {h.devices_imported}d</span>
                  <span className="text-gray-400">{new Date(h.created_at).toLocaleString()}</span>
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
