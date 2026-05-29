import { useEffect, useRef, useState } from 'react'

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8001'

export function useWebSocket(path: string) {
  const ws = useRef<WebSocket | null>(null)
  const [lastMessage, setLastMessage] = useState<unknown>(null)
  const [connected, setConnected] = useState(false)
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let cancelled = false

    const connect = () => {
      if (cancelled) return
      ws.current = new WebSocket(`${WS_URL}${path}`)

      ws.current.onopen = () => {
        if (!cancelled) setConnected(true)
      }

      ws.current.onclose = () => {
        if (!cancelled) {
          setConnected(false)
          reconnectTimeout.current = setTimeout(connect, 3000)
        }
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
