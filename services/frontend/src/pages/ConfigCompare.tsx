import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { diffLines } from 'diff'
import { api, fetchDevices, fetchConfigDiff, type Device } from '../api/client'
import DiffViewer from '../components/DiffViewer'

interface ConfigRow {
  id: number
  collected_at: string
  collected_by: string
  content: string
  rendered_content?: string  // CLI for display (AOS-CX JSON → CLI); else === content
  content_hash: string
}

type Mode = 'unified' | 'side'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500'

function fetchConfigs(deviceId: number): Promise<ConfigRow[]> {
  return api
    .get(`/configbackup/configs/?device=${deviceId}&ordering=-collected_at`)
    .then((r) => (Array.isArray(r.data) ? r.data : r.data.results ?? []) as ConfigRow[])
}

export default function ConfigCompare() {
  const [params, setParams] = useSearchParams()
  // Originating device when navigated from a device's Configuration tab — drives
  // the "← Back to device" link, the title, and (when no explicit versions are
  // given) auto-selecting that device's latest two snapshots. Captured once: the
  // URL-sync effect below rewrites the query string, so re-reading would lose it.
  const [fromDeviceId] = useState<number | null>(() => numParam(params, 'device'))
  const [leftDevice, setLeftDevice] = useState<number | null>(numParam(params, 'left') ?? fromDeviceId)
  const [leftVersion, setLeftVersion] = useState<number | null>(numParam(params, 'leftVersion'))
  const [rightDevice, setRightDevice] = useState<number | null>(numParam(params, 'right') ?? fromDeviceId)
  const [rightVersion, setRightVersion] = useState<number | null>(numParam(params, 'rightVersion'))
  const [mode, setMode] = useState<Mode>('unified')

  // Keep the URL shareable.
  useEffect(() => {
    const next: Record<string, string> = {}
    if (fromDeviceId) next.device = String(fromDeviceId)
    if (leftDevice) next.left = String(leftDevice)
    if (leftVersion) next.leftVersion = String(leftVersion)
    if (rightDevice) next.right = String(rightDevice)
    if (rightVersion) next.rightVersion = String(rightVersion)
    setParams(next, { replace: true })
  }, [fromDeviceId, leftDevice, leftVersion, rightDevice, rightVersion, setParams])

  const { data: devices = [] } = useQuery({
    queryKey: ['devices-all'],
    queryFn: () => fetchDevices({ page_size: '500' }).then((d) => d.results),
  })

  const { data: leftConfigs = [] } = useQuery({
    queryKey: ['cmp-configs', leftDevice],
    queryFn: () => fetchConfigs(leftDevice as number),
    enabled: !!leftDevice,
  })
  const { data: rightConfigs = [] } = useQuery({
    queryKey: ['cmp-configs', rightDevice],
    queryFn: () => fetchConfigs(rightDevice as number),
    enabled: !!rightDevice,
  })

  // Auto-select when arriving with ?device= but no explicit versions: left =
  // latest snapshot, right = the one immediately prior. Diff then runs on its
  // own. Scoped to the originating device so manually switching a selector to a
  // different device doesn't get its version auto-picked out from under the user.
  useEffect(() => {
    if (!fromDeviceId) return
    if (leftDevice === fromDeviceId && leftVersion == null && leftConfigs.length) setLeftVersion(leftConfigs[0].id)
    if (rightDevice === fromDeviceId && rightVersion == null && rightConfigs.length > 1) setRightVersion(rightConfigs[1].id)
  }, [fromDeviceId, leftDevice, rightDevice, leftConfigs, rightConfigs, leftVersion, rightVersion])

  const leftCfg = leftConfigs.find((c) => c.id === leftVersion)
  const rightCfg = rightConfigs.find((c) => c.id === rightVersion)

  const leftLabel = label(devices, leftDevice, leftCfg)
  const rightLabel = label(devices, rightDevice, rightCfg)

  const ready = !!leftCfg && !!rightCfg

  // Backend-computed structured diff (used by the unified DiffViewer).
  const { data: diff, isLoading: diffLoading, isError: diffError } = useQuery({
    queryKey: ['config-diff', leftVersion, rightVersion],
    queryFn: () => fetchConfigDiff({ left: leftVersion as number, right: rightVersion as number }),
    enabled: ready && mode === 'unified',
  })

  const fromDeviceName = fromDeviceId ? devices.find((d) => d.id === fromDeviceId)?.hostname : null

  return (
    <div className="space-y-4">
      <div>
        {fromDeviceId && (
          <Link to={`/devices/${fromDeviceId}?tab=configuration`} className="text-sm text-blue-600 hover:text-blue-800">
            &larr; Back to {fromDeviceName || 'device'}
          </Link>
        )}
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mt-1">
          Configuration Compare{fromDeviceName ? ` — ${fromDeviceName}` : ''}
        </h1>
      </div>

      {/* Selectors */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Selector
          title="Left" devices={devices}
          device={leftDevice} setDevice={(d) => { setLeftDevice(d); setLeftVersion(null) }}
          configs={leftConfigs} version={leftVersion} setVersion={setLeftVersion}
        />
        <Selector
          title="Right" devices={devices}
          device={rightDevice} setDevice={(d) => { setRightDevice(d); setRightVersion(null) }}
          configs={rightConfigs} version={rightVersion} setVersion={setRightVersion}
        />
      </div>

      {/* Mode toggle */}
      <div className="flex gap-1 bg-gray-100 dark:bg-gray-700 rounded-lg p-1 w-fit">
        {([['unified', 'Unified'], ['side', 'Side by Side']] as [Mode, string][]).map(([m, lbl]) => (
          <button key={m} onClick={() => setMode(m)}
            className={clsx('px-3 py-1.5 text-sm rounded-md font-medium', mode === m ? 'bg-white dark:bg-gray-800 shadow-sm text-blue-700' : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100')}>
            {lbl}
          </button>
        ))}
      </div>

      {/* Diff */}
      {!ready ? (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 py-16 text-center text-sm text-gray-500 dark:text-gray-400">
          Select a device and version on both sides to compare.
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-4 py-2 border-b border-gray-200 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400 flex justify-between">
            <span className="text-red-600 font-mono truncate">− {leftLabel}</span>
            <span className="text-green-600 font-mono truncate">+ {rightLabel}</span>
          </div>
          {mode === 'unified' && (
            diffLoading ? (
              <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
            ) : diffError || !diff ? (
              <div className="py-12 text-center text-sm text-red-600 dark:text-red-400">Failed to compute the diff. The API may be unavailable.</div>
            ) : (
              <DiffViewer diff={diff} leftLabel={leftLabel} rightLabel={rightLabel} />
            )
          )}
          {mode === 'side' && <SideBySide left={leftCfg!.rendered_content ?? leftCfg!.content} right={rightCfg!.rendered_content ?? rightCfg!.content} />}
        </div>
      )}
    </div>
  )
}

// ── helpers ────────────────────────────────────────────────────────────────

function numParam(params: URLSearchParams, key: string): number | null {
  const v = params.get(key)
  return v ? Number(v) : null
}

function label(devices: Device[], deviceId: number | null, cfg?: ConfigRow): string {
  const host = devices.find((d) => d.id === deviceId)?.hostname ?? `device ${deviceId ?? '?'}`
  if (!cfg) return host
  return `${host} @ ${new Date(cfg.collected_at).toLocaleString()}`
}

function Selector({ title, devices, device, setDevice, configs, version, setVersion }: {
  title: string
  devices: Device[]
  device: number | null
  setDevice: (d: number | null) => void
  configs: ConfigRow[]
  version: number | null
  setVersion: (v: number | null) => void
}) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">{title}</h3>
      <div>
        <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Device</label>
        <select className={inputCls} value={device ?? ''} onChange={(e) => setDevice(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Select device…</option>
          {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Version</label>
        <select className={inputCls} value={version ?? ''} disabled={!device} onChange={(e) => setVersion(e.target.value ? Number(e.target.value) : null)}>
          <option value="">{configs.length ? 'Select version…' : 'No snapshots'}</option>
          {configs.map((c, i) => (
            <option key={c.id} value={c.id}>
              {(i === 0 ? `v${configs.length} (current)` : `v${configs.length - i}`)} · {new Date(c.collected_at).toLocaleString()} · {c.collected_by}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}

interface Aligned { left: string | null; right: string | null; kind: 'same' | 'add' | 'del' }

function alignedRows(left: string, right: string): Aligned[] {
  const rows: Aligned[] = []
  for (const part of diffLines(left, right)) {
    const lines = part.value.split('\n')
    if (lines[lines.length - 1] === '') lines.pop()
    for (const ln of lines) {
      if (part.added) rows.push({ left: null, right: ln, kind: 'add' })
      else if (part.removed) rows.push({ left: ln, right: null, kind: 'del' })
      else rows.push({ left: ln, right: ln, kind: 'same' })
    }
  }
  return rows
}

function SideBySide({ left, right }: { left: string; right: string }) {
  const rows = useMemo(() => alignedRows(left, right), [left, right])
  return (
    <div className="overflow-auto max-h-[32rem] text-xs font-mono">
      <table className="w-full border-collapse">
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className={clsx('px-3 py-0.5 align-top whitespace-pre-wrap w-1/2 border-r border-gray-100 dark:border-gray-700',
                r.kind === 'del' && 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400')}>{r.left ?? ''}</td>
              <td className={clsx('px-3 py-0.5 align-top whitespace-pre-wrap w-1/2',
                r.kind === 'add' && 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400')}>{r.right ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

