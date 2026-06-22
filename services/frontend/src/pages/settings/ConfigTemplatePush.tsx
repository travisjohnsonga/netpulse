import { useEffect, useMemo, useState } from 'react'
import Modal from '../../components/Modal'
import { parseApiErrors } from '../../api/errors'
import {
  fetchDevices, previewConfigTemplate, pushConfigTemplate,
  type ConfigTemplate, type Device, type PushResponse,
} from '../../api/client'
import { detectVariables, isSensitiveName } from './ConfigTemplateEdit'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

type Mode = 'platform' | 'site' | 'specific'

export default function PushTemplateModal({ template, onClose, onPushed }: {
  template: ConfigTemplate
  onClose: () => void
  onPushed: (summary: string) => void
}) {
  const [devices, setDevices] = useState<Device[]>([])
  const [vars, setVars] = useState<Record<string, string>>({ ...template.variables })
  const [mode, setMode] = useState<Mode>('platform')
  const [site, setSite] = useState<string>('')
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [preview, setPreview] = useState<string | null>(null)
  const [previewErr, setPreviewErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [results, setResults] = useState<PushResponse | null>(null)

  useEffect(() => {
    fetchDevices({ page_size: '500' }).then((d) => setDevices(d.results)).catch(() => setDevices([]))
  }, [])

  const platformMatch = (d: Device) => !template.platform || d.platform === template.platform
  const matchingDevices = useMemo(() => devices.filter(platformMatch), [devices, template.platform])
  const sites = useMemo(
    () => Array.from(new Set(matchingDevices.map((d) => d.site_name).filter(Boolean))).sort() as string[],
    [matchingDevices],
  )

  const variableNames = useMemo(() => {
    const all = new Set<string>([...detectVariables(template.template_content), ...Object.keys(template.variables)])
    return Array.from(all).sort()
  }, [template])

  // Resolve the target device set from the chosen mode.
  const targets: Device[] = useMemo(() => {
    if (mode === 'platform') return matchingDevices
    if (mode === 'site') return matchingDevices.filter((d) => d.site_name === site)
    return devices.filter((d) => checked.has(d.id))
  }, [mode, matchingDevices, site, checked, devices])

  const toggle = (id: number) => setChecked((prev) => {
    const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next
  })

  const setVar = (k: string, v: string) => setVars((prev) => ({ ...prev, [k]: v }))

  const runPreview = async () => {
    const first = targets[0]
    if (!first) return
    setPreviewErr(null); setPreview(null)
    try {
      const res = await previewConfigTemplate(template.id, first.id, vars)
      setPreview(`# ${res.device}\n${res.rendered}`)
    } catch (e) {
      setPreviewErr(parseApiErrors(e, 'Render failed.'))
    }
  }

  const push = async () => {
    if (targets.length === 0) { setErr('Select at least one target device.'); return }
    if (!window.confirm(`Push "${template.name}" to ${targets.length} device${targets.length !== 1 ? 's' : ''}? This changes their running config.`)) return
    setBusy(true); setErr(null)
    try {
      const res = await pushConfigTemplate(template.id, targets.map((d) => d.id), vars)
      setResults(res)
      onPushed(`Pushed "${template.name}": ${res.succeeded}/${res.total} succeeded`)
    } catch (e) {
      // 403 when ALLOW_CONFIG_PUSH is off, or other failure.
      setErr(parseApiErrors(e, 'Push failed.'))
    } finally {
      setBusy(false)
    }
  }

  // ── Results view ──
  if (results) {
    return (
      <Modal title="Push Complete" onClose={onClose} size="lg"
        footer={<button onClick={onClose} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Close</button>}>
        <div className="space-y-3">
          <div className="text-sm font-medium text-gray-800 dark:text-gray-200">
            {results.succeeded}/{results.total} succeeded
          </div>
          <div className="border border-gray-200 dark:border-gray-700 rounded-lg divide-y divide-gray-100 dark:divide-gray-700 max-h-72 overflow-y-auto">
            {results.results.map((r) => (
              <div key={r.device_id} className="flex items-center justify-between px-3 py-2 text-sm">
                <span className="font-mono text-gray-700 dark:text-gray-300">{r.hostname}</span>
                {r.success
                  ? <span className="text-green-600 dark:text-green-400">✅ success</span>
                  : <span className="text-red-600 dark:text-red-400" title={r.error}>❌ {r.error}</span>}
              </div>
            ))}
          </div>
        </div>
      </Modal>
    )
  }

  // ── Push form ──
  return (
    <Modal title={`Push: ${template.name}`} onClose={onClose} size="lg"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={push} disabled={busy || targets.length === 0} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{busy ? 'Pushing…' : `Push to ${targets.length} Device${targets.length !== 1 ? 's' : ''}`}</button>
        </>
      }>
      <div className="space-y-4">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}

        {variableNames.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">Variables</label>
            <div className="space-y-2">
              {variableNames.map((n) => {
                const sensitive = isSensitiveName(n)
                return (
                  <div key={n} className="flex items-center gap-2">
                    <label className="w-40 shrink-0 text-xs font-mono text-gray-600 dark:text-gray-300 flex items-center gap-1">{n}{sensitive && <span title="sensitive">🔒</span>}</label>
                    <input className={inputCls} type={sensitive ? 'password' : 'text'} autoComplete="new-password"
                      value={vars[n] ?? ''} onChange={(e) => setVar(n, e.target.value)}
                      placeholder={sensitive ? 'required for push' : ''} />
                  </div>
                )
              })}
            </div>
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">Target Devices</label>
          <div className="space-y-2 text-sm">
            <label className="flex items-center gap-2">
              <input type="radio" checked={mode === 'platform'} onChange={() => setMode('platform')} />
              All {template.platform || 'matching'} devices ({matchingDevices.length})
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={mode === 'site'} onChange={() => setMode('site')} />
              Devices in site:
              <select className={`${inputCls} w-auto py-1`} value={site} onChange={(e) => { setSite(e.target.value); setMode('site') }}>
                <option value="">select…</option>
                {sites.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={mode === 'specific'} onChange={() => setMode('specific')} />
              Select specific devices
            </label>
          </div>

          {mode === 'specific' && (
            <div className="mt-2 border border-gray-200 dark:border-gray-700 rounded-lg divide-y divide-gray-100 dark:divide-gray-700 max-h-48 overflow-y-auto">
              {devices.map((d) => {
                const mismatch = !platformMatch(d)
                return (
                  <label key={d.id} className="flex items-center gap-2 px-3 py-1.5 text-sm">
                    <input type="checkbox" checked={checked.has(d.id)} onChange={() => toggle(d.id)} disabled={mismatch} />
                    <span className={`font-mono ${mismatch ? 'text-gray-400 dark:text-gray-600' : 'text-gray-700 dark:text-gray-300'}`}>{d.display_hostname || d.hostname}</span>
                    {mismatch && <span className="text-xs text-amber-600 dark:text-amber-500">platform mismatch ({d.platform})</span>}
                  </label>
                )
              })}
            </div>
          )}
        </div>

        <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 text-sm text-amber-800 dark:text-amber-300">
          ⚠️ This will push config changes to <strong>{targets.length}</strong> device{targets.length !== 1 ? 's' : ''}. Preview and test on one device first.
        </div>

        <div>
          <button onClick={runPreview} disabled={targets.length === 0} className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">
            Preview (first target)
          </button>
          {previewErr && <div className="mt-2 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-xs text-red-700 dark:text-red-400">{previewErr}</div>}
          {preview != null && (
            <pre className="mt-2 bg-gray-900 text-gray-100 text-xs font-mono rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{preview}</pre>
          )}
        </div>
      </div>
    </Modal>
  )
}
