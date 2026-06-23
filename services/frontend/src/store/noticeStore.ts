import { create } from 'zustand'

/**
 * Lightweight transient-notice store. Today it backs the global "Not authorized"
 * banner raised by the API client on any 403 (RBAC Track 2 Phase C) — so a
 * deep-link or mid-session capability change surfaces a clear, non-destructive
 * message instead of a silently broken panel. The API 403 stays the real
 * security boundary; this is just how we tell the user about it.
 */
interface NoticeState {
  forbidden: string | null
  showForbidden: (message: string) => void
  clearForbidden: () => void
}

export const useNoticeStore = create<NoticeState>((set) => ({
  forbidden: null,
  showForbidden: (message) => set({ forbidden: message }),
  clearForbidden: () => set({ forbidden: null }),
}))
