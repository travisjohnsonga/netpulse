/**
 * ChatOps in-UI chat state.
 *
 * A React context (not a Zustand store like the others) on purpose: it is mounted
 * ONCE around the authenticated app subtree in App.tsx (above the page <Routes>),
 * so the open/closed state and the full message history survive route navigation
 * — the panel and its state never live inside a page component and never reset
 * when you move between pages.
 *
 * Backend: POST /api/chatops/query/ via the shared `api` client (JWT + 401 refresh
 * handled by its interceptors). Replies are the structured IntentResult the chat
 * panel renders natively.
 */
import {
  createContext, useCallback, useContext, useMemo, useRef, useState,
  type ReactNode,
} from 'react'
import {
  chatOpsQuery, isChatOpsDenied, type ChatOpsResult,
} from '../api/client'

export interface UserMessage {
  id: string
  role: 'user'
  text: string
}

export interface SpaneMessage {
  id: string
  role: 'spane'
  result?: ChatOpsResult   // a structured answer
  denied?: string          // a policy denial guidance message
  error?: string           // a transport/unexpected failure message
}

export type ChatMessage = UserMessage | SpaneMessage

interface ChatOpsContextValue {
  open: boolean
  messages: ChatMessage[]
  loading: boolean
  openPanel: () => void
  closePanel: () => void
  toggle: () => void
  sendQuery: (text: string) => Promise<void>
}

const ChatOpsContext = createContext<ChatOpsContextValue | null>(null)

const GENERIC_ERROR =
  "Couldn't reach spane just now. Check your connection and try again."

export function ChatOpsProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const counter = useRef(0)

  const nextId = () => `m${++counter.current}`

  const openPanel = useCallback(() => setOpen(true), [])
  const closePanel = useCallback(() => setOpen(false), [])
  const toggle = useCallback(() => setOpen((o) => !o), [])

  const sendQuery = useCallback(async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || loading) return

    setMessages((m) => [...m, { id: nextId(), role: 'user', text: trimmed }])
    setLoading(true)
    try {
      const res = await chatOpsQuery(trimmed)
      const reply: SpaneMessage = isChatOpsDenied(res)
        ? { id: nextId(), role: 'spane', denied: res.message }
        : { id: nextId(), role: 'spane', result: res }
      setMessages((m) => [...m, reply])
    } catch {
      // Never surface raw errors/secrets; a plain, actionable message instead.
      setMessages((m) => [...m, { id: nextId(), role: 'spane', error: GENERIC_ERROR }])
    } finally {
      setLoading(false)
    }
  }, [loading])

  const value = useMemo<ChatOpsContextValue>(
    () => ({ open, messages, loading, openPanel, closePanel, toggle, sendQuery }),
    [open, messages, loading, openPanel, closePanel, toggle, sendQuery],
  )

  return <ChatOpsContext.Provider value={value}>{children}</ChatOpsContext.Provider>
}

export function useChatOps(): ChatOpsContextValue {
  const ctx = useContext(ChatOpsContext)
  if (!ctx) throw new Error('useChatOps must be used within a ChatOpsProvider')
  return ctx
}
