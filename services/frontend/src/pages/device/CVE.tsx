import { type DeviceDetail } from '../../api/client'

export default function CVE({ device }: { device: DeviceDetail }) {
  void device
  return <p className="text-sm text-gray-500">CVE — coming next.</p>
}
