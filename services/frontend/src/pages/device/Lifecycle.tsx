import { type DeviceDetail } from '../../api/client'

export default function Lifecycle({ device }: { device: DeviceDetail }) {
  void device
  return <p className="text-sm text-gray-500">Lifecycle — coming next.</p>
}
