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

export interface RbacRoleIdentity {
  name: string
  is_system: boolean
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
  // RBAC Track 2 Phase C: the user's effective capabilities + RBAC role identity,
  // resolved from GET /api/users/me/ (the JWT only carries the legacy `role`).
  // Drives capability-aware UI gating; the API 403 stays the real boundary.
  capabilities: string[]
  rbacRole: RbacRoleIdentity | null
  setTokens: (access: string, refresh: string) => void
  setAccessToken: (access: string) => void
  setCapabilities: (capabilities: string[], rbacRole: RbacRoleIdentity | null) => void
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
      capabilities: [],
      rbacRole: null,

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

      setCapabilities: (capabilities, rbacRole) => set({ capabilities, rbacRole }),

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
          capabilities: [],
          rbacRole: null,
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
        // Persisted so a page reload doesn't briefly hide nav/routes before the
        // /me refetch lands; the auth-init refetch then refreshes them.
        capabilities: s.capabilities,
        rbacRole: s.rbacRole,
      }),
    },
  ),
)

/** All effective capabilities for the current user (empty until /me resolves). */
export function useCapabilities(): string[] {
  return useAuthStore((s) => s.capabilities)
}

/**
 * True if the current user holds `capability`. Convenience for UI gating only —
 * the API 403 remains the authoritative security boundary. Superusers already
 * receive the full catalog from /me, so no client-side special-casing is needed.
 */
export function useHasCapability(capability: string): boolean {
  return useAuthStore((s) => s.capabilities.includes(capability))
}
