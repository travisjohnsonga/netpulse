import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface JWTPayload {
  username?: string
  role?: string
  name?: string
  email?: string
  must_change_password?: boolean
  exp: number
}

function decodePayload(token: string): JWTPayload | null {
  try {
    return JSON.parse(atob(token.split('.')[1])) as JWTPayload
  } catch {
    return null
  }
}

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  username: string | null
  role: string | null
  name: string | null
  email: string | null
  mustChangePassword: boolean
  isAuthenticated: boolean
  setTokens: (access: string, refresh: string) => void
  setAccessToken: (access: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      username: null,
      role: null,
      name: null,
      email: null,
      mustChangePassword: false,
      isAuthenticated: false,

      setTokens: (access, refresh) => {
        const payload = decodePayload(access)
        set({
          accessToken: access,
          refreshToken: refresh,
          username: payload?.username ?? null,
          role: payload?.role ?? null,
          name: payload?.name || null,
          email: payload?.email || null,
          mustChangePassword: payload?.must_change_password === true,
          isAuthenticated: true,
        })
      },

      setAccessToken: (access) => {
        const payload = decodePayload(access)
        set({
          accessToken: access,
          username: payload?.username ?? null,
          role: payload?.role ?? null,
          name: payload?.name || null,
          email: payload?.email || null,
          mustChangePassword: payload?.must_change_password === true,
          isAuthenticated: true,
        })
      },

      logout: () =>
        set({
          accessToken: null,
          refreshToken: null,
          username: null,
          role: null,
          name: null,
          email: null,
          mustChangePassword: false,
          isAuthenticated: false,
        }),
    }),
    {
      name: 'netpulse-auth',
      partialize: (s) => ({
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        username: s.username,
        role: s.role,
        name: s.name,
        email: s.email,
        mustChangePassword: s.mustChangePassword,
        isAuthenticated: s.isAuthenticated,
      }),
    },
  ),
)
