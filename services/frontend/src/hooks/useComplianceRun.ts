import { useEffect, useRef, useState } from 'react'
import {
  runComplianceAll,
  fetchComplianceRunStatus,
  type ComplianceRunStatus,
} from '../api/client'

/**
 * Drives the background fleet compliance run: start it (optionally for a subset
 * of devices) and poll its progress until done. Picks up an already-running
 * fleet run on mount so the progress shows even after a reload / on another page.
 */
export function useComplianceRunAll(onDone?: () => void) {
  const [status, setStatus] = useState<ComplianceRunStatus | null>(null)
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
        const s = await fetchComplianceRunStatus()
        setStatus(s)
        if (!s.running) { stop(); doneRef.current?.() }
      } catch { /* keep last status, try again next tick */ }
    }, 1500)
  }

  const start = async (deviceIds?: number[]) => {
    setStarting(true)
    try {
      const s = await runComplianceAll(deviceIds)
      setStatus(s)
      poll()
    } catch (e) {
      // 409 → a run is already in progress; just follow it.
      const resp = (e as { response?: { status?: number; data?: ComplianceRunStatus } }).response
      if (resp?.status === 409 && resp.data) { setStatus(resp.data); poll() }
      else throw e
    } finally {
      setStarting(false)
    }
  }

  useEffect(() => {
    fetchComplianceRunStatus()
      .then((s) => { setStatus(s); if (s.running) poll() })
      .catch(() => {})
    return stop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { status, start, starting, isRunning: !!status?.running }
}
