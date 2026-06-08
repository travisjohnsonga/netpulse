import { useEffect, useState } from 'react'
import { SectionHeader } from '../Settings'
import { fetchAuditRetention, saveAuditRetention } from '../../api/client'

const inputCls =
  'w-28 px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

export default function DataRetention() {
  const [auditDays, setAuditDays] = useState<number | ''>('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { fetchAuditRetention().then(setAuditDays).catch(() => setAuditDays(90)) }, [])

  const save = async () => {
    if (auditDays === '' || Number(auditDays) < 0) { setErr('Enter a number of days (0 = keep forever).'); return }
    setSaving(true); setErr(null); setMsg(null)
    try {
      const v = await saveAuditRetention(Number(auditDays))
      setAuditDays(v); setMsg('Saved.')
    } catch {
      setErr('Failed to save retention setting.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <SectionHeader title="Data Retention" description="How long NetPulse keeps historical records before pruning them." />
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-5 max-w-xl space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Audit Log retention</label>
          <div className="flex items-center gap-2">
            <input type="number" min={0} max={3650} className={inputCls}
              value={auditDays} onChange={(e) => setAuditDays(e.target.value === '' ? '' : Number(e.target.value))} />
            <span className="text-sm text-gray-500 dark:text-gray-400">days</span>
          </div>
          <p className="text-xs text-gray-400 mt-1">Audit-log rows older than this are pruned daily. Set to 0 to keep them indefinitely. Default 90.</p>
        </div>
        <div className="flex items-center gap-3">
          <button onClick={save} disabled={saving}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
            {saving ? 'Saving…' : 'Save'}
          </button>
          {msg && <span className="text-sm text-green-600 dark:text-green-400">{msg}</span>}
          {err && <span className="text-sm text-red-600 dark:text-red-400">{err}</span>}
        </div>
      </div>
    </div>
  )
}
