import { type DeviceDetail } from '../../api/client'

export default function Telemetry({ device }: { device: DeviceDetail }) {
  void device
  return <p className="text-sm text-gray-500">Telemetry — coming next.</p>
}
