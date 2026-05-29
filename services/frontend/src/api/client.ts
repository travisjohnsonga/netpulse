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
  platform: string
  vendor: string
  status: 'active' | 'inactive' | 'pending' | 'unreachable'
  last_seen: string | null
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
  rule_name: string
  device: string
  fired_at: string
  state: 'firing' | 'acknowledged' | 'resolved'
  message: string
}

export interface TopologyNode {
  id: string
  label: string
  type: string
  site: string | null
  status: string
  risk_score: number
}

export interface TopologyEdge {
  source: string
  target: string
  capacity_gbps: number
  utilization_pct: number
  utilization_color: string
  in_bps: number
  out_bps: number
  latency_ms: number | null
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

export async function fetchDevices(params?: Record<string, string>): Promise<DeviceListResponse> {
  const { data } = await api.get<DeviceListResponse>('/devices/', { params })
  return data
}

// DRF returns paginated { count, results } by default.
// Defensively coerce to array regardless of shape.
type MaybePaginated<T> = T[] | { results: T[]; count: number; next: string | null; previous: string | null }

export async function fetchAlerts(): Promise<Alert[]> {
  const { data } = await api.get<MaybePaginated<Alert>>('/alerts/events/')
  return Array.isArray(data) ? data : (data.results ?? [])
}

export async function checkInfraHealth(): Promise<InfraHealth> {
  const { data } = await api.get<InfraHealth>('/health/infrastructure/')
  return data
}

export async function fetchTopology(): Promise<TopologyData> {
  const { data } = await api.get<TopologyData>('/devices/topology/')
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

// Fetch recent alert events whose labels reference this device (by hostname or id).
export async function fetchDeviceAlerts(deviceId: number, hostname: string): Promise<AlertEvent[]> {
  const { data } = await api.get<AlertEvent[] | Paginated<AlertEvent>>('/alerts/events/', { params: { ordering: '-created_at' } })
  const events = unwrap(data)
  return events.filter((e) => {
    const l = e.labels || {}
    return l.hostname === hostname || l.device === hostname ||
      String(l.device_id) === String(deviceId) || String(l.device) === String(deviceId)
  })
}
