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
  services: Record<string, boolean>
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

export async function fetchAlerts(): Promise<Alert[]> {
  const { data } = await api.get<Alert[]>('/alerts/events/')
  return data
}

export async function fetchTopology(): Promise<TopologyData> {
  const { data } = await api.get<TopologyData>('/devices/topology/')
  return data
}

export async function acknowledgeAlert(id: number): Promise<void> {
  await api.patch(`/alerts/events/${id}/`, { state: 'acknowledged' })
}
