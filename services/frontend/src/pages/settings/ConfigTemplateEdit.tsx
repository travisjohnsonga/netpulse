import { useEffect, useMemo, useState } from 'react'
import Modal from '../../components/Modal'
import { parseApiErrors } from '../../api/errors'
import {
  createConfigTemplate, updateConfigTemplate, previewConfigTemplate,
  fetchDevices, CONFIG_TEMPLATE_CATEGORIES,
  type ConfigTemplate, type ConfigTemplateCategory, type Device,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'
const monoCls =
  'w-full px-3 py-2 text-xs font-mono leading-5 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-gray-900 text-gray-100'

// Common platform slugs (blank = all platforms).
const PLATFORMS = ['', 'aos_cx', 'ios', 'ios_xe', 'ios_xr', 'nxos', 'eos', 'junos', 'fortios', 'panos', 'sonicwall', 'aruba']

const SENSITIVE_RE = /pass|key|secret|token|cred/i
export const isSensitiveName = (n: string) => SENSITIVE_RE.test(n)

const AUTO_VARS = new Set(['device', 'site', 'settings'])

/** Best-effort client-side variable detection (the backend is authoritative). */
export function detectVariables(content: string): string[] {
  const names = new Set<string>()
  // {{ var }} / {{ var | filter }}
  for (const m of content.matchAll(/\{\{\s*([a-zA-Z_][\w]*)\s*(?:\|[^}]*)?\}\}/g)) {
    if (!AUTO_VARS.has(m[1])) names.add(m[1])
  }
  // {% if var is defined %} and similar control blocks
  for (const m of content.matchAll(/\{%[^%]*?\b([a-zA-Z_][\w]*)\b\s+is\s+defined[^%]*?%\}/g)) {
    if (!AUTO_VARS.has(m[1])) names.add(m[1])
  }
  return Array.from(names)
}

export default function EditTemplateModal({ template, onClose, onSaved }: {
  template: ConfigTemplate | null
  categoryLabels: Record<ConfigTemplateCategory, string>
  onClose: () => void
  onSaved: (name: string) => void
}) {
  const [name, setName] = useState(template?.name ?? '')
  const [category, setCategory] = useState<ConfigTemplateCategory>(template?.category ?? 'other')
  const [platform, setPlatform] = useState(template?.platform ?? '')
  const [description, setDescription] = useState(template?.description ?? '')
  const [content, setContent] = useState(template?.template_content ?? '')
  const [enabled, setEnabled] = useState(template?.enabled ?? true)
  const [vars, setVars] = useState<Record<string, string>>(template?.variables ?? {})
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [devices, setDevices] = useState<Device[]>([])
  const [previewDevice, setPreviewDevice] = useState<number | ''>('')
  const [preview, setPreview] = useState<string | null>(null)
  const [previewErr, setPreviewErr] = useState<string | null>(null)
  const [rendering, setRendering] = useState(false)

  useEffect(() => {
    fetchDevices({ page_size: '500' }).then((d) => setDevices(d.results)).catch(() => setDevices([]))
  }, [])

  // Variables shown: those referenced in the content, unioned with stored keys.
  const variableNames = useMemo(() => {
    const detected = detectVariables(content)
    const all = new Set<string>([...detected, ...Object.keys(vars)])
    return Array.from(all).sort()
  }, [content, vars])

  const setVar = (k: string, v: string) => setVars((prev) => ({ ...prev, [k]: v }))

  const runPreview = async () => {
    if (!template || previewDevice === '') return
    setRendering(true); setPreviewErr(null); setPreview(null)
    try {
      const res = await previewConfigTemplate(template.id, Number(previewDevice), vars, content)
      setPreview(res.rendered)
    } catch (e) {
      setPreviewErr(parseApiErrors(e, 'Render failed — check the Jinja2 syntax and variables.'))
    } finally {
      setRendering(false)
    }
  }

  const save = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    if (!content.trim()) { setErr('Template content is required.'); return }
    setSaving(true); setErr(null)
    // Only send variables that still exist as fields.
    const payloadVars: Record<string, string> = {}
    for (const n of variableNames) if (vars[n]) payloadVars[n] = vars[n]
    const payload = {
      name: name.trim(), category, platform: platform.trim(),
      description: description.trim(), template_content: content,
      variables: payloadVars, enabled,
    }
    try {
      if (template) await updateConfigTemplate(template.id, payload)
      else await createConfigTemplate(payload)
      onSaved(name.trim())
    } catch (e) {
      setErr(parseApiErrors(e, 'Failed to save template.'))
      setSaving(false)
    }
  }

  return (
    <Modal
      title={template ? `Edit Template: ${template.name}` : 'Add Template'}
      onClose={onClose}
      size="xl"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save Template'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}

        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Name</label>
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. AOS-CX SNMP v3" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Category</label>
            <select className={inputCls} value={category} onChange={(e) => setCategory(e.target.value as ConfigTemplateCategory)}>
              {CONFIG_TEMPLATE_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Platform</label>
            <select className={inputCls} value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {PLATFORMS.map((p) => <option key={p} value={p}>{p || 'all platforms'}</option>)}
            </select>
          </div>
          <div className="col-span-2">
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Description</label>
            <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="optional" />
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
            Template (Jinja2) — variables: <code className="text-[11px]">{'{{ device }}'}</code>, <code className="text-[11px]">{'{{ settings }}'}</code>, and any you define
          </label>
          <textarea className={monoCls} rows={7} value={content} onChange={(e) => setContent(e.target.value)} spellCheck={false}
            placeholder={'logging {{ syslog_server }} severity {{ syslog_severity | default(\'informational\') }}'} />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">Default Variables</label>
          {variableNames.length === 0 ? (
            <p className="text-xs text-gray-400 dark:text-gray-500">No variables referenced yet — use <code>{'{{ name }}'}</code> in the template.</p>
          ) : (
            <div className="space-y-2">
              {variableNames.map((n) => {
                const sensitive = isSensitiveName(n)
                return (
                  <div key={n} className="flex items-center gap-2">
                    <label className="w-40 shrink-0 text-xs font-mono text-gray-600 dark:text-gray-300 flex items-center gap-1">
                      {n}{sensitive && <span title="sensitive — stored securely, never returned">🔒</span>}
                    </label>
                    <input
                      className={inputCls}
                      type={sensitive ? 'password' : 'text'}
                      autoComplete="new-password"
                      value={vars[n] ?? ''}
                      onChange={(e) => setVar(n, e.target.value)}
                      placeholder={sensitive ? 'stored in OpenBao — leave blank to keep' : 'default value'}
                    />
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Preview</label>
          {!template ? (
            <p className="text-xs text-gray-400 dark:text-gray-500">Save the template first to render a preview on a device.</p>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-2">
                <select className={`${inputCls} w-auto`} value={previewDevice} onChange={(e) => setPreviewDevice(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">Select device…</option>
                  {devices.map((d) => <option key={d.id} value={d.id}>{d.display_hostname || d.hostname}</option>)}
                </select>
                <button onClick={runPreview} disabled={previewDevice === '' || rendering} className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">
                  {rendering ? 'Rendering…' : 'Render'}
                </button>
              </div>
              {previewErr && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-xs text-red-700 dark:text-red-400">{previewErr}</div>}
              {preview != null && (
                <pre className="bg-gray-900 text-gray-100 text-xs font-mono rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{preview || '(empty)'}</pre>
              )}
            </>
          )}
        </div>

        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled (available for pushing)
        </label>
      </div>
    </Modal>
  )
}
