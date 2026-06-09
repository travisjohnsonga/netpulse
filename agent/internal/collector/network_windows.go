//go:build windows

package collector

import "github.com/StackExchange/wmi"

type win32PerfNetIface struct {
	Name                  string
	BytesReceivedPerSec   uint64
	BytesSentPerSec       uint64
	PacketsReceivedPerSec uint64
	PacketsSentPerSec     uint64
	PacketsReceivedErrors uint64
	PacketsOutboundErrors uint64
}

func (c *NetworkCollector) Collect() ([]NetworkStat, error) {
	var ifaces []win32PerfNetIface
	if err := wmi.Query(
		`SELECT Name, BytesReceivedPerSec, BytesSentPerSec, PacketsReceivedPerSec, `+
			`PacketsSentPerSec, PacketsReceivedErrors, PacketsOutboundErrors `+
			`FROM Win32_PerfFormattedData_Tcpip_NetworkInterface`, &ifaces); err != nil {
		return nil, err
	}
	var stats []NetworkStat
	for _, n := range ifaces {
		if n.Name == "Loopback" || n.Name == "lo" {
			continue
		}
		stats = append(stats, NetworkStat{
			Interface: n.Name,
			RxBps:     float64(n.BytesReceivedPerSec) * 8,
			TxBps:     float64(n.BytesSentPerSec) * 8,
			RxPackets: n.PacketsReceivedPerSec,
			TxPackets: n.PacketsSentPerSec,
			RxErrors:  n.PacketsReceivedErrors,
			TxErrors:  n.PacketsOutboundErrors,
		})
	}
	return stats, nil
}
