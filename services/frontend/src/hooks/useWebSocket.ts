import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '../store/authStore'

// Derive the WebSocket base from the current page origin so both Vite's dev
// proxy (/ws → ws://localhost:8001) and the nginx proxy in production route
// correctly. Override with VITE_WS_URL for non-proxied deployments.
function wsBase(): string {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL as string
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}`
}

export function useWebSocket(path: string) {
  const ws = useRef<WebSocket | null>(null)
  const [lastMessage, setLastMessage] = useState<unknown>(null)
  const [connected, setConnected] = useState(false)
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const attempts = useRef(0)

  useEffect(() => {
    let cancelled = false

    const connect = () => {
      if (cancelled) return
      try {
        // The backend requires JWT auth on WS connections. Browsers can't set
        // headers on a WS handshake, so the access token rides as the second
        // subprotocol ("bearer", "<jwt>"); the server validates + echoes it.
        const token = useAuthStore.getState().accessToken
        ws.current = token
          ? new WebSocket(`${wsBase()}${path}`, ['bearer', token])
          : new WebSocket(`${wsBase()}${path}`)
      } catch {
        // Unsupported environment (SSR, etc.) — skip silently
        return
      }

      ws.current.onopen = () => {
        if (cancelled) return
        attempts.current = 0
        setConnected(true)
      }

      ws.current.onclose = () => {
        if (cancelled) return
        setConnected(false)
        // Exponential back-off: 1s, 2s, 4s … capped at 30s
        const delay = Math.min(1000 * 2 ** attempts.current, 30_000)
        attempts.current += 1
        reconnectTimeout.current = setTimeout(connect, delay)
      }

      ws.current.onerror = () => {
        ws.current?.close()
      }

      ws.current.onmessage = (e: MessageEvent<string>) => {
        if (cancelled) return
        try {
          setLastMessage(JSON.parse(e.data) as unknown)
        } catch {
          setLastMessage(e.data)
        }
      }
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current)
      ws.current?.close()
    }
  }, [path])

  return { lastMessage, connected }
}
