import { useEffect, useRef, useState } from 'react'
import {
  runReenrichAll,
  fetchReenrichStatus,
  type ReenrichStatus,
} from '../api/client'

/**
 * Drives the background fleet identity re-enrichment: start it (optionally for a
 * subset of devices) and poll its progress until done. Picks up an already-running
 * run on mount so progress shows even after a reload / on another page. Mirrors
 * useComplianceRunAll.
 */
export function useReenrichAll(onDone?: () => void) {
  const [status, setStatus] = useState<ReenrichStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const pollRef = useRef<number | null>(null)
  const doneRef = useRef(onDone)
  doneRef.current = onDone

  const stop = () => {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null }
  }

  const poll = () => {
    stop()
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchReenrichStatus()
        setStatus(s)
        if (!s.running) { stop(); doneRef.current?.() }
      } catch { /* keep last status, try again next tick */ }
    }, 2000)
  }

  const start = async (deviceIds?: number[]) => {
    setStarting(true)
    try {
      const s = await runReenrichAll(deviceIds)
      setStatus(s)
      poll()
    } catch (e) {
      // 409 → a run is already in progress; just follow it.
      const resp = (e as { response?: { status?: number; data?: ReenrichStatus } }).response
      if (resp?.status === 409 && resp.data) { setStatus(resp.data); poll() }
      else throw e
    } finally {
      setStarting(false)
    }
  }

  useEffect(() => {
    fetchReenrichStatus()
      .then((s) => { setStatus(s); if (s.running) poll() })
      .catch(() => {})
    return stop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { status, start, starting, isRunning: !!status?.running }
}
