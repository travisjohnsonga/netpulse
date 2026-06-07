import axios from 'axios'
import { useAuthStore } from '../store/authStore'

const API_URL = import.meta.env.VITE_API_URL || '/api'

export const api = axios.create({
  baseURL: API_URL,
  headers: { 'Content-Type': 'application/json' },
})

// Attach access token to every request
api.interceptors.request.use((cfg) => {
  const token = useAuthStore.getState().accessToken
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// Refresh on 401, then retry once
let refreshing: Promise<string> | null = null

api.interceptors.response.use(
  (res) => res,
  async (error: unknown) => {
    const axiosError = error as import('axios').AxiosError
    const original = axiosError.config as (import('axios').InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
    if (axiosError.response?.status === 401 && original && !original._retry) {
      original._retry = true
      const { refreshToken, setAccessToken, logout } = useAuthStore.getState()
      if (!refreshToken) { logout(); return Promise.reject(error) }
      try {
        if (!refreshing) {
          refreshing = axios
            .post<{ access: string }>(`${API_URL}/auth/token/refresh/`, { refresh: refreshToken })
            .then((r) => r.data.access)
            .finally(() => { refreshing = null })
        }
        const newAccess = await refreshing
        setAccessToken(newAccess)
        original.headers.Authorization = `Bearer ${newAccess}`
        return api(original)
      } catch {
        logout()
        return Promise.reject(error)
      }
    }
    return Promise.reject(error)
  },
)

// ── Types ────────────────────────────────────────────────────────────────────

export interface HealthStatus {
  status: string
  db?: boolean
  collector_ip?: string
  ssl_cert_days_remaining?: number | null
}

export interface InfraServiceHealth {
  ok: boolean
  response_ms: number | null
}

export interface InfraHealth {
  checked_at?: string
  version?: string
  services: {
    postgres: InfraServiceHealth
    valkey: InfraServiceHealth
    nats: InfraServiceHealth
    influxdb: InfraServiceHealth
    opensearch: InfraServiceHealth
    openbao: InfraServiceHealth
  }
}

// Configurable device role (Core Switch, Firewall, …) with a colour used for
// the role bubbles in the device list/detail. Nested on devices as `role`.
export interface DeviceRole {
  id: number
  name: string
  slug: string
  color: string
  description: string
  icon: string
  device_count?: number
  created_at?: string
  updated_at?: string
}

export interface Device {
  id: number
  hostname: string
  // Display-only hostname (domain suffix optionally stripped). The serializer
  // always returns it; use `display_hostname || hostname` when rendering.
  display_hostname: string
  // When the hostname was last verified against the network (SNMP sysName / DNS).
  hostname_verified_at?: string | null
  ip_address: string
  management_ip: string | null
  platform: string
  vendor: string
  model: string
  os_version: string
  serial_number: string
  status: 'active' | 'inactive' | 'pending' | 'unreachable'
  site_name: string | null
  role: DeviceRole | null
  credential_profile: number | null
  last_seen: string | null
  is_reachable?: boolean
  consecutive_failures?: number
  last_reachability_check?: string | null
  unreachable_since?: string | null
  notes: string
  created_at: string
}

// Reachability state from the authoritative status + is_reachable fields the
// reachability monitor maintains (and pushes live over /ws/devices/). It is NOT
// derived from last_seen age: last_seen tracks the last telemetry/poll write,
// which legitimately lags the poll interval, so age-based coloring wrongly
// shows healthy active devices as degraded/unreachable.
export type Reachability = 'reachable' | 'degraded' | 'unreachable'
export function reachabilityOf(d: { is_reachable?: boolean; status?: string; last_seen?: string | null }): Reachability {
  if (d.status === 'unreachable' || d.is_reachable === false) return 'unreachable'
  if (d.is_reachable === true || d.status === 'active') return 'reachable'
  // pending / inactive / unknown — not confirmed reachable.
  return 'degraded'
}

function _ageStr(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

// Human explanation of the reachability state — used as the badge tooltip.
// Reflects the authoritative status + is_reachable (see reachabilityOf); last
// seen is shown as supporting context only, never as the cause.
export function reachabilityReason(d: {
  is_reachable?: boolean; status?: string; last_seen?: string | null
  unreachable_since?: string | null; consecutive_failures?: number
}): string {
  const reach = reachabilityOf(d)
  if (reach === 'unreachable') {
    if (d.unreachable_since) return `Unreachable — down for ${_ageStr(d.unreachable_since)}`
    const f = d.consecutive_failures ? ` (${d.consecutive_failures} failed checks)` : ''
    return `Unreachable — last reachability check failed${f}`
  }
  if (reach === 'degraded') {
    return `${d.status === 'pending' ? 'Pending approval' : d.status === 'inactive' ? 'Inactive' : 'Not confirmed reachable'} — not actively monitored`
  }
  return d.last_seen ? `Reachable — last seen ${_ageStr(d.last_seen)} ago` : 'Reachable'
}

export interface DeviceListResponse {
  count: number
  next: string | null
  previous: string | null
  results: Device[]
}

export interface Alert {
  id: number
  severity: 'critical' | 'high' | 'medium' | 'low'
  effective_severity: 'critical' | 'high' | 'medium' | 'low' | 'info'
  rule_name: string
  title: string
  device: string
  device_id: number | null
  interface: string
  transition: '' | 'up' | 'down'
  downtime_seconds: number | null
  is_interface_alert: boolean
  fired_at: string
  state: 'firing' | 'acknowledged' | 'resolved'
  message: string
  // Long-form detail (e.g. a config-change unified diff) + machine alert type.
  details?: string
  alert_type?: string
  is_resolved?: boolean
  resolved_by?: string
  resolved_at?: string | null
}

export interface TopologyNode {
  id: string
  label: string
  type: string
  site: string | null
  status: string
  role?: string
  risk_score: number
  ip?: string
  vendor?: string
}

export interface TopologyEdge {
  source: string
  target: string
  port_a: string
  port_b: string
  speed_mbps: number | null
  utilization_pct: number
  utilization_color: string
}

export interface TopologyData {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
}

// ── Credentials ────────────────────────────────────────────────────────────

export type CredentialProtocol = 'ssh' | 'snmpv2c' | 'snmpv3' | 'https' | 'netconf' | 'gnmi'

export type TestResult = 'untested' | 'success' | 'partial' | 'failure'

// Full profile. One profile carries multiple protocols, each toggled via an
// *_enabled flag. Secret fields are write-only — accepted on write, forwarded
// to OpenBao, and never returned on read.
export interface CredentialProfile {
  id: number
  name: string
  description: string
  vault_path: string
  device_count: number
  enabled_protocols: CredentialProtocol[]

  ssh_enabled: boolean
  ssh_username: string
  ssh_auth_method: string
  ssh_port: number

  snmpv2c_enabled: boolean
  snmpv2c_port: number

  snmpv3_enabled: boolean
  snmpv3_username: string
  snmpv3_security_level: string
  snmpv3_auth_protocol: string
  snmpv3_priv_protocol: string
  snmpv3_port: number

  https_enabled: boolean
  https_auth_type: string
  https_username: string
  https_port: number
  https_verify_tls: boolean

  netconf_enabled: boolean
  netconf_port: number
  netconf_use_ssh_creds: boolean
  netconf_username: string

  gnmi_enabled: boolean
  gnmi_username: string
  gnmi_port: number
  gnmi_tls_enabled: boolean

  created_by: number | null
  last_tested: string | null
  last_test_result: TestResult
  last_test_message: string
  created_at: string
  updated_at: string
}

// Lightweight shape returned by the list endpoint.
export interface CredentialProfileListItem {
  id: number
  name: string
  enabled_protocols: CredentialProtocol[]
  device_count: number
  last_tested: string | null
  last_test_result: TestResult
  created_at: string
}

// Write payload — non-secret config plus optional write-only secrets.
// Loosely typed so the form can send a partial object.
export type CredentialProfilePayload = Partial<Record<string, unknown>> & { name: string }

export interface CredentialTestProtocolResult {
  protocol: CredentialProtocol
  label: string
  success: boolean
  message: string
  port: number
}

export interface CredentialTestResult {
  ip: string
  overall: TestResult
  results: CredentialTestProtocolResult[]
}

export interface CredentialProfileDevice {
  id: number
  hostname: string
  ip_address: string
  status: string
}

interface Paginated<T> {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

function unwrap<T>(data: T[] | Paginated<T>): T[] {
  return Array.isArray(data) ? data : (data.results ?? [])
}

// ── Config diff (structured unified diff from the backend) ───────────────────

export interface ConfigDiffLine {
  type: 'context' | 'add' | 'remove'
  content: string
  line_no: number
}

export interface ConfigDiffHunk {
  old_start: number
  old_count: number
  new_start: number
  new_count: number
  lines: ConfigDiffLine[]
}

export interface ConfigDiff {
  summary: { added: number; removed: number; changed: number }
  hunks: ConfigDiffHunk[]
}

// Compare two stored snapshots by id, or two raw config strings.
export async function fetchConfigDiff(
  payload: { left: number; right: number } | { old: string; new: string },
  context = 3,
): Promise<ConfigDiff> {
  const { data } = await api.post<ConfigDiff>('/configbackup/configs/diff/', { ...payload, context })
  return data
}

// ── API calls ────────────────────────────────────────────────────────────────

export async function login(
  username: string,
  password: string,
): Promise<{ access: string; refresh: string; must_change_password?: boolean }> {
  const { data } = await api.post<{ access: string; refresh: string; must_change_password?: boolean }>(
    '/auth/token/', { username, password },
  )
  return data
}

export async function checkHealth(): Promise<HealthStatus> {
  const { data } = await api.get<HealthStatus>('/health/')
  return data
}

export interface SetupStatus {
  setup_complete: boolean
  openbao_healthy: boolean
  database_healthy: boolean
  version: string
}

// No auth required — used to gate the app before login.
export async function fetchSetupStatus(): Promise<SetupStatus> {
  const { data } = await api.get<SetupStatus>('/setup/status/')
  return data
}

export interface MetricPoint { time: string; value: number }
export interface DeviceMetrics {
  device_id: string
  period: string
  metrics: {
    uptime_seconds: number | null
    memory_used_bytes: number | null
    memory_free_bytes: number | null
    memory_total_bytes: number | null
    memory_used_pct: number | null
    cpu_pct: number | null
    poll_duration_ms: number | null
  }
  timeseries: {
    uptime: MetricPoint[]
    memory_used_pct: MetricPoint[]
    cpu_pct: MetricPoint[]
  }
  interfaces: InterfaceStat[]
  lldp_neighbors?: LldpNeighbor[]
  environment?: DeviceEnvironment
  reachability?: DeviceReachability
}

export interface ReachabilityPoint {
  time: string
  rtt_ms: number | null
  reachable: boolean | null
}

// Ping/RTT latency history (from device_reachability in InfluxDB).
export interface DeviceReachability {
  current: boolean | null
  rtt_ms: number | null
  uptime_pct_24h: number | null
  avg_rtt_ms: number | null
  max_rtt_ms: number | null
  data: ReachabilityPoint[]
}

export interface DeviceEnvironmentSensor {
  sensor_name: string
  temperature_c: number | null
  status_ok: boolean
}

// Per-fan / per-PSU detail. reading (rpm/watts) is null when the device reports
// it as unavailable; status_ok is null when no per-unit sensor exists (unknown).
export interface DeviceEnvironmentFan {
  name: string
  rpm: number | null
  status_ok: boolean | null
}

export interface DeviceEnvironmentPsu {
  name: string
  watts: number | null
  status_ok: boolean | null
}

export interface DeviceEnvironmentPoe {
  budget_watts: number | null
  used_watts: number | null
  used_pct: number | null
  status: string // on / off / faulty / unknown
}

// Physical-sensor summary; empty {} for devices that report none (e.g. virtual).
export interface DeviceEnvironment {
  temperature_c?: number
  temperature_sensors?: number
  fan_sensors?: number
  power_sensors?: number
  // Explicit counts from ENTITY-SENSOR devices (AOS-CX).
  fan_count?: number
  psu_count?: number
  // Per-unit detail (device_environment measurement) + 24h temperature history.
  sensors?: DeviceEnvironmentSensor[]
  fans?: DeviceEnvironmentFan[]
  psus?: DeviceEnvironmentPsu[]
  poe?: DeviceEnvironmentPoe
  temperature_history?: MetricPoint[]
}

export interface LldpNeighbor {
  local_port: string
  neighbor_id: number
  neighbor_hostname: string
  remote_port: string
  discovered_via: string
}

export interface InterfaceStat {
  if_name: string
  if_index: number | string
  in_bps: number | null
  out_bps: number | null
  in_pps: number | null
  out_pps: number | null
  in_errors_rate: number | null
  out_errors_rate: number | null
  in_discards_rate: number | null
  out_discards_rate: number | null
  in_util_pct: number | null
  out_util_pct: number | null
  oper_status: string | null
  series: { in_bps: MetricPoint[]; out_bps: MetricPoint[] }
}

export async function fetchDeviceMetrics(deviceId: number, period = '1h'): Promise<DeviceMetrics> {
  const { data } = await api.get<DeviceMetrics>(`/devices/${deviceId}/metrics/`, { params: { period } })
  return data
}

export async function fetchDeviceReachability(deviceId: number, period = '1h'): Promise<DeviceReachability & { device_id: string; period: string }> {
  const { data } = await api.get(`/devices/${deviceId}/reachability/`, { params: { period } })
  return data
}

export async function pollDeviceNow(deviceId: number): Promise<{ status: string; device_id: number }> {
  const { data } = await api.post(`/devices/${deviceId}/poll-now/`)
  return data
}

// Re-run SNMP/SSH enrichment + interface/LLDP discovery in the background.
export async function enrichDevice(deviceId: number): Promise<{ status: string; device_id: number }> {
  const { data } = await api.post(`/devices/${deviceId}/enrich/`)
  return data
}

// Re-verify the device hostname now (SNMP sysName / DNS). Updates if changed.
export async function checkHostname(deviceId: number): Promise<{
  hostname_changed: boolean; old_hostname: string; new_hostname: string
}> {
  const { data } = await api.post(`/devices/${deviceId}/check-hostname/`)
  return data
}

// ── Ping summary (device-list sparklines) ─────────────────────────────────────
export interface PingSummary {
  device_id: number
  current_ms: number | null
  avg_ms: number | null
  max_ms: number | null
  uptime_pct: number | null
  sparkline: (number | null)[]   // ~24 points over 1h (2m30s buckets); null = unreachable/no data
}

export async function fetchPingSummary(): Promise<PingSummary[]> {
  const { data } = await api.get<PingSummary[]>('/devices/ping-summary/')
  return data
}

// ── ARP / MAC tables ──────────────────────────────────────────────────────────
export interface ArpEntry {
  id: number
  ip_address: string
  mac_address: string
  vendor: string
  interface: string
  vlan: number | null
  protocol: string
  age_minutes: number | null
  collected_at: string
}

export interface MacEntry {
  id: number
  mac_address: string
  vendor: string
  vlan: number | null
  interface: string
  entry_type: string
  collected_at: string
}

export interface ArpResponse { count: number; last_collected: string | null; results: ArpEntry[] }
export interface MacResponse { count: number; last_collected: string | null; results: MacEntry[] }

export async function fetchDeviceArp(deviceId: number, search = ''): Promise<ArpResponse> {
  const { data } = await api.get<ArpResponse>(`/devices/${deviceId}/arp/`, { params: search ? { search } : {} })
  return data
}

export async function fetchDeviceMac(
  deviceId: number, opts: { search?: string; vlan?: string; interface?: string } = {},
): Promise<MacResponse> {
  const { data } = await api.get<MacResponse>(`/devices/${deviceId}/mac/`, { params: opts })
  return data
}

export async function collectDeviceArpMac(deviceId: number): Promise<{ status: string; device_id: number }> {
  const { data } = await api.post(`/devices/${deviceId}/arp-mac/collect/`)
  return data
}

export interface NetworkSearchResult {
  query: string
  arp: (ArpEntry & { device_id: number; device_hostname: string })[]
  mac: (MacEntry & { device_id: number; device_hostname: string })[]
}

export async function networkSearch(q: string): Promise<NetworkSearchResult> {
  const { data } = await api.get<NetworkSearchResult>('/network/search/', { params: { q } })
  return data
}

// How a device's telemetry is currently being collected (gNMI / SNMP).
export interface CollectionStatus {
  device_id: string
  gnmi: {
    active: boolean
    last_seen_seconds_ago: number | null
    metrics_per_push: number | null
    interval_seconds: number
  }
  snmp: {
    active: boolean
    suppressed?: boolean
    suppressed_reason?: string
    last_poll_seconds_ago: number | null
    interval_seconds: number
    version: string | null
  }
  primary: 'gnmi' | 'snmp' | null
  any_active: boolean
}

export async function fetchCollectionStatus(deviceId: number): Promise<CollectionStatus> {
  const { data } = await api.get<CollectionStatus>(`/devices/${deviceId}/collection-status/`)
  return data
}

export interface SystemSettings {
  allow_config_push: boolean
  collector_ip: string
}

export async function fetchSystemSettings(): Promise<SystemSettings> {
  const { data } = await api.get<SystemSettings>('/settings/system/')
  return data
}

export interface HostnameDisplay {
  mode: 'strip' | 'full'
  domain_suffix: string
}

export async function fetchHostnameDisplay(): Promise<HostnameDisplay> {
  const { data } = await api.get<HostnameDisplay>('/settings/hostname-display/')
  return data
}

export async function saveHostnameDisplay(payload: HostnameDisplay): Promise<HostnameDisplay> {
  const { data } = await api.put<HostnameDisplay>('/settings/hostname-display/', payload)
  return data
}

export async function fetchDevices(params?: Record<string, string>): Promise<DeviceListResponse> {
  const { data } = await api.get<DeviceListResponse>('/devices/', { params })
  return data
}

// DRF returns paginated { count, results } by default.
// Defensively coerce to array regardless of shape.
type MaybePaginated<T> = T[] | { results: T[]; count: number; next: string | null; previous: string | null }

// resolved: 'false' (default, active only) | 'true' (resolved only) | 'all'.
export async function fetchAlerts(resolved: 'false' | 'true' | 'all' = 'false'): Promise<Alert[]> {
  const { data } = await api.get<MaybePaginated<Alert>>('/alerts/events/', { params: { resolved } })
  return Array.isArray(data) ? data : (data.results ?? [])
}

export async function resolveAlertEvent(id: number, note?: string): Promise<void> {
  await api.post(`/alerts/events/${id}/resolve/`, { note })
}

export async function checkInfraHealth(): Promise<InfraHealth> {
  const { data } = await api.get<InfraHealth>('/health/infrastructure/')
  return data
}

export async function fetchTopology(params?: Record<string, string>): Promise<TopologyData> {
  const { data } = await api.get<TopologyData>('/devices/topology/', { params })
  return data
}

export async function discoverDeviceLinks(deviceId: number): Promise<{ count: number; matched: number }> {
  const { data } = await api.post(`/devices/${deviceId}/topology/discover/`)
  return data
}

export async function acknowledgeAlert(id: number): Promise<void> {
  await api.patch(`/alerts/events/${id}/`, { state: 'acknowledged' })
}

// ── Credential profile API ───────────────────────────────────────────────────

export async function fetchCredentials(
  params?: Record<string, string>,
): Promise<CredentialProfileListItem[]> {
  const { data } = await api.get<CredentialProfileListItem[] | Paginated<CredentialProfileListItem>>(
    '/credentials/', { params },
  )
  return unwrap(data)
}

export async function fetchCredential(id: number): Promise<CredentialProfile> {
  const { data } = await api.get<CredentialProfile>(`/credentials/${id}/`)
  return data
}

export async function createCredential(
  payload: CredentialProfilePayload,
): Promise<CredentialProfile> {
  const { data } = await api.post<CredentialProfile>('/credentials/', payload)
  return data
}

export async function updateCredential(
  id: number, payload: Partial<CredentialProfilePayload>,
): Promise<CredentialProfile> {
  const { data } = await api.patch<CredentialProfile>(`/credentials/${id}/`, payload)
  return data
}

export async function deleteCredential(id: number): Promise<void> {
  await api.delete(`/credentials/${id}/`)
}

export async function testCredential(
  id: number, ip: string,
): Promise<CredentialTestResult> {
  const { data } = await api.post<CredentialTestResult>(
    `/credentials/${id}/test/`, null, { params: { ip } },
  )
  return data
}

export async function fetchCredentialDevices(id: number): Promise<CredentialProfileDevice[]> {
  const { data } = await api.get<CredentialProfileDevice[]>(`/credentials/${id}/devices/`)
  return data
}

// Assign (or clear, with null) the device's single credential profile.
export async function setDeviceCredentialProfile(
  deviceId: number, profileId: number | null,
): Promise<DeviceDetail> {
  const { data } = await api.patch<DeviceDetail>(
    `/devices/${deviceId}/`, { credential_profile: profileId },
  )
  return data
}

// ── Alert rules & channels ───────────────────────────────────────────────────

export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info'

export interface AlertRule {
  id: number
  name: string
  description: string
  severity: AlertSeverity
  condition: Record<string, unknown>
  channels: number[]
  is_active: boolean
  is_system: boolean
  cooldown_minutes: number
  created_at: string
  updated_at: string
}

export interface AlertChannel {
  id: number
  name: string
  channel_type: 'slack' | 'email' | 'pagerduty' | 'webhook'
  config: Record<string, unknown>
  is_active: boolean
}

export async function fetchAlertRules(): Promise<AlertRule[]> {
  const { data } = await api.get<AlertRule[] | Paginated<AlertRule>>('/alerts/rules/')
  return unwrap(data)
}

export async function createAlertRule(payload: Partial<AlertRule>): Promise<AlertRule> {
  const { data } = await api.post<AlertRule>('/alerts/rules/', payload)
  return data
}

export async function updateAlertRule(id: number, payload: Partial<AlertRule>): Promise<AlertRule> {
  const { data } = await api.patch<AlertRule>(`/alerts/rules/${id}/`, payload)
  return data
}

export async function fetchAlertChannels(): Promise<AlertChannel[]> {
  const { data } = await api.get<AlertChannel[] | Paginated<AlertChannel>>('/alerts/channels/')
  return unwrap(data)
}

// ── Collectors ───────────────────────────────────────────────────────────────

export type CollectorType = 'local' | 'remote'

export interface Collector {
  id: number
  name: string
  collector_type: CollectorType
  hostname: string
  location: string
  capabilities: Record<string, boolean>
  collector_ip: string | null
  site: number | null
  site_name?: string | null
  is_default: boolean
  device_count?: number
  is_healthy?: boolean
  status: 'pending' | 'active' | 'offline' | 'revoked'
  version: string
  remote_ip: string | null
  cert_serial: string
  cert_expires_at: string | null
  last_seen_at: string | null
  created_at: string
}

export async function fetchCollectors(): Promise<Collector[]> {
  const { data } = await api.get<Collector[] | Paginated<Collector>>('/collectors/')
  return unwrap(data)
}

export async function updateCollector(id: number, payload: Partial<Collector>): Promise<Collector> {
  const { data } = await api.patch<Collector>(`/collectors/${id}/`, payload)
  return data
}

// ── NetBox import ────────────────────────────────────────────────────────────

export interface NetBoxImportRecord {
  id: number
  netbox_url: string
  netbox_version: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  options: Record<string, boolean>
  sites_imported: number
  devices_imported: number
  skipped: number
  errors: string[]
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export async function netboxTestConnection(netbox_url: string, api_token: string): Promise<{ ok: boolean; version: string; message: string }> {
  const { data } = await api.post<{ ok: boolean; version: string; message: string }>('/import/netbox/test-connection/', { netbox_url, api_token })
  return data
}

export async function netboxImport(payload: { netbox_url: string; api_token: string; import_options: Record<string, boolean> }): Promise<NetBoxImportRecord> {
  const { data } = await api.post<NetBoxImportRecord>('/import/netbox/', payload)
  return data
}

export async function fetchNetboxImports(): Promise<NetBoxImportRecord[]> {
  const { data } = await api.get<NetBoxImportRecord[] | Paginated<NetBoxImportRecord>>('/import/netbox/')
  return unwrap(data)
}

export interface NetBoxPreviewDevice {
  action: 'create' | 'update' | 'skip'
  hostname: string
  ip: string | null
  platform?: string
  site?: string | null
  role?: string | null
  credential?: string | null
  reason?: string | null
  existing_id?: number
  changes?: string[]
}
export interface NetBoxPreview {
  summary: { total: number; will_create: number; will_update: number; will_skip: number }
  devices: NetBoxPreviewDevice[]
  credentials: { assignments: Record<string, number>; no_match: number }
}
export async function netboxPreview(payload: { netbox_url: string; api_token: string; import_options: Record<string, boolean> }): Promise<NetBoxPreview> {
  const { data } = await api.post<NetBoxPreview>('/import/netbox/preview/', payload)
  return data
}

// ── Site credential assignments ───────────────────────────────────────────────
export interface SiteCredential {
  id: number
  site: number
  credential_profile: number
  credential_profile_name: string
  role: number | null
  role_name: string | null
  priority: number
}
export async function fetchSiteCredentials(siteId: number): Promise<SiteCredential[]> {
  const { data } = await api.get<SiteCredential[]>(`/sites/${siteId}/credentials/`)
  return data
}
export async function addSiteCredential(siteId: number, payload: { credential_profile: number; role: number | null; priority: number }): Promise<SiteCredential> {
  const { data } = await api.post<SiteCredential>(`/sites/${siteId}/credentials/`, payload)
  return data
}
export async function deleteSiteCredential(siteId: number, credId: number): Promise<void> {
  await api.delete(`/sites/${siteId}/credentials/${credId}/`)
}
export async function suggestSiteCredential(siteId: number, roleId?: number | null): Promise<{ credential_profile: number | null; name: string | null; scope: string | null }> {
  const { data } = await api.get(`/sites/${siteId}/suggest-credential/`, { params: roleId ? { role: roleId } : {} })
  return data
}

// ── Email / SMTP settings (Settings → Integrations → Email) ───────────────────
export interface EmailProviderPreset {
  host: string; port: number; use_tls: boolean; use_ssl: boolean; username?: string; help: string
}
export interface EmailSettings {
  provider: string
  host: string
  port: number
  username: string
  use_tls: boolean
  use_ssl: boolean
  from_email: string
  from_name: string
  enabled: boolean
  password_set?: boolean
  provider_presets?: Record<string, EmailProviderPreset>
}

export async function fetchEmailSettings(): Promise<EmailSettings> {
  const { data } = await api.get<EmailSettings>('/integrations/email/')
  return data
}

export async function saveEmailSettings(
  payload: Partial<EmailSettings> & { password?: string },
): Promise<EmailSettings> {
  const { data } = await api.put<EmailSettings>('/integrations/email/', payload)
  return data
}

export async function sendTestEmail(to: string): Promise<{ sent: boolean; error?: string }> {
  const { data } = await api.post<{ sent: boolean; error?: string }>('/integrations/email/test/', { to })
  return data
}

// ── UniFi controllers (Settings → Integrations → UniFi) ───────────────────────
export interface UnifiController {
  id: number
  name: string
  host: string
  port: number
  username: string
  verify_ssl: boolean
  unifi_site_id: string
  site: number | null
  site_name?: string | null
  enabled: boolean
  last_sync: string | null
  last_error: string
  device_count: number
  password_set?: boolean
}

export async function fetchUnifiControllers(): Promise<UnifiController[]> {
  const { data } = await api.get<UnifiController[] | Paginated<UnifiController>>('/integrations/unifi/')
  return unwrap(data)
}
export async function createUnifiController(payload: Partial<UnifiController> & { password?: string }): Promise<UnifiController> {
  const { data } = await api.post<UnifiController>('/integrations/unifi/', payload)
  return data
}
export async function updateUnifiController(id: number, payload: Partial<UnifiController> & { password?: string }): Promise<UnifiController> {
  const { data } = await api.put<UnifiController>(`/integrations/unifi/${id}/`, payload)
  return data
}
export async function deleteUnifiController(id: number): Promise<void> {
  await api.delete(`/integrations/unifi/${id}/`)
}
export async function testUnifiController(id: number, password?: string): Promise<{ connected: boolean; sites?: string[]; device_count?: number; error?: string }> {
  const { data } = await api.post(`/integrations/unifi/${id}/test/`, password ? { password } : {})
  return data
}
export async function syncUnifiController(id: number): Promise<{ imported: number; updated: number; skipped: number }> {
  const { data } = await api.post(`/integrations/unifi/${id}/sync/`)
  return data
}
export async function syncAllUnifi(): Promise<{ controllers: number; imported: number; updated: number; skipped: number; failed: number }> {
  const { data } = await api.post('/integrations/unifi/sync-all/')
  return data
}

// ── UniFi Site Manager (cloud) account ────────────────────────────────────────
export interface UnifiCloudAccount {
  name: string
  enabled: boolean
  last_sync: string | null
  last_error: string
  host_count: number
  api_key_set?: boolean
}
export interface UnifiDiscoveredController {
  name: string; host: string; port: number; model: string; version: string; status: 'created' | 'updated'
}
export async function fetchUnifiCloud(): Promise<UnifiCloudAccount> {
  const { data } = await api.get<UnifiCloudAccount>('/integrations/unifi/cloud/')
  return data
}
export async function saveUnifiCloud(payload: Partial<UnifiCloudAccount> & { api_key?: string }): Promise<UnifiCloudAccount> {
  const { data } = await api.put<UnifiCloudAccount>('/integrations/unifi/cloud/', payload)
  return data
}
export async function testUnifiCloud(apiKey?: string): Promise<{ connected: boolean; host_count?: number; error?: string }> {
  const { data } = await api.post('/integrations/unifi/cloud/test/', apiKey ? { api_key: apiKey } : {})
  return data
}
export async function discoverUnifiControllers(): Promise<{ discovered: number; controllers: UnifiDiscoveredController[] }> {
  const { data } = await api.post('/integrations/unifi/cloud/discover/')
  return data
}

// ── Config backup settings ───────────────────────────────────────────────────

export interface ConfigBackupSettings {
  local_enabled: boolean
  local_path: string
  local_retention_days: number
  git_enabled: boolean
  git_provider: string
  git_repo_url: string
  git_branch: string
  git_auth_method: string
  git_vault_path: string
  git_commit_author: string
  git_commit_email: string
  git_sync_frequency: string
  last_sync_at: string | null
  last_sync_success: boolean | null
  last_commit_sha: string
  local_used_bytes: number
  updated_at: string
}

export async function fetchConfigBackup(): Promise<ConfigBackupSettings> {
  const { data } = await api.get<ConfigBackupSettings>('/settings/config-backup/')
  return data
}

export async function saveConfigBackup(payload: Partial<ConfigBackupSettings> & { git_credential?: string }): Promise<ConfigBackupSettings> {
  const { data } = await api.patch<ConfigBackupSettings>('/settings/config-backup/', payload)
  return data
}

export async function testGit(git_repo_url?: string): Promise<{ ok: boolean; message: string }> {
  const { data } = await api.post<{ ok: boolean; message: string }>('/settings/config-backup/test-git/', { git_repo_url })
  return data
}

export async function syncConfigNow(): Promise<{ ok: boolean; message: string; last_commit_sha?: string }> {
  const { data } = await api.post<{ ok: boolean; message: string; last_commit_sha?: string }>('/settings/config-backup/sync-now/')
  return data
}

// ── Logs ─────────────────────────────────────────────────────────────────────

export interface LogEntry {
  id: string
  timestamp: string
  hostname: string
  severity: string
  severity_label: string
  facility: string
  facility_label: string
  message: string
  program: string
  pid: string | null
  source_ip: string | null
  raw: string
}

export interface LogQueryResponse {
  count: number
  results: LogEntry[]
  summary: { total: number; by_severity: Record<string, number> }
  error?: string
  // Number of rows on this page hidden by enabled suppress filters
  // (from the X-Suppressed-Count response header).
  suppressed_count: number
}

export async function fetchLogs(params: Record<string, string>): Promise<LogQueryResponse> {
  const resp = await api.get<LogQueryResponse>('/logs/', { params })
  const suppressed = Number(resp.headers?.['x-suppressed-count'] ?? 0)
  return { ...resp.data, suppressed_count: Number.isFinite(suppressed) ? suppressed : 0 }
}

// ── Log Filters ─────────────────────────────────────────────────────────────────

export type LogFilterAction = 'suppress' | 'highlight' | 'tag'

export interface LogFilter {
  id: number
  name: string
  pattern: string
  action: LogFilterAction
  color: string
  tag: string
  platforms: string[]
  enabled: boolean
  created_at?: string
}

export interface LogFilterPayload {
  name: string
  pattern: string
  action: LogFilterAction
  color?: string
  tag?: string
  platforms?: string[]
  enabled?: boolean
}

export async function fetchLogFilters(): Promise<LogFilter[]> {
  const { data } = await api.get<LogFilter[] | Paginated<LogFilter>>('/logs/filters/')
  return unwrap(data)
}

export async function createLogFilter(payload: LogFilterPayload): Promise<LogFilter> {
  const { data } = await api.post<LogFilter>('/logs/filters/', payload)
  return data
}

export async function updateLogFilter(id: number, payload: Partial<LogFilterPayload>): Promise<LogFilter> {
  const { data } = await api.patch<LogFilter>(`/logs/filters/${id}/`, payload)
  return data
}

export async function deleteLogFilter(id: number): Promise<void> {
  await api.delete(`/logs/filters/${id}/`)
}

export async function testLogFilter(pattern: string, message: string): Promise<{ matches: boolean; error: string | null }> {
  const { data } = await api.post<{ matches: boolean; error: string | null }>(
    '/logs/filters/test/', { pattern, message })
  return data
}

export interface RecentConfig {
  id: number
  collected_at: string
  collected_by: string
  changed_from_previous: boolean
}

export async function fetchRecentConfigs(deviceId: number, limit = 3): Promise<RecentConfig[]> {
  const { data } = await api.get<RecentConfig[] | Paginated<RecentConfig>>(
    `/configbackup/configs/?device=${deviceId}&ordering=-collected_at&page_size=${limit}`,
  )
  return unwrap(data).slice(0, limit)
}

// ── Telemetry config & interfaces ────────────────────────────────────────────

export interface TelemetryConfig {
  id: number
  primary_method: 'snmp' | 'gnmi' | 'both'
  snmp_interval: number
  gnmi_interval: number
  collect_cpu: boolean
  collect_memory: boolean
  collect_temperature: boolean
  collect_power: boolean
  collect_fans: boolean
  collect_bgp: boolean
  collect_inventory: boolean
  collect_lldp: boolean
  override_intervals: boolean
  device_metrics_interval: number | null
  interface_traffic_interval: number | null
  interface_status_interval: number | null
  bgp_interval: number | null
  effective_intervals: { device_metrics: number; interface_traffic: number; interface_status: number; bgp: number }
}

export interface PollingSettings {
  device_metrics_interval: number
  interface_traffic_interval: number
  interface_status_interval: number
  bgp_interval: number
  inventory_interval: number
  lldp_interval: number
  max_concurrent_sessions: number
  snmp_timeout: number
  snmp_retries: number
  bulk_get_enabled: boolean
  bulk_get_max_repetitions: number
}

export async function fetchPollingSettings(): Promise<PollingSettings> {
  const { data } = await api.get<PollingSettings>('/settings/polling/')
  return data
}

export async function savePollingSettings(payload: Partial<PollingSettings>): Promise<PollingSettings> {
  const { data } = await api.put<PollingSettings>('/settings/polling/', payload)
  return data
}

// ── TLS / HTTPS server certificate ───────────────────────────────────────────

export interface SSLStatus {
  installed: boolean
  has_private_key: boolean
  source: string
  common_name: string
  issuer: string
  sans: string[]
  serial: string
  fingerprint_sha256: string
  not_before: string | null
  not_after: string | null
  expiry_status: 'none' | 'not_yet_valid' | 'expired' | 'critical' | 'warning' | 'ok'
  days_remaining: number | null
  pending_csr: string | null
}

export async function fetchSSLStatus(): Promise<SSLStatus> {
  const { data } = await api.get<SSLStatus>('/settings/ssl/')
  return data
}

export async function generateSelfSigned(payload: { common_name: string; sans?: string[]; days?: number }): Promise<SSLStatus> {
  const { data } = await api.post<SSLStatus>('/settings/ssl/self-signed/', payload)
  return data
}

export async function generateCSR(payload: { common_name: string; sans?: string[]; organization?: string; country?: string }): Promise<{ csr: string }> {
  const { data } = await api.post<{ csr: string }>('/settings/ssl/csr/', payload)
  return data
}

export async function uploadCertificate(payload: { certificate: string; private_key?: string; chain?: string }): Promise<SSLStatus> {
  const { data } = await api.post<SSLStatus>('/settings/ssl/upload/', payload)
  return data
}

// ── Trusted CA certificates ──────────────────────────────────────────────────

export interface CACertificate {
  id: number
  name: string
  subject: string
  issuer: string
  fingerprint_sha256: string
  not_before: string | null
  not_after: string | null
  is_root: boolean
  is_intermediate: boolean
  cert_pem: string
  added_by_username: string | null
  created_at: string
  expiry_status: 'ok' | 'warning' | 'expired' | 'none'
  days_remaining: number | null
}

export async function fetchCACerts(): Promise<CACertificate[]> {
  const { data } = await api.get<CACertificate[]>('/settings/ssl/ca-certs/')
  return data
}

export async function addCACert(payload: { name?: string; certificate: string }): Promise<CACertificate[]> {
  const { data } = await api.post<CACertificate[]>('/settings/ssl/ca-certs/', payload)
  return data
}

export async function deleteCACert(id: number): Promise<void> {
  await api.delete(`/settings/ssl/ca-certs/${id}/`)
}

export async function verifyCACert(id: number): Promise<{ valid: boolean; expiry_status: string; days_remaining: number | null }> {
  const { data } = await api.post(`/settings/ssl/ca-certs/${id}/verify/`)
  return data
}

// ── CVE feed settings ────────────────────────────────────────────────────────

export interface CVEFeedSettings {
  nvd_enabled: boolean
  cisa_kev_enabled: boolean
  cisco_psirt_enabled: boolean
  paloalto_enabled: boolean
  has_nvd_api_key: boolean
  has_psirt_credentials: boolean
  has_paloalto_api_key: boolean
}

export interface CVEFeedSettingsWrite {
  nvd_enabled?: boolean
  cisa_kev_enabled?: boolean
  cisco_psirt_enabled?: boolean
  paloalto_enabled?: boolean
  nvd_api_key?: string
  cisco_psirt_client_id?: string
  cisco_psirt_client_secret?: string
  paloalto_api_key?: string
}

export async function fetchCVEFeedSettings(): Promise<CVEFeedSettings> {
  const { data } = await api.get<CVEFeedSettings>('/cve/feed-settings/')
  return data
}

// ── CVE catalog ───────────────────────────────────────────────────────────────
export interface CVECatalogEntry {
  id: number
  cve_id: string
  description: string
  severity: 'critical' | 'high' | 'medium' | 'low' | 'none'
  cvss_score: string | null
  source: string
  cisa_kev: boolean
  affected_platforms: string[]
  affected_device_count: number
  published_at: string | null
  source_url: string
}

export interface CVESummary {
  total: number
  critical: number
  high: number
  medium: number
  low: number
  kev_count: number
  affected_devices: number
  patched: number
  inventory_platforms: string[]
  last_synced_at: string | null
  last_sync_status: string
  last_sync_summary: Record<string, unknown>
}

export async function fetchCVEs(
  params: { severity?: string; search?: string; ordering?: string; platform?: string; inventory_only?: boolean } = {},
): Promise<CVECatalogEntry[]> {
  const { data } = await api.get<CVECatalogEntry[] | Paginated<CVECatalogEntry>>('/cve/cves/', {
    params: { ordering: '-cvss_score', page_size: 200, ...params },
  })
  return unwrap(data)
}

export async function fetchCVESummary(inventoryOnly = true): Promise<CVESummary> {
  const { data } = await api.get<CVESummary>('/cve/cves/summary/', { params: { inventory_only: inventoryOnly } })
  return data
}

export async function triggerCVESync(): Promise<{ status: string }> {
  const { data } = await api.post<{ status: string }>('/cve/cves/sync/')
  return data
}

export async function setDeviceCvePatched(id: number, isPatched: boolean): Promise<DeviceCVE> {
  const { data } = await api.patch<DeviceCVE>(`/cve/device-cves/${id}/`, { is_patched: isPatched })
  return data
}

export async function saveCVEFeedSettings(payload: CVEFeedSettingsWrite): Promise<CVEFeedSettings> {
  const { data } = await api.put<CVEFeedSettings>('/cve/feed-settings/', payload)
  return data
}

// ── User profile & preferences ───────────────────────────────────────────────

export interface UserPreferences {
  theme: 'light' | 'dark' | 'system'
  log_default_time_range: '15m' | '1h' | '4h' | '12h' | '24h' | '7d' | 'all'
  log_default_page_size: number
  log_auto_refresh: boolean
  devices_default_columns: string[]
  devices_page_size: number
  timezone: string
  date_format: 'iso' | 'us' | 'eu'
  email_alerts: boolean
  slack_user_id: string
  discord_user_id: string
}

export interface Me {
  username: string
  email: string
  first_name: string
  last_name: string
  role: string
  is_superuser: boolean
  preferences: UserPreferences
}

export async function fetchMe(): Promise<Me> {
  const { data } = await api.get<Me>('/users/me/')
  return data
}

export async function updateMe(payload: Partial<Pick<Me, 'email' | 'first_name' | 'last_name'>>): Promise<Me> {
  const { data } = await api.put<Me>('/users/me/', payload)
  return data
}

export async function fetchPreferences(): Promise<UserPreferences> {
  const { data } = await api.get<UserPreferences>('/users/me/preferences/')
  return data
}

export async function savePreferences(payload: Partial<UserPreferences>): Promise<UserPreferences> {
  const { data } = await api.put<UserPreferences>('/users/me/preferences/', payload)
  return data
}

export async function changePassword(
  current_password: string,
  new_password: string,
): Promise<{ access?: string; refresh?: string }> {
  const { data } = await api.post<{ access?: string; refresh?: string }>(
    '/users/me/change-password/', { current_password, new_password },
  )
  return data
}

// ── Admin user management (Settings → Users) ─────────────────────────────────

export type UserRole = 'admin' | 'engineer' | 'viewer' | 'api'

export interface AdminUser {
  id: number
  username: string
  email: string
  first_name: string
  last_name: string
  role: UserRole
  is_active: boolean
  is_superuser: boolean
  last_login: string | null
  date_joined: string
}

export interface NewUser {
  username: string
  email?: string
  role: UserRole
  password: string
}

export async function fetchUsers(): Promise<AdminUser[]> {
  const { data } = await api.get<AdminUser[] | Paginated<AdminUser>>('/users/')
  return unwrap(data)
}

export async function createUser(payload: NewUser): Promise<AdminUser> {
  const { data } = await api.post<AdminUser>('/users/', payload)
  return data
}

export async function updateUser(id: number, payload: Partial<AdminUser>): Promise<AdminUser> {
  const { data } = await api.patch<AdminUser>(`/users/${id}/`, payload)
  return data
}

export async function deleteUser(id: number): Promise<void> {
  await api.delete(`/users/${id}/`)
}

// ── Device discovery (Settings → Discovery) ──────────────────────────────────

export type DiscoveryMethod = 'ping_snmp' | 'ping' | 'passive' | 'topology' | 'scan' | 'import'
export type DiscoveryStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface DiscoveryJob {
  id: number
  name: string
  method: DiscoveryMethod
  status: DiscoveryStatus
  subnets: string[]
  allowed_subnets: string[]
  excluded_subnets: string[]
  max_depth: number
  max_devices: number
  rate_limit_pps: number
  devices_found: number
  pending_count: number
  seed_device_hostname: string | null
  credential_profile: number | null
  credential_profile_name: string | null
  site: number | null
  site_name: string | null
  progress_current: number
  progress_total: number
  progress_message: string
  progress_pct: number
  ips_scanned: number
  error_message: string
  created_at: string
}

export interface DiscoveryProgress {
  status: DiscoveryStatus
  progress_pct: number
  progress_current: number
  progress_total: number
  progress_message: string
  ips_scanned: number
  devices_found: number
  elapsed_seconds: number
  error_message: string
}

export interface NewDiscoveryJob {
  name: string
  method: DiscoveryMethod
  subnets?: string[]
  allowed_subnets?: string[]
  excluded_subnets?: string[]
  credential_profile?: number | null
  site?: number | null
  max_devices?: number
  rate_limit_pps?: number
}

export interface DiscoveredDevice {
  id: number
  job: number
  source_ip: string
  detection_methods: string[]
  responds_to: Record<string, boolean>
  confidence_score: number
  discovered_hostname: string
  discovered_vendor: string
  discovered_platform: string
  device_category: 'network_device' | 'endpoint' | 'server' | 'printer' | 'unknown'
  os_detected: string
  os_accuracy: number | null
  status: 'pending' | 'approved' | 'rejected'
  already_exists: boolean
  existing_device_id: number | null
  existing_device_hostname: string | null
}

export async function fetchDiscoveryJobs(): Promise<DiscoveryJob[]> {
  const { data } = await api.get<DiscoveryJob[] | Paginated<DiscoveryJob>>('/devices/discovery/jobs/')
  return unwrap(data)
}

export async function createDiscoveryJob(payload: NewDiscoveryJob): Promise<DiscoveryJob> {
  const { data } = await api.post<DiscoveryJob>('/devices/discovery/jobs/', payload)
  return data
}

export async function updateDiscoveryJob(id: number, payload: Partial<NewDiscoveryJob>): Promise<DiscoveryJob> {
  const { data } = await api.patch<DiscoveryJob>(`/devices/discovery/jobs/${id}/`, payload)
  return data
}

export async function runDiscoveryJob(id: number): Promise<DiscoveryJob> {
  const { data } = await api.post<DiscoveryJob>(`/devices/discovery/jobs/${id}/run/`)
  return data
}

export async function restartDiscoveryJob(id: number): Promise<DiscoveryJob> {
  const { data } = await api.post<DiscoveryJob>(`/devices/discovery/jobs/${id}/restart/`)
  return data
}

export async function cancelDiscoveryJob(id: number): Promise<DiscoveryJob> {
  const { data } = await api.post<DiscoveryJob>(`/devices/discovery/jobs/${id}/cancel/`)
  return data
}

export async function deleteDiscoveryJob(id: number): Promise<void> {
  await api.delete(`/devices/discovery/jobs/${id}/`)
}

export async function fetchDiscoveryProgress(id: number): Promise<DiscoveryProgress> {
  const { data } = await api.get<DiscoveryProgress>(`/devices/discovery/jobs/${id}/progress/`)
  return data
}

export async function fetchJobDiscovered(jobId: number): Promise<DiscoveredDevice[]> {
  const { data } = await api.get<DiscoveredDevice[]>(`/devices/discovery/jobs/${jobId}/discovered/`)
  return unwrap(data)
}

export interface ApproveResult {
  device: { id: number; hostname: string }
  already_exists?: boolean
}

export async function approveDiscoveredDevice(
  id: number,
  opts: { credentialProfileId?: number | null; platform?: string } = {},
): Promise<ApproveResult> {
  const body: Record<string, unknown> = {}
  if (opts.credentialProfileId != null) body.credential_profile = opts.credentialProfileId
  if (opts.platform) body.platform = opts.platform
  const { data } = await api.post<ApproveResult>(`/devices/discovery/discovered/${id}/approve/`, body)
  return data
}

export async function rejectDiscoveredDevice(id: number): Promise<void> {
  await api.post(`/devices/discovery/discovered/${id}/reject/`)
}

export interface MonitoredInterface {
  id: number
  if_index: number | null
  if_name: string
  if_description: string
  if_speed_mbps: number | null
  if_type: string
  lldp_neighbor_hostname: string | null
  lldp_neighbor_port: string | null
  lldp_neighbor_desc: string | null
  lldp_neighbor_device_id: number | null
  poll_traffic: boolean
  poll_errors: boolean
  poll_status: boolean
  collection_method: 'auto' | 'snmp' | 'gnmi'
  last_status: string
  alert_on_down: boolean
  alert_on_up: boolean
  alert_severity: 'critical' | 'high' | 'medium' | 'low'
  consecutive_polls_before_alert: number
}

export interface InterfaceAlertConfig {
  if_names: string[]
  alert_on_down?: boolean
  alert_on_up?: boolean
  alert_severity?: 'critical' | 'high' | 'medium' | 'low'
  consecutive_polls_before_alert?: number
}

export async function saveInterfaceAlertConfig(deviceId: number, payload: InterfaceAlertConfig): Promise<MonitoredInterface[]> {
  const { data } = await api.post<MonitoredInterface[]>(`/devices/${deviceId}/interfaces/alert-config/`, payload)
  return data
}

export interface DiscoveredInterface {
  if_index: number | null
  if_name: string
  if_description: string
  if_speed_mbps: number | null
  if_type: string
  oper_status: string
  admin_status: string
  lldp_neighbor_hostname: string | null
  lldp_neighbor_port: string | null
  lldp_neighbor_desc: string | null
  auto_select: boolean
  collection_method: 'snmp' | 'gnmi'
}

export async function fetchTelemetryConfig(deviceId: number): Promise<TelemetryConfig> {
  const { data } = await api.get<TelemetryConfig>(`/devices/${deviceId}/telemetry-config/`)
  return data
}

export async function saveTelemetryConfig(deviceId: number, payload: Partial<TelemetryConfig>): Promise<TelemetryConfig> {
  const { data } = await api.put<TelemetryConfig>(`/devices/${deviceId}/telemetry-config/`, payload)
  return data
}

export async function discoverInterfaces(deviceId: number): Promise<{ count: number; auto_selected: number; interfaces: DiscoveredInterface[]; error?: string }> {
  const { data } = await api.post(`/devices/${deviceId}/interfaces/discover/`)
  return data
}

export async function fetchMonitoredInterfaces(deviceId: number): Promise<MonitoredInterface[]> {
  const { data } = await api.get<MonitoredInterface[] | Paginated<MonitoredInterface>>(`/devices/${deviceId}/interfaces/`)
  return unwrap(data)
}

export async function saveMonitoredInterfaces(deviceId: number, interfaces: Record<string, unknown>[]): Promise<MonitoredInterface[]> {
  const { data } = await api.post<MonitoredInterface[]>(`/devices/${deviceId}/interfaces/`, { interfaces })
  return data
}

export interface GeneratedConfig {
  platform: string
  vendor: string
  collector_ip: string
  snmpv3?: boolean
  snmp_warning?: string
  sections: Record<string, { enabled: boolean; config: string | null }>
  full_config: string
}

export interface ConfigPushRecord {
  id: number
  sections: string[]
  success: boolean
  output: string
  errors: string[]
  pushed_by_username: string | null
  created_at: string
}

export async function generateTelemetryConfig(deviceId: number): Promise<GeneratedConfig> {
  const { data } = await api.get<GeneratedConfig>(`/devices/${deviceId}/telemetry-config/generate/`)
  return data
}

export async function pushTelemetryConfig(deviceId: number, sections: string[]): Promise<{ success: boolean; pushed_sections: string[]; output: string; errors: string[] }> {
  const { data } = await api.post(`/devices/${deviceId}/telemetry-config/push/`, { sections })
  return data
}

export async function fetchPushHistory(deviceId: number): Promise<ConfigPushRecord[]> {
  const { data } = await api.get<ConfigPushRecord[]>(`/devices/${deviceId}/telemetry-config/push/`)
  return data
}

// ── Sites ────────────────────────────────────────────────────────────────────

export type SiteType = 'datacenter' | 'campus' | 'branch' | 'remote' | 'cloud'

export interface Site {
  id: number
  name: string
  slug: string
  description: string
  location: string
  site_type: SiteType
  address: string
  city: string
  state: string
  country: string
  latitude: string | null
  longitude: string | null
  parent_site: number | null
  parent_site_name: string | null
  contact_name: string
  contact_email: string
  contact_phone: string
  default_collector: number | null
  notes: string
  device_count: number
  created_at: string
  updated_at: string
}

export type SitePayload = Partial<Omit<Site, 'id' | 'slug' | 'parent_site_name' | 'device_count' | 'created_at' | 'updated_at'>> & { name: string }

export async function fetchSites(): Promise<Site[]> {
  const { data } = await api.get<Site[] | Paginated<Site>>('/sites/')
  return unwrap(data)
}

export async function fetchSite(id: number): Promise<Site> {
  const { data } = await api.get<Site>(`/sites/${id}/`)
  return data
}

export async function saveSite(payload: SitePayload, id?: number): Promise<Site> {
  const { data } = id
    ? await api.patch<Site>(`/sites/${id}/`, payload)
    : await api.post<Site>('/sites/', payload)
  return data
}

export async function deleteSite(id: number): Promise<void> {
  await api.delete(`/sites/${id}/`)
}

export async function fetchSiteDevices(id: number): Promise<Device[]> {
  const { data } = await api.get<Device[]>(`/sites/${id}/devices/`)
  return data
}

// ── Device detail ────────────────────────────────────────────────────────────

// Full device record from GET /api/devices/{id}/ (detail serializer = all fields).
export interface DeviceDetail {
  id: number
  hostname: string
  // Display-only hostname (domain suffix optionally stripped); SSH/SNMP/syslog
  // still use `hostname`. Always returned by the serializer.
  display_hostname: string
  // When the hostname was last verified against the network (SNMP sysName / DNS).
  hostname_verified_at?: string | null
  ip_address: string
  management_ip: string | null
  vendor: string
  model: string
  platform: string
  os_version: string
  serial_number: string
  status: string
  site: number | null
  role: DeviceRole | null
  groups: number[]
  credential_profile: number | null
  last_seen?: string | null
  is_reachable?: boolean
  consecutive_failures?: number
  last_reachability_check?: string | null
  collector_name?: string | null
  collector_ip?: string | null
  collector_status?: string | null
  notes: string
  created_at: string
  updated_at: string
}

export interface DeviceCreatePayload {
  hostname: string
  ip_address: string
  management_ip?: string | null
  vendor?: string
  model?: string
  platform?: string
  os_version?: string
  serial_number?: string
  status?: string
  site?: number | null
  role_id?: number | null
  collector?: number | null
  credential_profile?: number | null
  notes?: string
}

export async function fetchDevice(id: number): Promise<DeviceDetail> {
  const { data } = await api.get<DeviceDetail>(`/devices/${id}/`)
  return data
}

export async function createDevice(payload: DeviceCreatePayload): Promise<DeviceDetail> {
  const { data } = await api.post<DeviceDetail>('/devices/', payload)
  return data
}

export interface VersionCheck {
  current_version: string
  current_commit: string
  latest_commit: string | null
  latest_version: string | null
  update_available: boolean
  commits_behind: number
  release_notes_url: string
}
// Update check — returns null on any failure so the UI just hides the badge.
export async function fetchVersionCheck(): Promise<VersionCheck | null> {
  try {
    const { data } = await api.get<VersionCheck>('/version/check/')
    return data
  } catch {
    return null
  }
}

export interface ReachabilitySummaryPoint { time: string; active: number; unreachable: number }
export interface ReachabilitySummary {
  period: string
  total_devices: number
  data: ReachabilitySummaryPoint[]
}
// Fleet active/unreachable counts over time (dashboard "Device Status Over Time").
export async function fetchReachabilitySummary(period = '1h'): Promise<ReachabilitySummary> {
  const { data } = await api.get<ReachabilitySummary>('/devices/reachability-summary/', { params: { period } })
  return data
}

export interface PlatformOption { value: string; label: string }
// Supported device platforms, sourced from the backend (Device.Platform) so new
// platforms appear in dropdowns without a frontend change.
export async function fetchDevicePlatforms(): Promise<PlatformOption[]> {
  const { data } = await api.get<PlatformOption[]>('/devices/platforms/')
  return Array.isArray(data) ? data : []
}

export async function setDeviceCollector(id: number, collector: number | null): Promise<DeviceDetail> {
  const { data } = await api.patch<DeviceDetail>(`/devices/${id}/`, { collector })
  return data
}

// ── Device roles ─────────────────────────────────────────────────────────────

export interface DeviceRolePayload {
  name: string
  color: string
  description?: string
  icon?: string
}

export async function fetchDeviceRoles(): Promise<DeviceRole[]> {
  const { data } = await api.get<DeviceRole[] | Paginated<DeviceRole>>('/devices/roles/')
  return unwrap(data)
}

export async function createDeviceRole(payload: DeviceRolePayload): Promise<DeviceRole> {
  const { data } = await api.post<DeviceRole>('/devices/roles/', payload)
  return data
}

export async function updateDeviceRole(id: number, payload: Partial<DeviceRolePayload>): Promise<DeviceRole> {
  const { data } = await api.patch<DeviceRole>(`/devices/roles/${id}/`, payload)
  return data
}

export async function deleteDeviceRole(id: number): Promise<void> {
  await api.delete(`/devices/roles/${id}/`)
}

// ── Hostname Rules ──────────────────────────────────────────────────────────────

export type HostnameRuleType = 'role' | 'site' | 'both'

export interface HostnameRule {
  id: number
  name: string
  pattern: string
  rule_type: HostnameRuleType
  role: number | null
  role_name: string | null
  role_color: string | null
  site: number | null
  site_name: string | null
  priority: number
  enabled: boolean
  created_at?: string
  updated_at?: string
}

export interface HostnameRulePayload {
  name: string
  pattern: string
  rule_type: HostnameRuleType
  role?: number | null
  site?: number | null
  priority?: number
  enabled?: boolean
}

export async function fetchHostnameRules(): Promise<HostnameRule[]> {
  const { data } = await api.get<HostnameRule[] | Paginated<HostnameRule>>('/devices/hostname-rules/')
  return unwrap(data)
}

export async function createHostnameRule(payload: HostnameRulePayload): Promise<HostnameRule> {
  const { data } = await api.post<HostnameRule>('/devices/hostname-rules/', payload)
  return data
}

export async function updateHostnameRule(id: number, payload: Partial<HostnameRulePayload>): Promise<HostnameRule> {
  const { data } = await api.patch<HostnameRule>(`/devices/hostname-rules/${id}/`, payload)
  return data
}

export async function deleteHostnameRule(id: number): Promise<void> {
  await api.delete(`/devices/hostname-rules/${id}/`)
}

export async function testHostnameRule(pattern: string, hostnames: string[]): Promise<{ hostname: string; matches: boolean }[]> {
  const { data } = await api.post<{ hostname: string; matches: boolean }[]>(
    '/devices/hostname-rules/test/', { pattern, hostnames })
  return data
}

// Apply hostname rules to all devices missing role/site (or force overwrite).
export async function applyHostnameRulesBulk(force = false): Promise<{ updated: number; skipped: number }> {
  const { data } = await api.post<{ updated: number; skipped: number }>(
    '/devices/apply-rules/', { force })
  return data
}

export interface HostnameRulePreviewRoleRef { id: number; name: string; color: string }
export interface HostnameRulePreviewSiteRef { id: number; name: string }

export interface HostnameRulePreviewUpdate {
  device_id: number
  hostname: string
  current_role: HostnameRulePreviewRoleRef | null
  new_role: HostnameRulePreviewRoleRef | null
  current_site: HostnameRulePreviewSiteRef | null
  new_site: HostnameRulePreviewSiteRef | null
}

export interface HostnameRulePreviewSkip {
  device_id: number
  hostname: string
  reason: string
}

export interface HostnameRulePreview {
  would_update: HostnameRulePreviewUpdate[]
  would_skip: HostnameRulePreviewSkip[]
  summary: { total_devices: number; would_update: number; would_skip: number }
}

// Dry-run the bulk apply — what would change, without saving.
export async function previewHostnameRules(force = false): Promise<HostnameRulePreview> {
  const { data } = await api.post<HostnameRulePreview>('/devices/hostname-rules/preview/', { force })
  return data
}

// Apply hostname rules to a single device.
export async function applyHostnameRulesToDevice(id: number, force = false): Promise<{ role_assigned: boolean; site_assigned: boolean }> {
  const { data } = await api.post<{ role_assigned: boolean; site_assigned: boolean }>(
    `/devices/${id}/apply-rules/`, { force })
  return data
}

// Assign (or, with null, clear) a device's role.
export async function setDeviceRole(id: number, role_id: number | null): Promise<DeviceDetail> {
  const { data } = await api.patch<DeviceDetail>(`/devices/${id}/`, { role_id })
  return data
}

// Assign (or, with null, unassign) a device to a site.
export async function setDeviceSite(id: number, site: number | null): Promise<DeviceDetail> {
  const { data } = await api.patch<DeviceDetail>(`/devices/${id}/`, { site })
  return data
}

// Full-resource update (PUT). Send all writable fields so none are reset.
export async function updateDevice(
  id: number, payload: DeviceCreatePayload & { groups?: number[] },
): Promise<DeviceDetail> {
  const { data } = await api.put<DeviceDetail>(`/devices/${id}/`, payload)
  return data
}

export async function deleteDevice(id: number): Promise<void> {
  await api.delete(`/devices/${id}/`)
}

export interface TestConnectionResult {
  reachable: boolean
  open_ports: number[]
  banner: string
  vendor: string | null
  platform: string | null
  os_version: string | null
  model: string | null
  detail: string
}

export async function testConnection(ip: string): Promise<TestConnectionResult> {
  const { data } = await api.post<TestConnectionResult>('/devices/test-connection/', { ip })
  return data
}

export interface DetectPlatformResult {
  detected: boolean
  device_type?: string
  vendor?: string
  platform?: string
  os_version?: string | null
  hostname?: string | null
  model?: string | null
  serial?: string | null
  confidence?: 'high' | 'medium' | 'low'
  all_matches?: string[]
  error?: string
  best_guess?: string | null
}

export async function detectPlatform(ip: string, credentialProfileId: number): Promise<DetectPlatformResult> {
  const { data } = await api.post<DetectPlatformResult>('/devices/detect-platform/', {
    ip, credential_profile_id: credentialProfileId,
  })
  return data
}

// Create a site inline (used by the add-device wizard).
export async function createSite(payload: { name: string; location?: string }): Promise<Site> {
  const { data } = await api.post<Site>('/devices/sites/', payload)
  return data
}

// DRF serializes DecimalField as a string — keep them as string and parse in UI.
export interface RiskScore {
  id: number
  device: number
  hostname: string
  score: string
  cve_score: string
  compliance_score: string
  lifecycle_score: string
  anomaly_score: string
  last_computed_at: string
}

export async function fetchDeviceRiskScore(deviceId: number): Promise<RiskScore | null> {
  const { data } = await api.get<RiskScore[] | Paginated<RiskScore>>('/security/risk-scores/', { params: { device: String(deviceId) } })
  return unwrap(data)[0] ?? null
}

export interface ComplianceResult {
  id: number
  device: number
  policy: number
  rule: number
  outcome: 'pass' | 'fail' | 'error'
  detail: string
  created_at: string
}

export async function fetchComplianceResults(deviceId: number): Promise<ComplianceResult[]> {
  const { data } = await api.get<ComplianceResult[] | Paginated<ComplianceResult>>('/compliance/results/', { params: { device: String(deviceId) } })
  return unwrap(data)
}

// ── Template-based compliance ───────────────────────────────────────────────────

export interface ComplianceFinding {
  type: 'MISSING' | 'EXTRA' | 'DRIFT' | 'ERROR'
  severity: string
  line: string
  expected: string | null
  actual: string | null
  context?: string
}

export interface ComplianceTemplateResult {
  id: number
  device: number
  device_hostname: string | null
  template: number
  template_name: string | null
  status: 'compliant' | 'non_compliant' | 'error' | 'skipped'
  score: number | null
  checked_at: string
  config_snapshot: number | null
  findings: ComplianceFinding[]
  missing_count: number
  extra_count: number
  drift_count: number
  remediation: string
}

export interface DeviceComplianceResponse {
  overall_score: number | null
  results: ComplianceTemplateResult[]
}

export async function fetchDeviceCompliance(deviceId: number): Promise<DeviceComplianceResponse> {
  const { data } = await api.get<DeviceComplianceResponse>(`/devices/${deviceId}/compliance/`)
  return data
}

export async function runComplianceCheck(body: { device_id?: number; template_id?: number }): Promise<{ checked: number; compliant: number; non_compliant: number; error: number }> {
  const { data } = await api.post('/compliance/check/', body)
  return data
}

export interface ComplianceTemplate {
  id: number
  name: string
  description: string
  role: number | null
  role_name: string | null
  platform: string
  site: number | null
  site_name: string | null
  template_content: string
  variables: Record<string, unknown>
  enabled: boolean
  created_at?: string
  updated_at?: string
}

export interface ComplianceTemplatePayload {
  name: string
  description?: string
  role?: number | null
  platform?: string
  site?: number | null
  template_content: string
  variables?: Record<string, unknown>
  enabled?: boolean
}

export async function fetchComplianceTemplates(): Promise<ComplianceTemplate[]> {
  const { data } = await api.get<ComplianceTemplate[] | Paginated<ComplianceTemplate>>('/compliance/templates/')
  return unwrap(data)
}

export async function createComplianceTemplate(payload: ComplianceTemplatePayload): Promise<ComplianceTemplate> {
  const { data } = await api.post<ComplianceTemplate>('/compliance/templates/', payload)
  return data
}

export async function updateComplianceTemplate(id: number, payload: Partial<ComplianceTemplatePayload>): Promise<ComplianceTemplate> {
  const { data } = await api.patch<ComplianceTemplate>(`/compliance/templates/${id}/`, payload)
  return data
}

export async function deleteComplianceTemplate(id: number): Promise<void> {
  await api.delete(`/compliance/templates/${id}/`)
}

export async function previewComplianceTemplate(id: number, deviceId: number): Promise<{ rendered: string; hostname: string } | { error: string }> {
  const { data } = await api.post(`/compliance/templates/${id}/preview/`, { device_id: deviceId })
  return data
}

export interface DeviceCVE {
  id: number
  device: number
  cve: number
  cve_id: string
  severity: 'critical' | 'high' | 'medium' | 'low' | 'none'
  cvss_score: string | null
  cve_description?: string
  source_url?: string
  cisa_kev?: boolean
  match_type?: 'exact_version' | 'version_range' | 'keyword' | 'unverified'
  match_detail?: string
  published_at?: string | null
  is_patched: boolean
  patched_at: string | null
  created_at: string
}

export async function fetchDeviceCVEs(deviceId: number): Promise<DeviceCVE[]> {
  const { data } = await api.get<DeviceCVE[] | Paginated<DeviceCVE>>('/cve/device-cves/', { params: { device: String(deviceId) } })
  return unwrap(data)
}

export type MilestoneType = 'eos' | 'eosm' | 'eoss' | 'eol'

export interface LifecycleMilestone {
  id: number
  device: number
  hostname: string
  milestone_type: MilestoneType
  milestone_date: string
  source: string
  notes: string
}

export async function fetchLifecycleMilestones(deviceId: number): Promise<LifecycleMilestone[]> {
  const { data } = await api.get<LifecycleMilestone[] | Paginated<LifecycleMilestone>>('/lifecycle/milestones/', { params: { device: String(deviceId) } })
  return unwrap(data)
}

// AlertEvent has no device FK — device identity lives in the labels JSON.
export interface AlertEvent {
  id: number
  rule: number
  rule_name: string
  severity: AlertSeverity
  state: 'firing' | 'resolved'
  labels: Record<string, string>
  annotations: Record<string, string>
  resolved_at: string | null
  created_at: string
}

// Fetch recent alert events whose labels reference this device (by hostname or
// id). Includes resolved events so the device's recent-alerts list isn't empty
// once issues clear.
export async function fetchDeviceAlerts(deviceId: number, hostname: string): Promise<AlertEvent[]> {
  const { data } = await api.get<AlertEvent[] | Paginated<AlertEvent>>('/alerts/events/', { params: { ordering: '-created_at', resolved: 'all' } })
  const events = unwrap(data)
  return events.filter((e) => {
    const l = e.labels || {}
    return l.hostname === hostname || l.device === hostname ||
      String(l.device_id) === String(deviceId) || String(l.device) === String(deviceId)
  })
}

// ── Service checks (agentless synthetic monitoring) ──────────────────────────
export type CheckType =
  | 'http' | 'https' | 'tcp' | 'udp' | 'icmp' | 'dns' | 'tls'
  | 'smtp' | 'ftp' | 'ssh' | 'ssh_banner' | 'ldap' | 'radius' | 'tacacs' | 'custom'
export type CheckStatus = 'up' | 'down' | 'degraded' | 'unknown'

export interface ServiceCheck {
  id: number
  name: string
  check_type: CheckType
  host: string
  port: number | null
  effective_port: number | null
  interval_seconds: number
  timeout_seconds: number
  device: number | null
  device_hostname: string | null
  site: number | null
  site_name: string | null
  is_active: boolean
  is_enabled: boolean
  current_status: CheckStatus
  last_checked: string | null
  last_status_change: string | null
  consecutive_failures: number
  failures_before_alert: number
  alert_on_down: boolean
  alert_on_recovery: boolean
  alert_on_degraded: boolean
  config: Record<string, unknown>
  response_time_warning_ms: number | null
  response_time_critical_ms: number | null
  last_response_ms: number | null
  last_details: Record<string, unknown>
  tags: string[]
  notes: string
  created_at: string
}

export type ServiceCheckPayload = Partial<Omit<ServiceCheck,
  'id' | 'effective_port' | 'device_hostname' | 'site_name' | 'current_status' |
  'last_checked' | 'last_status_change' | 'consecutive_failures' | 'created_at'>>
  & { name: string; check_type: CheckType; host: string }

export interface CheckResult {
  id: number
  check: number
  status: CheckStatus
  response_time_ms: number | null
  checked_at: string
  error: string
  details: Record<string, unknown>
}

export interface CheckSummary {
  up: number
  down: number
  degraded: number
  unknown: number
  total: number
}

export async function fetchChecks(params?: Record<string, string>): Promise<ServiceCheck[]> {
  const { data } = await api.get<ServiceCheck[] | Paginated<ServiceCheck>>('/checks/', { params })
  return unwrap(data)
}

export async function fetchCheckSummary(): Promise<CheckSummary> {
  const { data } = await api.get<CheckSummary>('/checks/summary/')
  return data
}

export async function saveCheck(payload: ServiceCheckPayload, id?: number): Promise<ServiceCheck> {
  const { data } = id
    ? await api.patch<ServiceCheck>(`/checks/${id}/`, payload)
    : await api.post<ServiceCheck>('/checks/', payload)
  return data
}

export async function deleteCheck(id: number): Promise<void> {
  await api.delete(`/checks/${id}/`)
}

export async function runCheckNow(id: number): Promise<CheckResult & { current_status: CheckStatus }> {
  const { data } = await api.post(`/checks/${id}/run-now/`)
  return data
}

export interface CheckResultsResponse {
  check_id: number
  check_name: string
  period: string
  summary: { total: number; up: number; down: number; degraded: number; uptime_pct: number | null }
  results: CheckResult[]
}

export async function fetchCheckResults(id: number, period = '24h'): Promise<CheckResultsResponse> {
  const { data } = await api.get<CheckResultsResponse>(`/checks/${id}/results/`, { params: { period } })
  return data
}

// ── Alert routing (apps/alerting, Stage 1) ───────────────────────────────────
export interface AlertTeam {
  id: number
  name: string
  description: string
  color: string
  slack_webhook_url: string
  discord_webhook_url: string
  member_count: number
  created_at: string
  updated_at: string
}

export interface EscalationStep {
  id: number
  policy: number
  step_number: number
  delay_minutes: number
  notify_team: number | null
  notify_user: number | null
  notify_type: string
}

export interface EscalationPolicy {
  id: number
  name: string
  description: string
  team: number
  repeat_interval_minutes: number
  steps: EscalationStep[]
  created_at: string
  updated_at: string
}

export interface AlertRoute {
  id: number
  name: string
  description: string
  is_active: boolean
  priority: number
  match_severity: string[]
  match_source: string[]
  match_device_tags: string[]
  match_check_types: string[]
  match_sites: number[]
  escalation_policy: number
  policy_name: string
  suppress_during_maintenance: boolean
  suppress_if_parent_down: boolean
  created_at: string
}

export async function fetchTeams(): Promise<AlertTeam[]> {
  const { data } = await api.get<AlertTeam[] | Paginated<AlertTeam>>('/alerting/teams/')
  return unwrap(data)
}
export async function saveTeam(payload: Partial<AlertTeam> & { name: string }, id?: number): Promise<AlertTeam> {
  const { data } = id ? await api.patch(`/alerting/teams/${id}/`, payload) : await api.post('/alerting/teams/', payload)
  return data
}
export async function deleteTeam(id: number): Promise<void> { await api.delete(`/alerting/teams/${id}/`) }

export interface TeamMember {
  id: number
  team: number
  user: number
  username: string
  email: string
  full_name: string
  role: 'member' | 'lead' | 'manager'
  notify_email: boolean
  notify_sms: boolean
  notify_slack: boolean
  notify_discord: boolean
}

export type TeamMemberPatch = Partial<Pick<TeamMember,
  'role' | 'notify_email' | 'notify_slack' | 'notify_discord'>>

export async function fetchTeamMembers(teamId: number): Promise<TeamMember[]> {
  const { data } = await api.get<TeamMember[]>(`/alerting/teams/${teamId}/members/`)
  return Array.isArray(data) ? data : []
}
export async function addTeamMember(
  teamId: number, body: { user: number; role: string } & TeamMemberPatch,
): Promise<TeamMember> {
  const { data } = await api.post(`/alerting/teams/${teamId}/members/`, body)
  return data
}
export async function updateTeamMember(
  teamId: number, userId: number, patch: TeamMemberPatch,
): Promise<TeamMember> {
  const { data } = await api.patch(`/alerting/teams/${teamId}/members/${userId}/`, patch)
  return data
}
export async function removeTeamMember(teamId: number, userId: number): Promise<void> {
  await api.delete(`/alerting/teams/${teamId}/members/${userId}/`)
}
export async function testTeamDiscord(id: number): Promise<{ ok: boolean; error: string }> {
  const { data } = await api.post(`/alerting/teams/${id}/test-discord/`)
  return data
}

export async function fetchPolicies(): Promise<EscalationPolicy[]> {
  const { data } = await api.get<EscalationPolicy[] | Paginated<EscalationPolicy>>('/alerting/policies/')
  return unwrap(data)
}
export async function savePolicy(payload: { name: string; team: number; description?: string }, id?: number): Promise<EscalationPolicy> {
  const { data } = id ? await api.patch(`/alerting/policies/${id}/`, payload) : await api.post('/alerting/policies/', payload)
  return data
}

export async function fetchRoutes(): Promise<AlertRoute[]> {
  const { data } = await api.get<AlertRoute[] | Paginated<AlertRoute>>('/alerting/routes/')
  return unwrap(data)
}
export async function saveRoute(payload: Partial<AlertRoute> & { name: string; escalation_policy: number }, id?: number): Promise<AlertRoute> {
  const { data } = id ? await api.patch(`/alerting/routes/${id}/`, payload) : await api.post('/alerting/routes/', payload)
  return data
}
export async function deleteRoute(id: number): Promise<void> { await api.delete(`/alerting/routes/${id}/`) }
export async function testRoute(sample: { severity?: string; source?: string; check_type?: string; site?: number }): Promise<{ matched: boolean; route: AlertRoute | null }> {
  const { data } = await api.post('/alerting/routes/test/', sample)
  return data
}

// ── Alert notification timeline + acknowledge (apps/alerting Stage 2) ─────────
export interface AlertNotificationRecord {
  id: number
  alert_event: number
  channel: string
  status: string
  sent_at: string | null
  username: string | null
  error: string
  created_at: string
}

export async function fetchAlertNotifications(eventId: number): Promise<AlertNotificationRecord[]> {
  const { data } = await api.get<AlertNotificationRecord[] | Paginated<AlertNotificationRecord>>(
    '/alerting/notifications/', { params: { alert_event: eventId, ordering: 'created_at' } })
  return unwrap(data)
}

export async function acknowledgeAlertEvent(eventId: number, note?: string, snoozeMinutes?: number): Promise<void> {
  await api.post(`/alerts/events/${eventId}/acknowledge/`, { note, snooze_minutes: snoozeMinutes })
}
export async function snoozeAlertEvent(eventId: number, minutes: number): Promise<void> {
  await api.post(`/alerts/events/${eventId}/snooze/`, { minutes })
}

// ── Maintenance windows (apps/alerting) ──────────────────────────────────────
export interface MaintenanceWindow {
  id: number
  name: string
  description: string
  start_time: string
  end_time: string
  timezone: string
  recurrence: 'none' | 'daily' | 'weekly' | 'monthly'
  recurrence_days: string[]
  devices: number[]
  sites: number[]
  device_names: string[]
  site_names: string[]
  check_types: string[]
  severity_filter: string[]
  is_active: boolean
  is_currently_active: boolean
  created_at: string
}

export type MaintenanceWindowPayload = Partial<Omit<MaintenanceWindow,
  'id' | 'is_currently_active' | 'device_names' | 'site_names' | 'created_at'>>
  & { name: string; start_time: string; end_time: string }

export async function fetchMaintenanceWindows(): Promise<MaintenanceWindow[]> {
  const { data } = await api.get<MaintenanceWindow[] | Paginated<MaintenanceWindow>>('/alerting/maintenance/')
  return unwrap(data)
}
export async function fetchActiveMaintenance(): Promise<MaintenanceWindow[]> {
  const { data } = await api.get<MaintenanceWindow[]>('/alerting/maintenance/active/')
  return Array.isArray(data) ? data : []
}
export async function saveMaintenanceWindow(payload: MaintenanceWindowPayload, id?: number): Promise<MaintenanceWindow> {
  const { data } = id
    ? await api.patch(`/alerting/maintenance/${id}/`, payload)
    : await api.post('/alerting/maintenance/', payload)
  return data
}
export async function deleteMaintenanceWindow(id: number): Promise<void> { await api.delete(`/alerting/maintenance/${id}/`) }
export async function endMaintenanceNow(id: number): Promise<void> { await api.post(`/alerting/maintenance/${id}/end-now/`) }

// ── MIB files (Settings → MIB Files) ─────────────────────────────────────────

// `path` is the relative dir: "standard", "vendor/cisco", "vendor/community",
// or "custom". Only custom MIBs are deletable.
export interface MibInfo {
  name: string
  file: string
  path: string
  objects: number
  loaded: boolean
  deletable: boolean
}

export interface MibUploadResult {
  success: boolean
  objects_loaded: number
  module: string
  warnings: string[]
}

export async function fetchMibs(): Promise<MibInfo[]> {
  const { data } = await api.get<{ mibs: MibInfo[] }>('/mibs/')
  return data.mibs ?? []
}

// Multipart upload — let axios set the Content-Type (with boundary) for FormData.
export async function uploadMib(file: File): Promise<MibUploadResult> {
  const form = new FormData()
  form.append('file', file)
  // Override the instance's JSON default so axios sets multipart + boundary.
  const { data } = await api.post<MibUploadResult>('/mibs/upload/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function deleteMib(name: string): Promise<void> {
  await api.delete(`/mibs/${encodeURIComponent(name)}/`)
}

export interface OidResolution {
  oid: string
  name: string | null
  resolved: boolean
}

export async function resolveOid(oid: string): Promise<OidResolution> {
  const { data } = await api.get<OidResolution>(`/mibs/resolve/${encodeURIComponent(oid)}/`)
  return data
}

// ── SSO providers ──────────────────────────────────────────────────────────────

/** Public shape returned by GET /sso/providers/ to anonymous callers. */
export interface SSOProviderPublic {
  id: number
  name: string
  provider: string
  is_default: boolean
  login_url: string
}

/** Full admin shape (GET as admin / retrieve / create / update). */
export interface SSOProvider {
  id: number
  name: string
  provider: string
  client_id: string
  has_secret: boolean
  tenant_id: string
  okta_domain: string
  saml_metadata_url: string
  is_enabled: boolean
  is_default: boolean
  allow_signup: boolean
  default_role: string
  allowed_domains: string[]
  created_at: string
  updated_at: string
}

export interface SSOProviderInput {
  name: string
  provider: string
  client_id?: string
  client_secret?: string
  tenant_id?: string
  okta_domain?: string
  saml_metadata_url?: string
  is_enabled?: boolean
  is_default?: boolean
  allow_signup?: boolean
  default_role?: string
  allowed_domains?: string[]
}

export interface SSOTestResult {
  valid: boolean
  error: string | null
}

/** Public list for the login page (no auth required; enabled providers only). */
export async function fetchSSOProvidersPublic(): Promise<SSOProviderPublic[]> {
  const { data } = await api.get('/sso/providers/')
  return Array.isArray(data) ? data : (data.results ?? [])
}

/** Admin list — full fields incl. disabled providers (admin auth required). */
export async function fetchSSOProviders(): Promise<SSOProvider[]> {
  const { data } = await api.get('/sso/providers/')
  return Array.isArray(data) ? data : (data.results ?? [])
}

export async function createSSOProvider(input: SSOProviderInput): Promise<SSOProvider> {
  const { data } = await api.post<SSOProvider>('/sso/providers/', input)
  return data
}

export async function updateSSOProvider(id: number, input: Partial<SSOProviderInput>): Promise<SSOProvider> {
  const { data } = await api.patch<SSOProvider>(`/sso/providers/${id}/`, input)
  return data
}

export async function deleteSSOProvider(id: number): Promise<void> {
  await api.delete(`/sso/providers/${id}/`)
}

export async function testSSOProvider(id: number): Promise<SSOTestResult> {
  const { data } = await api.post<SSOTestResult>(`/sso/providers/${id}/test/`, {})
  return data
}

// ── Onboarding (Get Started wizard) ─────────────────────────────────────────────

export interface OnboardingStatus {
  show_onboarding: boolean
  reasons: { has_devices: boolean; user_completed: boolean }
}

export async function fetchOnboardingStatus(): Promise<OnboardingStatus> {
  const { data } = await api.get<OnboardingStatus>('/onboarding/status/')
  return data
}

export async function completeOnboarding(): Promise<void> {
  await api.post('/onboarding/complete/', {})
}

// ── Flows (NetFlow / sFlow / IPFIX analytics) ───────────────────────────────────

export interface FlowRecord {
  id: string
  timestamp: string
  exporter_ip: string
  protocol_version: string
  src_ip: string
  dst_ip: string
  src_port: number
  dst_port: number
  ip_protocol: number
  protocol: string          // resolved name: TCP / UDP / ICMP / …
  service: string | null    // well-known service for the port, if any
  bytes: number
  packets: number
  duration_ms: number | null
  input_if: number | null
  output_if: number | null
  tcp_flags: number | null
  tos: number | null
}

export interface FlowListResponse {
  count: number
  results: FlowRecord[]
  ip?: string
}

export async function fetchFlows(params: Record<string, string>): Promise<FlowListResponse> {
  const { data } = await api.get<FlowListResponse>('/flows/', { params })
  return data
}

export async function searchFlows(ip: string, window = '24h', limit = 100): Promise<FlowListResponse> {
  const { data } = await api.get<FlowListResponse>('/flows/search/', {
    params: { ip, window, limit: String(limit) },
  })
  return data
}

export interface TopTalker {
  src_ip: string
  flows: number
  bytes: number
  packets: number
}

export interface TopTalkersResponse {
  by: string
  window: string
  results: TopTalker[]
}

export async function fetchTopTalkers(params: Record<string, string>): Promise<TopTalkersResponse> {
  const { data } = await api.get<TopTalkersResponse>('/flows/top-talkers/', { params })
  return data
}

export interface FlowProtocol {
  protocol: string
  flows: number
  bytes: number
}

export interface FlowTimePoint {
  timestamp: string
  bytes: number
}

export interface FlowSummary {
  window: string
  total_flows: number
  total_bytes: number
  total_packets: number
  unique_src_ips: number
  unique_dst_ips: number
  top_protocols: FlowProtocol[]
  bytes_over_time: FlowTimePoint[]
}

export async function fetchFlowSummary(params: Record<string, string>): Promise<FlowSummary> {
  const { data } = await api.get<FlowSummary>('/flows/summary/', { params })
  return data
}

// Per-device flow charts (device Flows tab): inbound/outbound traffic over time,
// TCP/UDP/ICMP/Other mix, and the top conversations involving the device.
export interface FlowTrafficPoint {
  timestamp: string
  inbound_bytes: number
  outbound_bytes: number
}

export interface FlowProtocolMix {
  protocol: string
  bytes: number
  flows: number
  pct: number
}

export interface FlowConversation {
  src_ip: string
  dst_ip: string
  bytes: number
  packets: number
  flows: number
}

export interface FlowDeviceSummary {
  window: string
  traffic_over_time: FlowTrafficPoint[]
  protocol_mix: FlowProtocolMix[]
  top_conversations: FlowConversation[]
}

export async function fetchFlowDeviceSummary(params: Record<string, string>): Promise<FlowDeviceSummary> {
  const { data } = await api.get<FlowDeviceSummary>('/flows/device-summary/', { params })
  return data
}

// Traffic-flow Sankey: top conversations as nodes (unique IPs) + links (bytes).
export interface FlowSankeyNode {
  name: string
}

export interface FlowSankeyLink {
  source: string
  target: string
  value: number
  bytes: number
  packets: number
  flows: number
}

export interface FlowSankeyData {
  window: string
  nodes: FlowSankeyNode[]
  links: FlowSankeyLink[]
}

export async function fetchFlowSankey(params: Record<string, string>): Promise<FlowSankeyData> {
  const { data } = await api.get<FlowSankeyData>('/flows/sankey/', { params })
  return data
}
