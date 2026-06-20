import { useEffect, useState } from 'react'
import Modal from '../../components/Modal'
import { SectionHeader } from '../Settings'
import { useComplianceRunAll } from '../../hooks/useComplianceRun'
import {
  fetchComplianceTemplates, createComplianceTemplate, updateComplianceTemplate,
  deleteComplianceTemplate, previewComplianceTemplate,
  fetchDevicePlatforms, fetchDeviceRoles, fetchSites, fetchDevices,
  type ComplianceTemplate, type ComplianceTemplatePayload,
  type PlatformOption, type DeviceRole, type Site, type Device,
} from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

export default function ComplianceTemplates() {
  const [templates, setTemplates] = useState<ComplianceTemplate[]>([])
  const [platforms, setPlatforms] = useState<PlatformOption[]>([])
  const [roles, setRoles] = useState<DeviceRole[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<ComplianceTemplate | null>(null)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<ComplianceTemplate | null>(null)
  const [runErr, setRunErr] = useState<string | null>(null)
  const { status: runStatus, start: startRun, starting, isRunning } = useComplianceRunAll()

  const load = () => {
    setLoading(true)
    Promise.all([
      fetchComplianceTemplates(), fetchDevicePlatforms(), fetchDeviceRoles(),
      fetchSites(), fetchDevices({ page_size: '500' }),
    ])
      .then(([t, p, r, s, d]) => {
        setTemplates(t); setPlatforms(p); setRoles(r); setSites(s)
        setDevices(d.results); setError(null)
      })
      .catch(() => setError('Failed to load compliance templates.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const toggleEnabled = async (t: ComplianceTemplate) => {
    setTemplates((prev) => prev.map((x) => x.id === t.id ? { ...x, enabled: !x.enabled } : x))
    try {
      await updateComplianceTemplate(t.id, { enabled: !t.enabled })
    } catch {
      setTemplates((prev) => prev.map((x) => x.id === t.id ? { ...x, enabled: t.enabled } : x))
    }
  }

  const runAll = async () => {
    setRunErr(null)
    try {
      await startRun()
    } catch {
      setRunErr('Failed to start the compliance run.')
    }
  }

  const scopeLabel = (t: ComplianceTemplate) => {
    const parts: string[] = []
    if (t.role_name) parts.push(`Role: ${t.role_name}`)
    if (t.platform) parts.push(`Platform: ${t.platform}`)
    if (t.site_name) parts.push(`Site: ${t.site_name}`)
    return parts.length ? parts.join(' · ') : 'All devices'
  }

  return (
    <div>
      <SectionHeader
        title="Compliance Templates"
        description="Jinja2 templates of expected configuration, scoped by role / platform / site. spane diffs each device's running config against the rendered template and flags MISSING / DRIFT / EXTRA lines."
        action={
          <div className="flex gap-2">
            <button onClick={runAll} disabled={starting || isRunning}
              className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50">
              {starting || isRunning ? 'Running…' : '▶ Run Compliance Check — All Devices'}
            </button>
            <button onClick={() => setCreating(true)}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
              + Add Template
            </button>
          </div>
        }
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}
      {runErr && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4">{runErr}</div>}
      {runStatus && (runStatus.running || runStatus.done > 0) && (
        <div className="bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 rounded-lg px-3 py-2 text-sm text-blue-700 dark:text-blue-300 mb-4 flex items-center gap-2">
          {runStatus.running ? (
            <>
              <span className="w-3.5 h-3.5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              Running compliance… {runStatus.done}/{runStatus.total} devices
            </>
          ) : (
            <>✅ Complete: {runStatus.success} passed, {runStatus.failed} failed</>
          )}
        </div>
      )}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
        ) : templates.length === 0 ? (
          <div className="py-16 text-center text-sm text-gray-500 dark:text-gray-400">
            No compliance templates yet. Add a template to define expected config for a role, platform, or site.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-5 py-3 font-medium">Scope</th>
                <th className="px-5 py-3 font-medium">Enabled</th>
                <th className="px-5 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {templates.map((t) => (
                <tr key={t.id} className={t.enabled ? '' : 'opacity-50'}>
                  <td className="px-5 py-3">
                    <div className="text-gray-900 dark:text-gray-100 font-medium">{t.name}</div>
                    {t.description && <div className="text-xs text-gray-500 dark:text-gray-400">{t.description}</div>}
                  </td>
                  <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{scopeLabel(t)}</td>
                  <td className="px-5 py-3">
                    <button onClick={() => toggleEnabled(t)} title={t.enabled ? 'Disable' : 'Enable'}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${t.enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'}`}>
                      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${t.enabled ? 'translate-x-5' : 'translate-x-1'}`} />
                    </button>
                  </td>
                  <td className="px-5 py-3 text-right whitespace-nowrap">
                    <button onClick={() => setEditing(t)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700 mr-1">Edit</button>
                    <button onClick={() => setDeleting(t)} className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {(creating || editing) && (
        <TemplateModal
          template={editing}
          platforms={platforms}
          roles={roles}
          sites={sites}
          devices={devices}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSaved={() => { setCreating(false); setEditing(null); load() }}
        />
      )}
      {deleting && (
        <DeleteTemplateModal
          template={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => { setDeleting(null); load() }}
        />
      )}
    </div>
  )
}

function TemplateModal({ template, platforms, roles, sites, devices, onClose, onSaved }: {
  template: ComplianceTemplate | null
  platforms: PlatformOption[]
  roles: DeviceRole[]
  sites: Site[]
  devices: Device[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(template?.name ?? '')
  const [description, setDescription] = useState(template?.description ?? '')
  const [platform, setPlatform] = useState(template?.platform ?? '')
  const [role, setRole] = useState<number | ''>(template?.role ?? '')
  const [site, setSite] = useState<number | ''>(template?.site ?? '')
  const [content, setContent] = useState(template?.template_content ?? '')
  const [variablesText, setVariablesText] = useState(
    JSON.stringify(template?.variables ?? {}, null, 2))
  const [enabled, setEnabled] = useState(template?.enabled ?? true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // Preview
  const [previewDevice, setPreviewDevice] = useState<number | ''>('')
  const [rendered, setRendered] = useState<string | null>(null)
  const [previewErr, setPreviewErr] = useState<string | null>(null)
  const [previewing, setPreviewing] = useState(false)

  const parseVariables = (): Record<string, unknown> | null => {
    if (!variablesText.trim()) return {}
    try { return JSON.parse(variablesText) } catch { return null }
  }

  const runPreview = async () => {
    if (!template || previewDevice === '') return
    setPreviewing(true); setPreviewErr(null); setRendered(null)
    try {
      const res = await previewComplianceTemplate(template.id, previewDevice as number)
      if ('error' in res) setPreviewErr(res.error)
      else setRendered(res.rendered)
    } catch {
      setPreviewErr('Preview request failed.')
    } finally {
      setPreviewing(false)
    }
  }

  const save = async () => {
    if (!name.trim()) { setErr('Name is required.'); return }
    if (!content.trim()) { setErr('Template content is required.'); return }
    const variables = parseVariables()
    if (variables === null) { setErr('Variables must be valid JSON.'); return }
    setSaving(true); setErr(null)
    try {
      const payload: ComplianceTemplatePayload = {
        name: name.trim(), description: description.trim(),
        platform, role: role === '' ? null : role, site: site === '' ? null : site,
        template_content: content, variables, enabled,
      }
      if (template) await updateComplianceTemplate(template.id, payload)
      else await createComplianceTemplate(payload)
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: unknown } })?.response?.data
      setErr(typeof detail === 'object' ? JSON.stringify(detail) : 'Failed to save template.')
      setSaving(false)
    }
  }

  return (
    <Modal
      title={template ? `Edit Template: ${template.name}` : 'Add Compliance Template'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{saving ? 'Saving…' : 'Save'}</button>
        </>
      }
    >
      <div className="space-y-4">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Name</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. NTP Policy" />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Description</label>
          <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional" />
        </div>

        {/* Scope */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Platform</label>
            <select className={inputCls} value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="">All</option>
              {platforms.map((p) => <option key={p.value} value={p.value}>{p.value}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Role</label>
            <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value ? Number(e.target.value) : '')}>
              <option value="">Any</option>
              {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Site</label>
            <select className={inputCls} value={site} onChange={(e) => setSite(e.target.value ? Number(e.target.value) : '')}>
              <option value="">Any</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
            Template content <span className="text-gray-400">(Jinja2 — expected config lines)</span>
          </label>
          <textarea className={`${inputCls} font-mono`} rows={6} value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={'ntp server {{ ntp_server_1 }}\nntp server {{ ntp_server_2 }}\nhostname {{ device.hostname }}'} />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
            Default variables <span className="text-gray-400">(JSON — overridable per device)</span>
          </label>
          <textarea className={`${inputCls} font-mono`} rows={4} value={variablesText}
            onChange={(e) => setVariablesText(e.target.value)}
            placeholder={'{\n  "ntp_server_1": "10.0.0.1"\n}'} />
          <p className="text-xs text-gray-400 mt-1">A <code>device</code> context (hostname, ip, platform, site, role) is always available.</p>
        </div>

        {/* Preview — only for saved templates */}
        {template && (
          <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3">
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Preview against a device</label>
            <div className="flex gap-2">
              <select className={inputCls} value={previewDevice} onChange={(e) => setPreviewDevice(e.target.value ? Number(e.target.value) : '')}>
                <option value="">Select device…</option>
                {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname}</option>)}
              </select>
              <button onClick={runPreview} disabled={previewing || previewDevice === ''}
                className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-white dark:hover:bg-gray-800 disabled:opacity-50 text-gray-700 dark:text-gray-300 whitespace-nowrap">
                {previewing ? 'Rendering…' : 'Preview'}
              </button>
            </div>
            {previewErr && <div className="mt-2 text-sm text-amber-600 dark:text-amber-400">⚠️ {previewErr}</div>}
            {rendered !== null && (
              <pre className="mt-2 text-xs font-mono bg-white dark:bg-gray-950 border border-gray-200 dark:border-gray-700 rounded p-3 overflow-x-auto text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{rendered || '(empty)'}</pre>
            )}
          </div>
        )}

        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="rounded" />
          Enabled
        </label>
      </div>
    </Modal>
  )
}

function DeleteTemplateModal({ template, onClose, onDeleted }: {
  template: ComplianceTemplate
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const remove = async () => {
    setDeleting(true); setErr(null)
    try { await deleteComplianceTemplate(template.id); onDeleted() }
    catch { setErr('Failed to delete template.'); setDeleting(false) }
  }

  return (
    <Modal
      title="Delete Template"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="flex-1 py-2.5 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50">Cancel</button>
          <button onClick={remove} disabled={deleting} className="flex-1 py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">{deleting ? 'Deleting…' : 'Delete'}</button>
        </>
      }
    >
      <div className="space-y-3">
        {err && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400">{err}</div>}
        <p className="text-sm text-gray-700 dark:text-gray-300">
          Delete the template <strong>{template.name}</strong>? Existing results will also be removed. This cannot be undone.
        </p>
      </div>
    </Modal>
  )
}
