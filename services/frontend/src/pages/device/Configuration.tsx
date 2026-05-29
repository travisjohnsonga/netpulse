import { type DeviceDetail } from '../../api/client'

export default function Configuration({ device }: { device: DeviceDetail }) {
  void device
  return <p className="text-sm text-gray-500">Configuration — coming next.</p>
}
