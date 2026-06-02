import { useEffect, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import {
  fetchTeams, saveTeam, deleteTeam, testTeamDiscord, fetchPolicies, savePolicy,
  fetchRoutes, saveRoute, deleteRoute, testRoute,
  fetchMaintenanceWindows, saveMaintenanceWindow, deleteMaintenanceWindow, endMaintenanceNow, fetchDevices,
  fetchTeamMembers, addTeamMember, updateTeamMember, removeTeamMember, fetchUsers,
  type AlertTeam, type EscalationPolicy, type AlertRoute, type TeamMember, type AdminUser,
  type MaintenanceWindow, type MaintenanceWindowPayload, type Device,
} from '../../api/client'

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info']
const SOURCES = ['check_engine', 'snmp', 'reachability', 'gnmi']

const input = 'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'
const label = 'block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1'
const card = 'bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700'

export default function AlertRouting() {
  const [teams, setTeams] = useState<AlertTeam[]>([])
  const [policies, setPolicies] = useState<EscalationPolicy[]>([])
  const [routes, setRoutes] = useState<AlertRoute[]>([])
  const [windows, setWindows] = useState<MaintenanceWindow[]>([])
  const [modal, setModal] = useState<null | 'team' | 'policy' | 'route' | 'maint'>(null)
  const [membersTeam, setMembersTeam] = useState<AlertTeam | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = () => {
    fetchTeams().then(setTeams).catch(() => setErr('Could not load alert routing.'))
    fetchPolicies().then(setPolicies).catch(() => {})
    fetchRoutes().then(setRoutes).catch(() => {})
    fetchMaintenanceWindows().then(setWindows).catch(() => {})
  }
  useEffect(load, [])

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">Alert Routing</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          Route alerts to teams via escalation policies (Stage 1: email notifications).
        </p>
      </div>
      {err && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{err}</div>}

      {/* Teams */}
      <Section title="Teams" onAdd={() => setModal('team')}>
        {teams.length === 0 ? <Empty text="No teams yet." /> : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-700">
            {teams.map((t) => (
              <li key={t.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
                <span className="w-3 h-3 rounded-full" style={{ background: t.color }} />
                <span className="font-medium text-gray-800 dark:text-gray-100">{t.name}</span>
                <span className="text-gray-500 dark:text-gray-400">{t.member_count} member{t.member_count === 1 ? '' : 's'}</span>
                {t.discord_webhook_url && <span title="Discord webhook configured">💬</span>}
                <span className="ml-auto flex gap-3">
                  <button onClick={() => setMembersTeam(t)} className="text-xs text-blue-600 hover:text-blue-800">Members</button>
                  {t.discord_webhook_url && (
                    <button onClick={() => testTeamDiscord(t.id).then((r) => alert(r.ok ? 'Discord test sent ✅' : `Failed: ${r.error}`))}
                      className="text-xs text-indigo-600 hover:text-indigo-800">Test Discord</button>
                  )}
                  <button onClick={() => deleteTeam(t.id).then(load)} className="text-xs text-red-600 hover:text-red-800">Delete</button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Escalation policies */}
      <Section title="Escalation Policies" onAdd={() => setModal('policy')} addDisabled={teams.length === 0}>
        {policies.length === 0 ? <Empty text="No policies yet." /> : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-700">
            {policies.map((p) => (
              <li key={p.id} className="px-5 py-2.5 text-sm">
                <span className="font-medium text-gray-800 dark:text-gray-100">{p.name}</span>
                <span className="ml-2 text-gray-500 dark:text-gray-400">{p.steps.length} step{p.steps.length === 1 ? '' : 's'}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Routes */}
      <Section title="Alert Routes" onAdd={() => setModal('route')} addDisabled={policies.length === 0}>
        {routes.length === 0 ? <Empty text="No routes yet. Routes match alerts to an escalation policy." /> : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                <th className="px-5 py-2.5 font-medium">#</th>
                <th className="px-5 py-2.5 font-medium">Name</th>
                <th className="px-5 py-2.5 font-medium">Matches</th>
                <th className="px-5 py-2.5 font-medium">Policy</th>
                <th className="px-5 py-2.5 font-medium text-right"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {routes.map((r) => (
                <tr key={r.id} className={clsx(!r.is_active && 'opacity-50')}>
                  <td className="px-5 py-2.5 text-gray-500">{r.priority}</td>
                  <td className="px-5 py-2.5 font-medium text-gray-800 dark:text-gray-100">{r.name}</td>
                  <td className="px-5 py-2.5 text-gray-500 dark:text-gray-400 text-xs">{matchSummary(r)}</td>
                  <td className="px-5 py-2.5 text-gray-600 dark:text-gray-300">{r.policy_name}</td>
                  <td className="px-5 py-2.5 text-right">
                    <button onClick={() => deleteRoute(r.id).then(load)} className="text-xs text-red-600 hover:text-red-800">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <RouteTester />

      {/* Maintenance windows */}
      <Section title="Maintenance Windows" onAdd={() => setModal('maint')}>
        {windows.length === 0 ? <Empty text="No maintenance windows. Schedule one to suppress alerts during maintenance." /> : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-700">
            {windows.map((w) => {
              const scope = w.device_names.length ? w.device_names.join(', ')
                : w.site_names.length ? `sites: ${w.site_names.join(', ')}` : 'all devices'
              return (
                <li key={w.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
                  <span>{w.is_currently_active ? '🔵' : new Date(w.start_time) > new Date() ? '⏰' : '✅'}</span>
                  <div>
                    <span className="font-medium text-gray-800 dark:text-gray-100">{w.name}</span>
                    <span className="block text-xs text-gray-500 dark:text-gray-400">
                      {new Date(w.start_time).toLocaleString()} → {new Date(w.end_time).toLocaleString()} · {scope}
                      {w.recurrence !== 'none' ? ` · ${w.recurrence}` : ''}
                    </span>
                  </div>
                  <span className="ml-auto flex gap-3">
                    {w.is_currently_active && (
                      <button onClick={() => endMaintenanceNow(w.id).then(load)} className="text-xs text-amber-600 hover:text-amber-800">End now</button>
                    )}
                    <button onClick={() => deleteMaintenanceWindow(w.id).then(load)} className="text-xs text-red-600 hover:text-red-800">Delete</button>
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </Section>

      {modal === 'team' && <TeamModal onClose={() => setModal(null)} onSaved={() => { setModal(null); load() }} />}
      {modal === 'policy' && <PolicyModal teams={teams} onClose={() => setModal(null)} onSaved={() => { setModal(null); load() }} />}
      {modal === 'route' && <RouteModal policies={policies} onClose={() => setModal(null)} onSaved={() => { setModal(null); load() }} />}
      {modal === 'maint' && <MaintenanceModal onClose={() => setModal(null)} onSaved={() => { setModal(null); load() }} />}
      {membersTeam && <MembersModal team={membersTeam} onClose={() => { setMembersTeam(null); load() }} />}
    </div>
  )
}

const ROLES: TeamMember['role'][] = ['member', 'lead', 'manager']

function MembersModal({ team, onClose }: { team: AlertTeam; onClose: () => void }) {
  const [members, setMembers] = useState<TeamMember[]>([])
  const [users, setUsers] = useState<AdminUser[]>([])
  const [search, setSearch] = useState('')
  const [addUserId, setAddUserId] = useState<number | ''>('')
  const [addRole, setAddRole] = useState<TeamMember['role']>('member')
  const [busy, setBusy] = useState(false)

  const reload = () => fetchTeamMembers(team.id).then(setMembers).catch(() => setMembers([]))
  useEffect(() => {
    reload()
    fetchUsers().then(setUsers).catch(() => setUsers([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.id])

  const memberUserIds = new Set(members.map((m) => m.user))
  const candidates = users
    .filter((u) => !memberUserIds.has(u.id))
    .filter((u) => {
      const q = search.toLowerCase()
      return !q || u.username.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)
        || `${u.first_name} ${u.last_name}`.toLowerCase().includes(q)
    })

  const add = async () => {
    if (!addUserId) return
    setBusy(true)
    try { await addTeamMember(team.id, { user: addUserId, role: addRole }); setAddUserId(''); setSearch(''); await reload() }
    finally { setBusy(false) }
  }
  const patch = async (m: TeamMember, p: Partial<TeamMember>) => {
    setMembers((prev) => prev.map((x) => (x.id === m.id ? { ...x, ...p } : x)))  // optimistic
    try { await updateTeamMember(team.id, m.user, p) } catch { reload() }
  }
  const remove = async (m: TeamMember) => { await removeTeamMember(team.id, m.user); reload() }

  const Toggle = ({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) => (
    <button onClick={onClick}
      className={clsx('px-2 py-0.5 text-xs rounded-md border',
        on ? 'bg-blue-50 dark:bg-blue-900/30 border-blue-300 dark:border-blue-700 text-blue-700 dark:text-blue-300'
           : 'border-gray-200 dark:border-gray-700 text-gray-400')}>{children}</button>
  )

  return (
    <Modal title={`Members — ${team.name}`} onClose={onClose} footer={
      <button onClick={onClose} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-lg">Done</button>
    }>
      <div className="space-y-3">
        {members.length === 0 && <p className="text-sm text-gray-400">No members yet.</p>}
        {members.map((m) => (
          <div key={m.id} className="border border-gray-100 dark:border-gray-700 rounded-lg p-3">
            <div className="flex items-center gap-2">
              <span className="flex-1 min-w-0 truncate text-sm text-gray-800 dark:text-gray-100">
                👤 {m.full_name || m.username} <span className="text-gray-400">({m.email || 'no email'})</span>
              </span>
              <select className="text-xs border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-800 px-1 py-0.5"
                value={m.role} onChange={(e) => patch(m, { role: e.target.value as TeamMember['role'] })}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              <button onClick={() => remove(m)} className="text-xs text-red-600 hover:text-red-800">Remove</button>
            </div>
            <div className="flex gap-1.5 mt-2">
              <span className="text-xs text-gray-400 mr-1">Notify via:</span>
              <Toggle on={m.notify_email} onClick={() => patch(m, { notify_email: !m.notify_email })}>✉ Email</Toggle>
              <Toggle on={m.notify_slack} onClick={() => patch(m, { notify_slack: !m.notify_slack })}>💬 Slack</Toggle>
              <Toggle on={m.notify_discord} onClick={() => patch(m, { notify_discord: !m.notify_discord })}>🎮 Discord</Toggle>
            </div>
          </div>
        ))}

        <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
          <label className={label}>Add member</label>
          <input className={clsx(input, 'mb-2')} placeholder="Search users by name or email…"
            value={search} onChange={(e) => setSearch(e.target.value)} />
          <div className="flex gap-2">
            <select className={input} value={addUserId} onChange={(e) => setAddUserId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">— Select user —</option>
              {candidates.slice(0, 50).map((u) => (
                <option key={u.id} value={u.id}>{u.username}{u.email ? ` (${u.email})` : ''}</option>
              ))}
            </select>
            <select className="text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 px-2"
              value={addRole} onChange={(e) => setAddRole(e.target.value as TeamMember['role'])}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <button onClick={add} disabled={busy || !addUserId}
              className="px-3 py-2 text-sm bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-lg whitespace-nowrap">Add</button>
          </div>
        </div>

        <div className="border-t border-gray-200 dark:border-gray-700 pt-3 text-xs text-gray-500 dark:text-gray-400">
          Team channels: {team.slack_webhook_url ? 'Slack ✓' : 'Slack —'} · {team.discord_webhook_url ? 'Discord ✓' : 'Discord —'}.
          Slack/Discord per-member alerts also need the user's handle set in their profile.
        </div>
      </div>
    </Modal>
  )
}

function matchSummary(r: AlertRoute): string {
  const parts: string[] = []
  if (r.match_severity.length) parts.push(r.match_severity.join('/'))
  if (r.match_source.length) parts.push(r.match_source.join('/'))
  if (r.match_check_types.length) parts.push(r.match_check_types.join('/'))
  return parts.length ? parts.join(' · ') : 'everything'
}

function Section({ title, onAdd, addDisabled, children }: { title: string; onAdd: () => void; addDisabled?: boolean; children: React.ReactNode }) {
  return (
    <div className={card}>
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 dark:border-gray-700">
        <h3 className="font-semibold text-gray-800 dark:text-gray-200">{title}</h3>
        <button onClick={onAdd} disabled={addDisabled}
          className="px-3 py-1.5 text-xs font-medium bg-blue-600 hover:bg-blue-700 text-white rounded-md disabled:opacity-40">+ Add</button>
      </div>
      {children}
    </div>
  )
}
function Empty({ text }: { text: string }) {
  return <p className="px-5 py-6 text-sm text-gray-400 dark:text-gray-500 text-center">{text}</p>
}

function TeamModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [color, setColor] = useState('#ef4444')
  const [slack, setSlack] = useState('')
  const [discord, setDiscord] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <Modal title="Add Team" onClose={onClose} footer={
      <>
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm">Cancel</button>
        <button disabled={busy || !name.trim()}
          onClick={() => { setBusy(true); saveTeam({ name, color, slack_webhook_url: slack, discord_webhook_url: discord }).then(onSaved).finally(() => setBusy(false)) }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm disabled:opacity-50">Save</button>
      </>
    }>
      <div className="space-y-4">
        <div><label className={label}>Name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Network Ops" /></div>
        <div><label className={label}>Colour</label><input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-9 w-16 rounded border border-gray-300 dark:border-gray-600" /></div>
        <div className="border-t border-gray-100 dark:border-gray-700 pt-3">
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">Notification channels</p>
          <div><label className={label}>Slack webhook URL <span className="text-gray-400">(optional)</span></label>
            <input className={input} value={slack} onChange={(e) => setSlack(e.target.value)} placeholder="https://hooks.slack.com/services/..." /></div>
          <div className="mt-3"><label className={label}>Discord webhook URL <span className="text-gray-400">(optional)</span></label>
            <input className={input} value={discord} onChange={(e) => setDiscord(e.target.value)} placeholder="https://discord.com/api/webhooks/..." />
            <p className="text-[11px] text-gray-400 mt-1">Discord: Server Settings → Integrations → Webhooks → New Webhook</p></div>
        </div>
      </div>
    </Modal>
  )
}

function PolicyModal({ teams, onClose, onSaved }: { teams: AlertTeam[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [team, setTeam] = useState(teams[0]?.id ?? 0)
  const [busy, setBusy] = useState(false)
  return (
    <Modal title="Add Escalation Policy" onClose={onClose} footer={
      <>
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm">Cancel</button>
        <button disabled={busy || !name.trim() || !team} onClick={() => { setBusy(true); savePolicy({ name, team }).then(onSaved).finally(() => setBusy(false)) }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm disabled:opacity-50">Save</button>
      </>
    }>
      <div className="space-y-4">
        <div><label className={label}>Name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Network Critical" /></div>
        <div><label className={label}>Team</label>
          <select className={input} value={team} onChange={(e) => setTeam(Number(e.target.value))}>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
        <p className="text-xs text-gray-400">Steps are added via the API in Stage 1; the visual builder lands in a later stage.</p>
      </div>
    </Modal>
  )
}

function MultiCheck({ options, value, onChange }: { options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  const toggle = (o: string) => onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o])
  return (
    <div className="flex flex-wrap gap-3 text-sm">
      {options.map((o) => (
        <label key={o} className="inline-flex items-center gap-1.5 text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={value.includes(o)} onChange={() => toggle(o)} />{o}
        </label>
      ))}
    </div>
  )
}

function RouteModal({ policies, onClose, onSaved }: { policies: EscalationPolicy[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [policy, setPolicy] = useState(policies[0]?.id ?? 0)
  const [priority, setPriority] = useState(100)
  const [severity, setSeverity] = useState<string[]>([])
  const [source, setSource] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  return (
    <Modal title="Add Alert Route" size="lg" onClose={onClose} footer={
      <>
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm">Cancel</button>
        <button disabled={busy || !name.trim() || !policy}
          onClick={() => { setBusy(true); saveRoute({ name, escalation_policy: policy, priority, match_severity: severity, match_source: source }).then(onSaved).finally(() => setBusy(false)) }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm disabled:opacity-50">Save</button>
      </>
    }>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div><label className={label}>Name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Critical Network" /></div>
          <div><label className={label}>Priority <span className="text-gray-400">(lower first)</span></label><input type="number" className={input} value={priority} onChange={(e) => setPriority(Number(e.target.value))} /></div>
        </div>
        <div><label className={label}>Escalation policy</label>
          <select className={input} value={policy} onChange={(e) => setPolicy(Number(e.target.value))}>
            {policies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div><label className={label}>Match severity <span className="text-gray-400">(none = all)</span></label><MultiCheck options={SEVERITIES} value={severity} onChange={setSeverity} /></div>
        <div><label className={label}>Match source <span className="text-gray-400">(none = all)</span></label><MultiCheck options={SOURCES} value={source} onChange={setSource} /></div>
      </div>
    </Modal>
  )
}

function RouteTester() {
  const [severity, setSeverity] = useState('high')
  const [source, setSource] = useState('')
  const [result, setResult] = useState<{ matched: boolean; name?: string } | null>(null)
  const run = () => testRoute({ severity, source: source || undefined }).then((r) => setResult({ matched: r.matched, name: r.route?.name }))
  return (
    <div className={clsx(card, 'p-4')}>
      <h3 className="font-semibold text-gray-800 dark:text-gray-200 mb-2">Test routing</h3>
      <div className="flex flex-wrap items-end gap-3">
        <div><label className={label}>Severity</label>
          <select className={input} value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
        <div><label className={label}>Source <span className="text-gray-400">(optional)</span></label>
          <select className={input} value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">—</option>
            {SOURCES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
        <button onClick={run} className="px-4 py-2 bg-gray-800 dark:bg-gray-700 text-white rounded-lg text-sm">Test</button>
        {result && (
          <span className="text-sm">
            {result.matched
              ? <span className="text-green-600 dark:text-green-400">→ matched <strong>{result.name}</strong></span>
              : <span className="text-gray-500">→ no route matched</span>}
          </span>
        )}
      </div>
    </div>
  )
}

function toLocalInput(d: Date): string {
  // datetime-local value (local time, no seconds/zone).
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function MaintenanceModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const now = new Date()
  const [name, setName] = useState('')
  const [start, setStart] = useState(toLocalInput(now))
  const [end, setEnd] = useState(toLocalInput(new Date(now.getTime() + 2 * 3600_000)))
  const [recurrence, setRecurrence] = useState<'none' | 'daily' | 'weekly' | 'monthly'>('none')
  const [scopeAll, setScopeAll] = useState(true)
  const [deviceIds, setDeviceIds] = useState<number[]>([])
  const [severity, setSeverity] = useState<string[]>([])
  const [devices, setDevices] = useState<Device[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { fetchDevices({ page_size: '500' }).then((r) => setDevices(r.results)).catch(() => {}) }, [])

  const submit = async () => {
    setErr(null)
    if (!name.trim()) { setErr('Name is required.'); return }
    setBusy(true)
    try {
      const payload: MaintenanceWindowPayload = {
        name,
        start_time: new Date(start).toISOString(),
        end_time: new Date(end).toISOString(),
        recurrence,
        severity_filter: severity,
        devices: scopeAll ? [] : deviceIds,
      }
      await saveMaintenanceWindow(payload)
      onSaved()
    } catch { setErr('Could not save the maintenance window.') } finally { setBusy(false) }
  }

  return (
    <Modal title="Schedule Maintenance" size="lg" onClose={onClose} footer={
      <>
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm">Cancel</button>
        <button disabled={busy || !name.trim()} onClick={submit} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm disabled:opacity-50">Schedule</button>
      </>
    }>
      <div className="space-y-4">
        {err && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{err}</div>}
        <div><label className={label}>Name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Router1 IOS Upgrade" /></div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className={label}>Start</label><input type="datetime-local" className={input} value={start} onChange={(e) => setStart(e.target.value)} /></div>
          <div><label className={label}>End</label><input type="datetime-local" className={input} value={end} onChange={(e) => setEnd(e.target.value)} /></div>
        </div>
        <div><label className={label}>Recurrence</label>
          <select className={input} value={recurrence} onChange={(e) => setRecurrence(e.target.value as typeof recurrence)}>
            {(['none', 'daily', 'weekly', 'monthly'] as const).map((r) => <option key={r} value={r}>{r === 'none' ? 'One-time' : r}</option>)}
          </select>
        </div>
        <div>
          <label className={label}>Scope</label>
          <div className="space-y-2 text-sm">
            <label className="flex items-center gap-2 text-gray-700 dark:text-gray-300">
              <input type="radio" checked={scopeAll} onChange={() => setScopeAll(true)} /> All devices and services
            </label>
            <label className="flex items-center gap-2 text-gray-700 dark:text-gray-300">
              <input type="radio" checked={!scopeAll} onChange={() => setScopeAll(false)} /> Specific devices
            </label>
            {!scopeAll && (
              <select multiple className={clsx(input, 'h-28')} value={deviceIds.map(String)}
                onChange={(e) => setDeviceIds(Array.from(e.target.selectedOptions, (o) => Number(o.value)))}>
                {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname}</option>)}
              </select>
            )}
          </div>
        </div>
        <div><label className={label}>Suppress severities <span className="text-gray-400">(none = all)</span></label>
          <MultiCheck options={SEVERITIES} value={severity} onChange={setSeverity} /></div>
      </div>
    </Modal>
  )
}
