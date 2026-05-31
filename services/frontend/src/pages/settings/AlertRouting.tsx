import { useEffect, useState } from 'react'
import clsx from 'clsx'
import Modal from '../../components/Modal'
import {
  fetchTeams, saveTeam, deleteTeam, fetchPolicies, savePolicy,
  fetchRoutes, saveRoute, deleteRoute, testRoute,
  type AlertTeam, type EscalationPolicy, type AlertRoute,
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
  const [modal, setModal] = useState<null | 'team' | 'policy' | 'route'>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = () => {
    fetchTeams().then(setTeams).catch(() => setErr('Could not load alert routing.'))
    fetchPolicies().then(setPolicies).catch(() => {})
    fetchRoutes().then(setRoutes).catch(() => {})
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
                <button onClick={() => deleteTeam(t.id).then(load)} className="ml-auto text-xs text-red-600 hover:text-red-800">Delete</button>
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

      {modal === 'team' && <TeamModal onClose={() => setModal(null)} onSaved={() => { setModal(null); load() }} />}
      {modal === 'policy' && <PolicyModal teams={teams} onClose={() => setModal(null)} onSaved={() => { setModal(null); load() }} />}
      {modal === 'route' && <RouteModal policies={policies} onClose={() => setModal(null)} onSaved={() => { setModal(null); load() }} />}
    </div>
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
  const [busy, setBusy] = useState(false)
  return (
    <Modal title="Add Team" onClose={onClose} footer={
      <>
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm">Cancel</button>
        <button disabled={busy || !name.trim()} onClick={() => { setBusy(true); saveTeam({ name, color }).then(onSaved).finally(() => setBusy(false)) }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm disabled:opacity-50">Save</button>
      </>
    }>
      <div className="space-y-4">
        <div><label className={label}>Name</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Network Ops" /></div>
        <div><label className={label}>Colour</label><input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-9 w-16 rounded border border-gray-300 dark:border-gray-600" /></div>
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
