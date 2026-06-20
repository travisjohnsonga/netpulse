import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchMe, updateMe, savePreferences, changePassword,
  type Me, type UserPreferences,
} from '../api/client'
import { parseApiErrors } from '../api/errors'
import { useThemeStore, type Theme } from '../store/themeStore'
import { usePreferencesStore } from '../store/preferencesStore'
import { useUnitsStore, type TempUnit } from '../store/unitsStore'

const card = 'bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-5'
const input = 'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'
const btn = 'px-4 py-2 text-sm rounded-lg font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 transition-colors'

const TIME_RANGES = [
  ['15m', 'Last 15 minutes'], ['1h', 'Last 1 hour'], ['4h', 'Last 4 hours'],
  ['12h', 'Last 12 hours'], ['24h', 'Last 24 hours'], ['7d', 'Last 7 days'], ['all', 'All time'],
] as const

const TIMEZONES = ['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Singapore', 'Australia/Sydney']

export default function Profile() {
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { theme, setTheme } = useThemeStore()
  const setStorePrefs = usePreferencesStore((s) => s.set)
  const tempUnit = useUnitsStore((s) => s.unit)
  const setUnitStore = useUnitsStore((s) => s.setUnit)

  useEffect(() => { fetchMe().then(setMe).catch(() => setError('Failed to load profile.')) }, [])

  // Apply instantly (localStorage + UI), then persist to the API in the background.
  const changeTempUnit = (u: TempUnit) => {
    setUnitStore(u)
    patchPrefs({ temperature_unit: u }).catch(() => setError('Save failed.'))
  }

  const patchPrefs = async (patch: Partial<UserPreferences>) => {
    const updated = await savePreferences(patch)
    setMe((m) => (m ? { ...m, preferences: updated } : m))
    setStorePrefs(updated)
    return updated
  }

  if (error && !me) return <div className="text-sm text-red-600 dark:text-red-400">{error}</div>
  if (!me) return <div className="text-sm text-gray-400">Loading…</div>
  const p = me.preferences

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Profile & Preferences</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">Your account, appearance and viewer defaults.</p>
      </div>

      {error && <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300">{error}</div>}

      <AccountSection me={me} onSaved={setMe} onError={setError} />
      <PasswordSection onError={setError} />

      {/* Appearance */}
      <div className={card}>
        <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Appearance</h2>
        <p className={label}>Theme</p>
        <div className="flex gap-2">
          {(['light', 'dark', 'system'] as Theme[]).map((t) => (
            <button
              key={t}
              onClick={() => { setTheme(t); patchPrefs({ theme: t }).catch(() => setError('Failed to save theme.')) }}
              className={clsx(
                'px-4 py-2 text-sm rounded-lg border capitalize transition-colors',
                theme === t
                  ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300'
                  : 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800',
              )}
            >
              {t === 'light' ? '☀️ Light' : t === 'dark' ? '🌙 Dark' : '🖥 System'}
            </button>
          ))}
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">Applies instantly; System follows your OS setting.</p>
      </div>

      {/* Log viewer preferences */}
      <div className={card}>
        <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Log Viewer Preferences</h2>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={label}>Default time range</label>
            <select className={input} value={p.log_default_time_range}
              onChange={(e) => patchPrefs({ log_default_time_range: e.target.value as UserPreferences['log_default_time_range'] }).catch(() => setError('Save failed.'))}>
              {TIME_RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div>
            <label className={label}>Default page size</label>
            <select className={input} value={p.log_default_page_size}
              onChange={(e) => patchPrefs({ log_default_page_size: Number(e.target.value) }).catch(() => setError('Save failed.'))}>
              {[25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 mt-4">
          <input type="checkbox" checked={p.log_auto_refresh}
            onChange={(e) => patchPrefs({ log_auto_refresh: e.target.checked }).catch(() => setError('Save failed.'))} />
          Auto-refresh logs by default
        </label>
      </div>

      {/* Display */}
      <div className={card}>
        <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Display</h2>
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className={label}>Timezone</label>
            <select className={input} value={p.timezone}
              onChange={(e) => patchPrefs({ timezone: e.target.value }).catch(() => setError('Save failed.'))}>
              {TIMEZONES.includes(p.timezone) ? null : <option value={p.timezone}>{p.timezone}</option>}
              {TIMEZONES.map((z) => <option key={z} value={z}>{z}</option>)}
            </select>
          </div>
          <div>
            <label className={label}>Date format</label>
            <div className="flex gap-2 pt-1">
              {([['iso', '2026-05-30'], ['us', '05/30/2026'], ['eu', '30/05/2026']] as const).map(([v, l]) => (
                <button key={v}
                  onClick={() => patchPrefs({ date_format: v }).catch(() => setError('Save failed.'))}
                  className={clsx('px-3 py-2 text-xs rounded-lg border transition-colors',
                    p.date_format === v
                      ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300'
                      : 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800')}>
                  {l}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className={label}>Temperature</label>
            <div className="flex gap-2 pt-1">
              {([['C', 'Celsius (°C)'], ['F', 'Fahrenheit (°F)']] as const).map(([v, l]) => (
                <button key={v}
                  onClick={() => changeTempUnit(v)}
                  className={clsx('px-3 py-2 text-xs rounded-lg border transition-colors',
                    tempUnit === v
                      ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300'
                      : 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800')}>
                  {l}
                </button>
              ))}
            </div>
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 mt-4">
          <input type="checkbox" checked={p.email_alerts}
            onChange={(e) => patchPrefs({ email_alerts: e.target.checked }).catch(() => setError('Save failed.'))} />
          Email me alert notifications
        </label>

        {/* Chat handles — used to DM / @mention you when your alerting team is
            notified via Slack/Discord (set "Notify via" on the team membership). */}
        <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
          <ChatHandle label="Slack member ID" placeholder="U01234ABCDE" value={p.slack_user_id}
            help="Slack → your profile → ⋮ → Copy member ID"
            onSave={(v) => patchPrefs({ slack_user_id: v }).catch(() => setError('Save failed.'))} />
          <ChatHandle label="Discord user ID" placeholder="123456789012345678" value={p.discord_user_id}
            help="Discord → Settings → Advanced → Developer Mode → right-click your name → Copy User ID"
            onSave={(v) => patchPrefs({ discord_user_id: v }).catch(() => setError('Save failed.'))} />
        </div>
      </div>
    </div>
  )
}

function ChatHandle({ label: lbl, value, placeholder, help, onSave }: {
  label: string; value: string; placeholder: string; help: string; onSave: (v: string) => void
}) {
  const [v, setV] = useState(value)
  useEffect(() => { setV(value) }, [value])
  return (
    <div>
      <label className={label}>{lbl}</label>
      <input className={input} value={v} placeholder={placeholder}
        onChange={(e) => setV(e.target.value)}
        onBlur={() => { if (v !== value) onSave(v.trim()) }} />
      <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">{help}</p>
    </div>
  )
}

function AccountSection({ me, onSaved, onError }: { me: Me; onSaved: (m: Me) => void; onError: (e: string | null) => void }) {
  const [email, setEmail] = useState(me.email)
  const [first, setFirst] = useState(me.first_name)
  const [last, setLast] = useState(me.last_name)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const save = async () => {
    setSaving(true); onError(null)
    try {
      const updated = await updateMe({ email, first_name: first, last_name: last })
      onSaved(updated); setSaved(true); setTimeout(() => setSaved(false), 2000)
    } catch (e) { onError(parseApiErrors(e, 'Failed to save account info.')) } finally { setSaving(false) }
  }

  return (
    <div className={card}>
      <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Account Information</h2>
      <div className="grid sm:grid-cols-2 gap-4">
        <div>
          <label className={label}>Username</label>
          <input className={clsx(input, 'opacity-60')} value={me.username} disabled />
        </div>
        <div>
          <label className={label}>Role</label>
          <input className={clsx(input, 'opacity-60 capitalize')} value={me.role} disabled />
        </div>
        <div className="sm:col-span-2">
          <label className={label}>Email</label>
          <input className={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className={label}>First name</label>
          <input className={input} value={first} onChange={(e) => setFirst(e.target.value)} />
        </div>
        <div>
          <label className={label}>Last name</label>
          <input className={input} value={last} onChange={(e) => setLast(e.target.value)} />
        </div>
      </div>
      <button onClick={save} disabled={saving} className={clsx(btn, 'mt-4', saved && '!bg-green-600')}>
        {saving ? 'Saving…' : saved ? 'Saved!' : 'Save Changes'}
      </button>
    </div>
  )
}

function PasswordSection({ onError }: { onError: (e: string | null) => void }) {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const submit = async () => {
    setMsg(null); onError(null)
    if (next !== confirm) { onError('New password and confirmation do not match.'); return }
    setBusy(true)
    try {
      await changePassword(cur, next)
      setMsg('Password updated.'); setCur(''); setNext(''); setConfirm('')
    } catch (e: unknown) {
      onError(parseApiErrors(e, 'Failed to update password.'))
    } finally { setBusy(false) }
  }

  return (
    <div className={card}>
      <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Change Password</h2>
      {msg && <div className="bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-900 rounded-lg px-3 py-2 text-sm text-green-700 dark:text-green-300 mb-3">{msg}</div>}
      <div className="space-y-3">
        <div><label className={label}>Current password</label><input className={input} type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></div>
        <div><label className={label}>New password</label><input className={input} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></div>
        <div><label className={label}>Confirm new password</label><input className={input} type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></div>
      </div>
      <button onClick={submit} disabled={busy || !cur || !next} className={clsx(btn, 'mt-4')}>
        {busy ? 'Updating…' : 'Update Password'}
      </button>
    </div>
  )
}
