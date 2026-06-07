import { useCallback, useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { SectionHeader } from '../Settings'
import {
  fetchMibs, uploadMib, deleteMib, resolveOid,
  type MibInfo, type MibUploadResult, type OidResolution,
} from '../../api/client'
import { parseApiErrors } from '../../api/errors'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

function apiError(err: unknown, fallback: string): string {
  return parseApiErrors(err, fallback)
}

// Warnings can come back from a 400 error body as well as a 201 success body.
function apiWarnings(err: unknown): string[] {
  const e = err as { response?: { data?: { warnings?: string[] } } }
  return e?.response?.data?.warnings ?? []
}

type Category = 'standard' | 'vendor' | 'community' | 'custom'

function categoryOf(path: string): Category {
  if (path === 'standard') return 'standard'
  if (path === 'vendor/community') return 'community'
  if (path === 'custom') return 'custom'
  if (path.startsWith('vendor/')) return 'vendor'
  // Anything unexpected falls into custom so it's still visible.
  return 'custom'
}

// Last path segment, e.g. "vendor/cisco" → "cisco".
function vendorOf(path: string): string {
  const seg = path.split('/').filter(Boolean).pop() || 'other'
  return seg
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export default function Mibs() {
  const [mibs, setMibs] = useState<MibInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Upload state.
  const [uploading, setUploading] = useState(false)
  const [uploadResult, setUploadResult] = useState<MibUploadResult | null>(null)
  const [uploadError, setUploadError] = useState<{ message: string; warnings: string[] } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Per-MIB delete busy state.
  const [deleting, setDeleting] = useState<string | null>(null)

  // OID resolver state.
  const [oid, setOid] = useState('')
  const [resolving, setResolving] = useState(false)
  const [resolution, setResolution] = useState<OidResolution | null>(null)
  const [resolveErr, setResolveErr] = useState<string | null>(null)

  const flash = (msg: string) => { setNotice(msg); setTimeout(() => setNotice(null), 3000) }

  const load = useCallback((silent = false) => {
    if (!silent) setLoading(true)
    fetchMibs()
      .then((m) => { setMibs(m); setError(null) })
      .catch((e) => setError(apiError(e, 'Failed to load MIB files.')))
      .finally(() => { if (!silent) setLoading(false) })
  }, [])

  useEffect(() => { load() }, [load])

  const onUpload = async (file: File) => {
    setUploading(true); setUploadResult(null); setUploadError(null)
    try {
      const res = await uploadMib(file)
      setUploadResult(res)
      flash(`Loaded ${res.module} (${res.objects_loaded} object${res.objects_loaded === 1 ? '' : 's'})`)
      load(true)
    } catch (e) {
      setUploadError({ message: apiError(e, 'Upload failed.'), warnings: apiWarnings(e) })
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) onUpload(file)
  }

  const remove = async (m: MibInfo) => {
    if (!window.confirm(`Delete custom MIB "${m.name}"? This unloads it from the platform.`)) return
    setDeleting(m.name); setError(null)
    try { await deleteMib(m.name); flash(`Deleted ${m.name}`); load(true) }
    catch (e) { setError(apiError(e, 'Failed to delete MIB.')) }
    finally { setDeleting(null) }
  }

  const onResolve = async () => {
    const q = oid.trim()
    if (!q) return
    setResolving(true); setResolution(null); setResolveErr(null)
    try { setResolution(await resolveOid(q)) }
    catch (e) { setResolveErr(apiError(e, 'Failed to resolve OID.')) }
    finally { setResolving(false) }
  }

  const standard = mibs.filter((m) => categoryOf(m.path) === 'standard')
  const vendor = mibs.filter((m) => categoryOf(m.path) === 'vendor')
  const community = mibs.filter((m) => categoryOf(m.path) === 'community')
  const custom = mibs.filter((m) => categoryOf(m.path) === 'custom')

  // Per-vendor MIB counts for the Vendor section summary.
  const vendorCounts = vendor.reduce<Record<string, number>>((acc, m) => {
    const v = vendorOf(m.path)
    acc[v] = (acc[v] ?? 0) + 1
    return acc
  }, {})
  const vendorSummary = Object.entries(vendorCounts).sort((a, b) => a[0].localeCompare(b[0]))

  return (
    <div>
      <SectionHeader
        title="MIB Files"
        description="SNMP MIB modules used to resolve OIDs and decode traps. Upload custom MIBs (.my / .mib / .txt). Standard and vendor MIBs ship with the platform."
        action={
          <>
            <input ref={fileInputRef} type="file" accept=".my,.mib,.txt" className="hidden" onChange={onFileChange} />
            <button onClick={() => fileInputRef.current?.click()} disabled={uploading}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
              {uploading ? 'Uploading…' : '⬆ Upload MIB'}
            </button>
          </>
        }
      />

      {notice && <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-lg px-4 py-3 text-sm text-green-700 dark:text-green-400 mb-4">✅ {notice}</div>}
      {error && <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-red-700 dark:text-red-400 mb-4">{error}</div>}

      {/* Upload result / error */}
      {uploadResult && (
        <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-lg px-4 py-3 text-sm text-green-700 dark:text-green-400 mb-4">
          <p className="font-medium">Loaded {uploadResult.module} — {uploadResult.objects_loaded} object{uploadResult.objects_loaded === 1 ? '' : 's'}.</p>
          {uploadResult.warnings.length > 0 && (
            <ul className="list-disc list-inside mt-1 text-amber-600 dark:text-amber-400">
              {uploadResult.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
        </div>
      )}
      {uploadError && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3 text-sm text-red-700 dark:text-red-400 mb-4">
          <p className="font-medium">{uploadError.message}</p>
          {uploadError.warnings.length > 0 && (
            <ul className="list-disc list-inside mt-1">
              {uploadError.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* OID resolver */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 mb-6">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">Resolve OID</h3>
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            className={clsx(inputCls, 'font-mono sm:flex-1')}
            value={oid}
            placeholder="1.3.6.1.2.1.1.1.0"
            onChange={(e) => setOid(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') onResolve() }}
          />
          <button onClick={onResolve} disabled={resolving || !oid.trim()}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium shrink-0">
            {resolving ? 'Resolving…' : 'Resolve'}
          </button>
        </div>
        {resolveErr && <p className="text-sm text-red-600 dark:text-red-400 mt-2">{resolveErr}</p>}
        {resolution && (
          <p className="text-sm mt-2">
            <span className="font-mono text-gray-600 dark:text-gray-400">{resolution.oid}</span>
            {' → '}
            {resolution.resolved && resolution.name
              ? <span className="font-medium text-green-700 dark:text-green-400">{resolution.name}</span>
              : <span className="text-gray-400 dark:text-gray-500">not resolved</span>}
          </p>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <div className="space-y-6">
          {/* Standard MIBs */}
          <MibListSection title="Standard MIBs (built-in)" mibs={standard} />

          {/* Vendor MIBs — per-vendor count summary */}
          <Card title="Vendor MIBs" count={vendor.length}>
            {vendor.length === 0 ? (
              <EmptyRow text="No vendor MIBs installed." />
            ) : (
              <div className="px-5 py-4 flex flex-wrap gap-2">
                {vendorSummary.map(([v, n]) => (
                  <span key={v} className="px-3 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300">
                    {titleCase(v)} ({n} MIB{n === 1 ? '' : 's'})
                  </span>
                ))}
              </div>
            )}
          </Card>

          {/* Community MIBs */}
          <MibListSection title="Community MIBs" mibs={community} />

          {/* Custom MIBs — deletable */}
          <Card title="Custom MIBs" count={custom.length}>
            {custom.length === 0 ? (
              <EmptyRow text="No custom MIBs uploaded yet. Use Upload MIB above to add one." />
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                    <th className="px-5 py-2.5 font-medium">Name</th>
                    <th className="px-5 py-2.5 font-medium">File</th>
                    <th className="px-5 py-2.5 font-medium text-right">Objects</th>
                    <th className="px-5 py-2.5 font-medium text-center">Loaded</th>
                    <th className="px-5 py-2.5 font-medium text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                  {custom.map((m) => (
                    <tr key={m.name} className={clsx('text-gray-700 dark:text-gray-300', deleting === m.name && 'opacity-50')}>
                      <td className="px-5 py-2.5 font-medium">{m.name}</td>
                      <td className="px-5 py-2.5 font-mono text-xs text-gray-500 dark:text-gray-400">{m.file}</td>
                      <td className="px-5 py-2.5 text-right">{m.objects}</td>
                      <td className="px-5 py-2.5 text-center">{m.loaded ? '✅' : '—'}</td>
                      <td className="px-5 py-2.5 text-right">
                        {m.deletable ? (
                          <button onClick={() => remove(m)} disabled={deleting === m.name}
                            className="px-2.5 py-1 text-xs border border-red-300 dark:border-red-700 text-red-700 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/30 disabled:opacity-50">
                            🗑 Delete
                          </button>
                        ) : (
                          <span className="text-xs text-gray-400">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}

// Card wrapper matching the other settings sections.
function Card({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">
        {title} <span className="text-gray-400 dark:text-gray-500 font-normal">({count})</span>
      </h3>
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-x-auto">
        {children}
      </div>
    </div>
  )
}

function EmptyRow({ text }: { text: string }) {
  return <div className="px-5 py-8 text-center text-sm text-gray-400 dark:text-gray-500">{text}</div>
}

// A simple read-only list of MIBs with their object counts (standard / community).
function MibListSection({ title, mibs }: { title: string; mibs: MibInfo[] }) {
  return (
    <Card title={title} count={mibs.length}>
      {mibs.length === 0 ? (
        <EmptyRow text="None installed." />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
              <th className="px-5 py-2.5 font-medium">Name</th>
              <th className="px-5 py-2.5 font-medium">File</th>
              <th className="px-5 py-2.5 font-medium text-right">Objects</th>
              <th className="px-5 py-2.5 font-medium text-center">Loaded</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {mibs.map((m) => (
              <tr key={m.name} className="text-gray-700 dark:text-gray-300">
                <td className="px-5 py-2.5 font-medium">{m.name}</td>
                <td className="px-5 py-2.5 font-mono text-xs text-gray-500 dark:text-gray-400">{m.file}</td>
                <td className="px-5 py-2.5 text-right">{m.objects}</td>
                <td className="px-5 py-2.5 text-center">{m.loaded ? '✅' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}
