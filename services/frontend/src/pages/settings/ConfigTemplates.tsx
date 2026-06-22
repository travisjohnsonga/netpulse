import { useEffect, useMemo, useState } from 'react'
import { SectionHeader } from '../Settings'
import { parseApiErrors } from '../../api/errors'
import {
  fetchConfigTemplates, deleteConfigTemplate,
  CONFIG_TEMPLATE_CATEGORIES,
  type ConfigTemplate, type ConfigTemplateCategory,
} from '../../api/client'
import EditTemplateModal from './ConfigTemplateEdit'
import PushTemplateModal from './ConfigTemplatePush'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

const CATEGORY_LABELS: Record<ConfigTemplateCategory, string> = Object.fromEntries(
  CONFIG_TEMPLATE_CATEGORIES.map((c) => [c.value, c.label]),
) as Record<ConfigTemplateCategory, string>

export default function ConfigTemplates() {
  const [templates, setTemplates] = useState<ConfigTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [catFilter, setCatFilter] = useState<string>('')
  const [platformFilter, setPlatformFilter] = useState<string>('')
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<ConfigTemplate | null>(null)
  const [pushing, setPushing] = useState<ConfigTemplate | null>(null)

  const load = (quiet = false) => {
    if (!quiet) setLoading(true)
    fetchConfigTemplates()
      .then((t) => { setTemplates(t); setError(null) })
      .catch(() => setError('Failed to load config templates.'))
      .finally(() => setLoading(false))
  }
  useEffect(() => load(), [])

  const flash = (msg: string) => { setNotice(msg); setTimeout(() => setNotice(null), 3500) }

  const platforms = useMemo(
    () => Array.from(new Set(templates.map((t) => t.platform).filter(Boolean))).sort(),
    [templates],
  )

  const visible = templates.filter((t) =>
    (!catFilter || t.category === catFilter) &&
    (!platformFilter || t.platform === platformFilter))

  // Group visible templates by category, in the canonical category order.
  const grouped = CONFIG_TEMPLATE_CATEGORIES
    .map((c) => ({ category: c.value, label: c.label, items: visible.filter((t) => t.category === c.value) }))
    .filter((g) => g.items.length > 0)

  const remove = async (t: ConfigTemplate) => {
    if (t.builtin) return
    if (!window.confirm(`Delete template "${t.name}"? This cannot be undone.`)) return
    try { await deleteConfigTemplate(t.id); flash(`Deleted "${t.name}"`); load(true) }
    catch (e) { setError(parseApiErrors(e, 'Failed to delete template.')) }
  }

  return (
    <div>
      <SectionHeader
        title="Configuration Push Templates"
        description="Editable Jinja2 templates for pushing standardized config (SNMP, syslog, NTP…) to devices."
        action={
          <button onClick={() => setCreating(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
            + Add Template
          </button>
        }
      />

      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}
      {notice && <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-lg px-4 py-2 text-sm text-green-700 dark:text-green-300 mb-4">✅ {notice}</div>}

      <div className="flex flex-wrap gap-3 mb-4">
        <select className={`${inputCls} w-auto`} value={catFilter} onChange={(e) => setCatFilter(e.target.value)}>
          <option value="">All Categories</option>
          {CONFIG_TEMPLATE_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
        <select className={`${inputCls} w-auto`} value={platformFilter} onChange={(e) => setPlatformFilter(e.target.value)}>
          <option value="">All Platforms</option>
          {platforms.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
      ) : grouped.length === 0 ? (
        <div className="py-16 text-center text-sm text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          No templates match. Add your first config-push template.
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map((g) => (
            <section key={g.category}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">{g.label}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {g.items.map((t) => (
                  <div key={t.id} className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 flex flex-col">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-900 dark:text-gray-100 truncate">{t.name}</span>
                          {t.builtin && <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">built-in</span>}
                          {!t.enabled && <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400">disabled</span>}
                        </div>
                        {t.description && <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">{t.description}</p>}
                      </div>
                      <span className="shrink-0 inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />{t.platform || 'all'}
                      </span>
                    </div>
                    <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700 flex items-center justify-end gap-1.5">
                      <button onClick={() => setEditing(t)} className="px-2.5 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700">Edit</button>
                      <button onClick={() => setPushing(t)} disabled={!t.enabled} title="Push to devices" className="px-2.5 py-1 text-xs bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-md inline-flex items-center gap-1">▶ Push</button>
                      {!t.builtin && (
                        <button onClick={() => remove(t)} className="px-2.5 py-1 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30">Delete</button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {(creating || editing) && (
        <EditTemplateModal
          template={editing}
          categoryLabels={CATEGORY_LABELS}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSaved={(name) => { setCreating(false); setEditing(null); flash(`Saved "${name}"`); load(true) }}
        />
      )}
      {pushing && (
        <PushTemplateModal
          template={pushing}
          onClose={() => setPushing(null)}
          onPushed={(summary) => { flash(summary) }}
        />
      )}
    </div>
  )
}
