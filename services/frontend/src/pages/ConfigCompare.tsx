import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { createTwoFilesPatch, diffLines } from 'diff'
import { api, fetchDevices, type Device } from '../api/client'

interface ConfigRow {
  id: number
  collected_at: string
  collected_by: string
  content: string
  content_hash: string
}

type Mode = 'unified' | 'side' | 'summary'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500'

function fetchConfigs(deviceId: number): Promise<ConfigRow[]> {
  return api
    .get(`/configbackup/configs/?device=${deviceId}&ordering=-collected_at`)
    .then((r) => (Array.isArray(r.data) ? r.data : r.data.results ?? []) as ConfigRow[])
}

export default function ConfigCompare() {
  const [params, setParams] = useSearchParams()
  const [leftDevice, setLeftDevice] = useState<number | null>(numParam(params, 'left'))
  const [leftVersion, setLeftVersion] = useState<number | null>(numParam(params, 'leftVersion'))
  const [rightDevice, setRightDevice] = useState<number | null>(numParam(params, 'right'))
  const [rightVersion, setRightVersion] = useState<number | null>(numParam(params, 'rightVersion'))
  const [mode, setMode] = useState<Mode>('unified')

  // Keep the URL shareable.
  useEffect(() => {
    const next: Record<string, string> = {}
    if (leftDevice) next.left = String(leftDevice)
    if (leftVersion) next.leftVersion = String(leftVersion)
    if (rightDevice) next.right = String(rightDevice)
    if (rightVersion) next.rightVersion = String(rightVersion)
    setParams(next, { replace: true })
  }, [leftDevice, leftVersion, rightDevice, rightVersion, setParams])

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

  const leftCfg = leftConfigs.find((c) => c.id === leftVersion)
  const rightCfg = rightConfigs.find((c) => c.id === rightVersion)

  const leftLabel = label(devices, leftDevice, leftCfg)
  const rightLabel = label(devices, rightDevice, rightCfg)

  const ready = !!leftCfg && !!rightCfg

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Configuration Compare</h1>

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
      <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit">
        {([['unified', 'Unified'], ['side', 'Side by Side'], ['summary', 'Summary']] as [Mode, string][]).map(([m, lbl]) => (
          <button key={m} onClick={() => setMode(m)}
            className={clsx('px-3 py-1.5 text-sm rounded-md font-medium', mode === m ? 'bg-white shadow-sm text-blue-700' : 'text-gray-600 hover:text-gray-900')}>
            {lbl}
          </button>
        ))}
      </div>

      {/* Diff */}
      {!ready ? (
        <div className="bg-white rounded-lg border border-gray-200 py-16 text-center text-sm text-gray-500">
          Select a device and version on both sides to compare.
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-2 border-b border-gray-200 text-xs text-gray-500 flex justify-between">
            <span className="text-red-600 font-mono truncate">− {leftLabel}</span>
            <span className="text-green-600 font-mono truncate">+ {rightLabel}</span>
          </div>
          {mode === 'unified' && <Unified left={leftCfg!.content} right={rightCfg!.content} leftLabel={leftLabel} rightLabel={rightLabel} />}
          {mode === 'side' && <SideBySide left={leftCfg!.content} right={rightCfg!.content} />}
          {mode === 'summary' && <Summary left={leftCfg!.content} right={rightCfg!.content} />}
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
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      <div>
        <label className="block text-xs text-gray-500 mb-1">Device</label>
        <select className={inputCls} value={device ?? ''} onChange={(e) => setDevice(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Select device…</option>
          {devices.map((d) => <option key={d.id} value={d.id}>{d.hostname}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">Version</label>
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

function Unified({ left, right, leftLabel, rightLabel }: { left: string; right: string; leftLabel: string; rightLabel: string }) {
  const patch = useMemo(
    () => createTwoFilesPatch(leftLabel, rightLabel, left, right, '', '', { context: 3 }),
    [left, right, leftLabel, rightLabel],
  )
  return (
    <pre className="bg-gray-900 text-xs font-mono p-4 overflow-x-auto leading-relaxed max-h-[32rem]">
      {patch.split('\n').map((line, i) => (
        <div key={i} className={clsx(
          line.startsWith('+') && !line.startsWith('+++') && 'text-green-400',
          line.startsWith('-') && !line.startsWith('---') && 'text-red-400',
          line.startsWith('@@') && 'text-sky-400',
          (line.startsWith('+++') || line.startsWith('---') || line.startsWith('Index') || line.startsWith('===')) && 'text-gray-500',
          !/^[+\-@]/.test(line) && 'text-gray-300',
        )}>{line || ' '}</div>
      ))}
    </pre>
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
              <td className={clsx('px-3 py-0.5 align-top whitespace-pre-wrap w-1/2 border-r border-gray-100',
                r.kind === 'del' && 'bg-red-50 text-red-700')}>{r.left ?? ''}</td>
              <td className={clsx('px-3 py-0.5 align-top whitespace-pre-wrap w-1/2',
                r.kind === 'add' && 'bg-green-50 text-green-700')}>{r.right ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Summary({ left, right }: { left: string; right: string }) {
  const { added, removed, sections } = useMemo(() => {
    let added = 0, removed = 0, sections = 0
    for (const part of diffLines(left, right)) {
      const n = part.count ?? part.value.split('\n').filter(Boolean).length
      if (part.added) { added += n; sections++ }
      else if (part.removed) { removed += n; sections++ }
    }
    return { added, removed, sections }
  }, [left, right])

  return (
    <div className="p-6 grid grid-cols-3 gap-4 text-center">
      <Stat value={`+${added}`} label="lines added" color="text-green-600" />
      <Stat value={`-${removed}`} label="lines removed" color="text-red-600" />
      <Stat value={String(sections)} label="sections changed" color="text-gray-800" />
      {added === 0 && removed === 0 && (
        <p className="col-span-3 text-sm text-gray-500">The two configurations are identical.</p>
      )}
    </div>
  )
}

function Stat({ value, label, color }: { value: string; label: string; color: string }) {
  return (
    <div>
      <p className={clsx('text-3xl font-bold', color)}>{value}</p>
      <p className="text-xs text-gray-400 mt-1">{label}</p>
    </div>
  )
}
