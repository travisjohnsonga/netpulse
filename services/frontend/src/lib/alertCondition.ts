// Human-friendly rendering of an AlertRule.condition JSON.
//
// Conditions in spane are lightweight metadata, NOT a rigid schema: engine and
// seeded rules store {source, metric} (the real threshold lives in the engine),
// while user-created / cloned rules store {metric, op, threshold[, for_checks]}.
// This best-effort parser renders the common shapes into plain English and
// reports whether it fully understood the shape — when it didn't (`parsed:
// false`), the UI leans on the raw JSON it always shows alongside.

export interface ConditionSummary {
  /** Best-effort plain-English description. */
  text: string
  /** false → we fell back to enumerating keys; the raw JSON is authoritative. */
  parsed: boolean
}

// Accept the many spellings an operator might arrive as → a tidy math symbol.
const OP_SYMBOLS: Record<string, string> = {
  '>': '>', '>=': '≥', '<': '<', '<=': '≤', '==': '=', '=': '=', '!=': '≠',
  gt: '>', gte: '≥', ge: '≥', lt: '<', lte: '≤', le: '≤', eq: '=', ne: '≠',
  greater_than: '>', greater_or_equal: '≥', less_than: '<',
  less_or_equal: '≤', equals: '=', not_equals: '≠',
}

// Known metric → display label. Anything unlisted is humanized generically.
const METRIC_LABELS: Record<string, string> = {
  rtt_ms: 'RTT', latency_ms: 'Latency', cpu: 'CPU', cpu_pct: 'CPU',
  memory: 'Memory', memory_pct: 'Memory', temperature_c: 'Temperature',
  flow_mbps: 'Flow volume', wan_utilization: 'WAN utilization',
  poe_used_pct: 'PoE usage', log_keywords: 'Log keywords',
  config_diff: 'Config change', sensor_status: 'Sensor status',
}

// Unit inferred from a metric-name suffix (tight spacing to read "500ms",
// "85°C", "90%"; Mbps/bps get a leading space). An explicit `unit` key wins.
const UNIT_BY_SUFFIX: [string, string][] = [
  ['_ms', 'ms'], ['_mbps', ' Mbps'], ['_bps', ' bps'],
  ['_pct', '%'], ['_percent', '%'], ['_c', '°C'],
]

function metricLabel(metric: string): string {
  if (METRIC_LABELS[metric]) return METRIC_LABELS[metric]
  let base = metric
  for (const [suf] of UNIT_BY_SUFFIX) {
    if (base.endsWith(suf)) { base = base.slice(0, -suf.length); break }
  }
  return base.replace(/_/g, ' ').trim() || metric
}

function metricUnit(metric: string, explicit?: unknown): string {
  if (typeof explicit === 'string' && explicit.trim()) {
    const u = explicit.trim()
    return u === '%' || u.startsWith('°') ? u : ` ${u}`
  }
  for (const [suf, unit] of UNIT_BY_SUFFIX) if (metric.endsWith(suf)) return unit
  return ''
}

function sourceLabel(source: string): string {
  const known: Record<string, string> = {
    'reachability_monitor': 'the reachability monitor',
    'stream-processor': 'the stream processor',
    'config_manager': 'the config manager',
    'environment_poll': 'the environment poller',
    'circuits': 'the circuit checker',
  }
  return known[source] ?? source.replace(/[_-]/g, ' ')
}

function firstNum(...vals: unknown[]): number | null {
  for (const v of vals) {
    if (typeof v === 'number' && Number.isFinite(v)) return v
    if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v)
  }
  return null
}

function fmtVal(v: unknown): string {
  if (v === null) return 'null'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function describeCondition(
  condition: Record<string, unknown> | null | undefined,
): ConditionSummary {
  if (!condition || typeof condition !== 'object') {
    return { text: 'No condition defined.', parsed: false }
  }
  const c = condition
  const keys = Object.keys(c)
  if (keys.length === 0) return { text: 'No condition defined.', parsed: false }

  // Internal platform-health rules carry {meta: true}; there is no user threshold.
  if (c.meta === true) {
    return {
      text: 'Internal spane platform-health check — evaluated by spane’s own engine, not a user-set threshold.',
      parsed: true,
    }
  }

  const metric = typeof c.metric === 'string' ? c.metric : null
  const rawOp = (c.op ?? c.operator ?? c.comparison)
  const op = typeof rawOp === 'string' ? (OP_SYMBOLS[rawOp] ?? rawOp) : undefined
  const threshold = c.threshold ?? c.value ?? c.limit
  const source = typeof c.source === 'string' ? c.source : null

  // A "sustained for N checks / N seconds" clause, if present.
  const checks = firstNum(c.for_checks, c.consecutive, c.sustained_checks, c.checks)
  const durS = firstNum(c.duration_s, c.duration_seconds, c.duration)
  let sustain = ''
  if (checks != null) sustain = `, sustained for ${checks} check${checks === 1 ? '' : 's'}`
  else if (durS != null) sustain = `, sustained for ${durS}s`

  if (metric) {
    const label = metricLabel(metric)
    if (op && (typeof threshold === 'number' || typeof threshold === 'string')) {
      return {
        text: `Fires when ${label} ${op} ${threshold}${metricUnit(metric, c.unit)}${sustain}.`,
        parsed: true,
      }
    }
    if (source) {
      const s = sourceLabel(source)
      return {
        text: `Watches ${label} reported by ${s}${sustain}. The threshold is configured on ${s}.`,
        parsed: true,
      }
    }
    return { text: `Watches ${label}${sustain}.`, parsed: true }
  }

  if (source) {
    return { text: `Evaluated by ${sourceLabel(source)}${sustain}.`, parsed: true }
  }

  // Unknown shape → enumerate its keys so the user still sees *something*
  // readable; the raw JSON block carries the authoritative detail.
  return { text: keys.map((k) => `${k}: ${fmtVal(c[k])}`).join(', '), parsed: false }
}
