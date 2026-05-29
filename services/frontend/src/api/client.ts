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

export type CredentialType =
  | 'snmpv1' | 'snmpv2c' | 'snmpv3'
  | 'ssh_password' | 'ssh_key'
  | 'http_basic' | 'http_token' | 'http_apikey'
  | 'gnmi' | 'netconf'

export type CredentialPurpose =
  | 'snmp_polling' | 'ssh_config' | 'ssh_backup'
  | 'netconf' | 'gnmi' | 'http_api'

export type TestResult = 'untested' | 'success' | 'failure'

// Full profile (detail/create/update). Secret fields are write-only — they are
// accepted on write, forwarded to OpenBao, and never returned on read.
export interface CredentialProfile {
  id: number
  name: string
  credential_type: CredentialType
  description: string
  username: string
  auth_method: string
  port: number | null
  tls_enabled: boolean
  snmp_version: string
  snmp_security_level: string
  auth_protocol: string
  priv_protocol: string
  vault_path: string
  device_count: number
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
  credential_type: CredentialType
  username: string
  device_count: number
  last_tested: string | null
  last_test_result: TestResult
  created_at: string
}

// Write payload — metadata plus optional write-only secret fields.
export interface CredentialProfilePayload {
  name: string
  credential_type: CredentialType
  description?: string
  username?: string
  auth_method?: string
  port?: number | null
  tls_enabled?: boolean
  snmp_version?: string
  snmp_security_level?: string
  auth_protocol?: string
  priv_protocol?: string
  // write-only secrets (never echoed back)
  community?: string
  auth_password?: string
  priv_password?: string
  password?: string
  private_key?: string
  passphrase?: string
  token?: string
  api_key?: string
}

export interface CredentialTestResult {
  ip: string
  success: boolean
  message: string
  latency_ms: number | null
  port: number
}

export interface DeviceCredential {
  id: number
  device: number
  device_hostname: string
  credential: number
  credential_name: string
  credential_type: CredentialType
  purpose: CredentialPurpose
  is_primary: boolean
  last_used: string | null
  last_success: string | null
  failure_count: number
  notes: string
  created_at: string
  updated_at: string
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

export async function fetchCredentialDevices(id: number): Promise<DeviceCredential[]> {
  const { data } = await api.get<DeviceCredential[]>(`/credentials/${id}/devices/`)
  return data
}

// Device-scoped credential associations.
export async function fetchDeviceCredentials(deviceId: number): Promise<DeviceCredential[]> {
  const { data } = await api.get<DeviceCredential[] | Paginated<DeviceCredential>>(
    `/devices/${deviceId}/credentials/`,
  )
  return unwrap(data)
}

export async function addDeviceCredential(
  deviceId: number,
  payload: { credential: number; purpose: CredentialPurpose; is_primary?: boolean; notes?: string },
): Promise<DeviceCredential> {
  const { data } = await api.post<DeviceCredential>(
    `/devices/${deviceId}/credentials/`, payload,
  )
  return data
}

export async function removeDeviceCredential(
  deviceId: number, purpose: CredentialPurpose,
): Promise<void> {
  await api.delete(`/devices/${deviceId}/credentials/${purpose}/`)
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
