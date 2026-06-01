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

export interface InfraHealth {
  services: {
    postgres: boolean
    valkey: boolean
    nats: boolean
    influxdb: boolean
    opensearch: boolean
  }
}

export interface Device {
  id: number
  hostname: string
  ip_address: string
  management_ip: string | null
  platform: string
  vendor: string
  model: string
  os_version: string
  serial_number: string
  status: 'active' | 'inactive' | 'pending' | 'unreachable'
  site_name: string | null
  credential_profile: number | null
  last_seen: string | null
  is_reachable?: boolean
  consecutive_failures?: number
  last_reachability_check?: string | null
  unreachable_since?: string | null
  notes: string
  created_at: string
}

// Reachability state derived from is_reachable + last_seen recency.
export type Reachability = 'reachable' | 'degraded' | 'unreachable'
export function reachabilityOf(d: { is_reachable?: boolean; last_seen?: string | null }): Reachability {
  if (d.is_reachable === false) return 'unreachable'
  if (!d.last_seen) return 'unreachable'
  const age = (Date.now() - new Date(d.last_seen).getTime()) / 1000
  if (age > 300) return 'unreachable'
  if (age > 60) return 'degraded'
  return 'reachable'
}

function _ageStr(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

// Human explanation of the derived reachability state — used as the badge
// tooltip. The list-level state is derived purely from is_reachable + last_seen
// recency, so the reason reflects that (telemetry staleness / outage duration),
// not per-metric causes which the list doesn't load.
export function reachabilityReason(d: {
  is_reachable?: boolean; last_seen?: string | null
  unreachable_since?: string | null; consecutive_failures?: number
}): string {
  const reach = reachabilityOf(d)
  if (reach === 'unreachable') {
    if (d.unreachable_since) return `Unreachable — down for ${_ageStr(d.unreachable_since)}`
    if (d.is_reachable === false) {
      const f = d.consecutive_failures ? ` (${d.consecutive_failures} failed checks)` : ''
      return `Unreachable — last reachability check failed${f}`
    }
    if (!d.last_seen) return 'Unreachable — no successful contact yet'
    return `Unreachable — no telemetry for ${_ageStr(d.last_seen)}`
  }
  if (reach === 'degraded') {
    return `Degraded — telemetry stale, last seen ${d.last_seen ? _ageStr(d.last_seen) : '?'} ago (expected within 1m)`
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

// ── API calls ────────────────────────────────────────────────────────────────

export async function login(username: string, password: string): Promise<{ access: string; refresh: string }> {
  const { data } = await api.post<{ access: string; refresh: string }>('/auth/token/', { username, password })
  return data
}

export async function checkHealth(): Promise<HealthStatus> {
  const { data } = await api.get<HealthStatus>('/health/')
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
}

// Physical-sensor summary; empty {} for devices that report none (e.g. virtual).
export interface DeviceEnvironment {
  temperature_c?: number
  temperature_sensors?: number
  fan_sensors?: number
  power_sensors?: number
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

export async function pollDeviceNow(deviceId: number): Promise<{ status: string; device_id: number }> {
  const { data } = await api.post(`/devices/${deviceId}/poll-now/`)
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

export interface Collector {
  id: number
  name: string
  collector_ip: string | null
  site: number | null
  site_name?: string | null
  is_default: boolean
  device_count?: number
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
}

export async function fetchLogs(params: Record<string, string>): Promise<LogQueryResponse> {
  const { data } = await api.get<LogQueryResponse>('/logs/', { params })
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

export async function changePassword(current_password: string, new_password: string): Promise<void> {
  await api.post('/users/me/change-password/', { current_password, new_password })
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

export type DiscoveryMethod = 'passive' | 'topology' | 'scan' | 'import'
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
  created_at: string
}

export interface NewDiscoveryJob {
  name: string
  method: DiscoveryMethod
  subnets?: string[]
  allowed_subnets?: string[]
  excluded_subnets?: string[]
  credential_profile?: number | null
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
  status: 'pending' | 'approved' | 'rejected'
}

export async function fetchDiscoveryJobs(): Promise<DiscoveryJob[]> {
  const { data } = await api.get<DiscoveryJob[] | Paginated<DiscoveryJob>>('/devices/discovery/jobs/')
  return unwrap(data)
}

export async function createDiscoveryJob(payload: NewDiscoveryJob): Promise<DiscoveryJob> {
  const { data } = await api.post<DiscoveryJob>('/devices/discovery/jobs/', payload)
  return data
}

export async function deleteDiscoveryJob(id: number): Promise<void> {
  await api.delete(`/devices/discovery/jobs/${id}/`)
}

export async function fetchDiscoveredDevices(status = 'pending'): Promise<DiscoveredDevice[]> {
  const { data } = await api.get<DiscoveredDevice[] | Paginated<DiscoveredDevice>>(
    `/devices/discovery/discovered/?status=${status}`)
  return unwrap(data)
}

export async function approveDiscoveredDevice(id: number, credentialProfileId?: number | null): Promise<void> {
  await api.post(`/devices/discovery/discovered/${id}/approve/`,
    credentialProfileId != null ? { credential_profile: credentialProfileId } : {})
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
  ip_address: string
  management_ip: string | null
  vendor: string
  model: string
  platform: string
  os_version: string
  serial_number: string
  status: string
  site: number | null
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

export async function setDeviceCollector(id: number, collector: number | null): Promise<DeviceDetail> {
  const { data } = await api.patch<DeviceDetail>(`/devices/${id}/`, { collector })
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

export interface DeviceCVE {
  id: number
  device: number
  cve: number
  cve_id: string
  severity: 'critical' | 'high' | 'medium' | 'low' | 'none'
  cvss_score: string | null
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
  | 'smtp' | 'ftp' | 'ssh' | 'ssh_banner' | 'ldap' | 'custom'
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
