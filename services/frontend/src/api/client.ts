import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL || '/api'

export const api = axios.create({
  baseURL: API_URL,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

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

export async function checkHealth(): Promise<HealthStatus> {
  const { data } = await api.get<HealthStatus>('/health/')
  return data
}

export async function fetchDevices(
  params?: Record<string, string>,
): Promise<DeviceListResponse> {
  const { data } = await api.get<DeviceListResponse>('/devices/', { params })
  return data
}

export async function fetchAlerts(): Promise<Alert[]> {
  const { data } = await api.get<Alert[]>('/alerts/events/')
  return data
}
