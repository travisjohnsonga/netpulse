import { useState } from 'react'
import clsx from 'clsx'
import { type DeviceDetail } from '../../api/client'
import Modal from '../../components/Modal'

// Config storage/versioning is handled by the config-manager service; its API
// isn't exposed yet, so this renders an illustrative running-config snapshot
// derived from the device record. Download is real (client-side blob).

function sampleConfig(d: DeviceDetail): string {
  return [
    '! NetPulse captured running-config',
    `hostname ${d.hostname}`,
    '!',
    'service timestamps log datetime msec',
    'no ip domain-lookup',
    '!',
    'interface Loopback0',
    ` ip address ${d.ip_address} 255.255.255.255`,
    '!',
    'interface GigabitEthernet0/0/0',
    ' description WAN uplink',
    ' ip address dhcp',
    ' no shutdown',
    '!',
    'snmp-server community netpulse RO',
    'logging host 10.0.0.10',
    '!',
    'line vty 0 4',
    ' transport input ssh',
    '!',
    'end',
  ].join('\n')
}

interface Version { id: number; label: string; when: string; author: string }
const VERSIONS: Version[] = [
  { id: 3, label: 'v3 (current)', when: '2d ago', author: 'config-backup' },
  { id: 2, label: 'v2', when: '5d ago', author: 'dana' },
  { id: 1, label: 'v1', when: '12d ago', author: 'config-backup' },
]

export default function Configuration({ device }: { device: DeviceDetail }) {
  const config = sampleConfig(device)
  const [comparing, setComparing] = useState(false)
  const [selected, setSelected] = useState(3)

  const download = () => {
    const blob = new Blob([config], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${device.hostname}-running-config.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Version history */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-200"><h3 className="text-sm font-semibold text-gray-800">Version History</h3></div>
        <div className="divide-y divide-gray-100">
          {VERSIONS.map((v) => (
            <button
              key={v.id}
              onClick={() => setSelected(v.id)}
              className={clsx('w-full text-left px-4 py-3 hover:bg-gray-50', selected === v.id && 'bg-blue-50')}
            >
              <p className="text-sm font-medium text-gray-800">{v.label}</p>
              <p className="text-xs text-gray-400">{v.author} · {v.when}</p>
            </button>
          ))}
        </div>
      </div>

      {/* Config viewer */}
      <div className="lg:col-span-2 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <h3 className="text-sm font-semibold text-gray-800">Running Config</h3>
          <div className="flex gap-2">
            <button onClick={() => setComparing(true)} className="px-3 py-1.5 text-xs border border-gray-300 rounded-lg hover:bg-gray-50">Compare</button>
            <button onClick={download} className="px-3 py-1.5 text-xs border border-gray-300 rounded-lg hover:bg-gray-50">Download</button>
          </div>
        </div>
        <pre className="bg-gray-900 text-gray-100 text-xs font-mono p-4 overflow-x-auto leading-relaxed max-h-[28rem]">
          {config.split('\n').map((line, i) => (
            <div key={i} className={clsx(
              line.startsWith('!') && 'text-gray-500',
              /^(hostname|interface|line|snmp-server|logging|service|ip|no)\b/.test(line) && 'text-sky-300',
              line.trim().startsWith('ip address') && 'text-emerald-300',
            )}>{line || ' '}</div>
          ))}
        </pre>
      </div>

      {comparing && (
        <Modal title="Compare Configurations" onClose={() => setComparing(false)} size="xl"
          footer={<button onClick={() => setComparing(false)} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Close</button>}>
          <p className="text-sm text-gray-500 mb-3">v2 → v3 (current). Full diff renders once the config-manager API is wired.</p>
          <div className="font-mono text-xs rounded-lg border border-gray-200 overflow-hidden">
            <div className="bg-red-50 text-red-700 px-3 py-1">- snmp-server community public RO</div>
            <div className="bg-green-50 text-green-700 px-3 py-1">+ snmp-server community netpulse RO</div>
            <div className="bg-green-50 text-green-700 px-3 py-1">+ logging host 10.0.0.10</div>
          </div>
        </Modal>
      )}
    </div>
  )
}
