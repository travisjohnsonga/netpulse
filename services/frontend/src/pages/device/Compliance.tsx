import { type DeviceDetail } from '../../api/client'

export default function Compliance({ device }: { device: DeviceDetail }) {
  void device
  return <p className="text-sm text-gray-500">Compliance — coming next.</p>
}
