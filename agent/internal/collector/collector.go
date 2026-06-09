// Package collector gathers host metrics. Shared types + constructors live
// here; the per-OS Collect/read implementations live in *_linux.go / *_windows.go
// behind build tags, all exposing the same cross-platform API
// (CollectMemory/CollectDisk/CollectServices/LoadAverage and the
// CPUCollector/NetworkCollector Collect methods).
package collector

import "time"

type CPUStat struct {
	Core   string  `json:"core"`
	User   float64 `json:"user"`
	System float64 `json:"system"`
	Idle   float64 `json:"idle"`
	IOWait float64 `json:"iowait"`
	Steal  float64 `json:"steal"`
	Usage  float64 `json:"usage_pct"`
}

type MemoryStat struct {
	TotalBytes     uint64  `json:"total_bytes"`
	UsedBytes      uint64  `json:"used_bytes"`
	FreeBytes      uint64  `json:"free_bytes"`
	CachedBytes    uint64  `json:"cached_bytes"`
	BufferedBytes  uint64  `json:"buffered_bytes"`
	AvailableBytes uint64  `json:"available_bytes"`
	UsagePct       float64 `json:"usage_pct"`
	SwapTotal      uint64  `json:"swap_total_bytes"`
	SwapUsed       uint64  `json:"swap_used_bytes"`
	SwapFree       uint64  `json:"swap_free_bytes"`
}

type DiskStat struct {
	Mount      string  `json:"mount"`
	Device     string  `json:"device"`
	FSType     string  `json:"fstype"`
	TotalBytes uint64  `json:"total_bytes"`
	UsedBytes  uint64  `json:"used_bytes"`
	FreeBytes  uint64  `json:"free_bytes"`
	UsagePct   float64 `json:"usage_pct"`
	ReadBps    float64 `json:"read_bytes_per_sec"`
	WriteBps   float64 `json:"write_bytes_per_sec"`
	IOUtilPct  float64 `json:"io_util_pct"`
}

type NetworkStat struct {
	Interface string  `json:"interface"`
	RxBytes   uint64  `json:"rx_bytes"`
	TxBytes   uint64  `json:"tx_bytes"`
	RxPackets uint64  `json:"rx_packets"`
	TxPackets uint64  `json:"tx_packets"`
	RxErrors  uint64  `json:"rx_errors"`
	TxErrors  uint64  `json:"tx_errors"`
	RxDropped uint64  `json:"rx_dropped"`
	TxDropped uint64  `json:"tx_dropped"`
	RxBps     float64 `json:"rx_bps"`
	TxBps     float64 `json:"tx_bps"`
}

type ServiceStat struct {
	Name        string `json:"name"`
	DisplayName string `json:"display_name"`
	State       string `json:"state"`
	StartType   string `json:"start_type"`
	Running     bool   `json:"running"`
}

// CPUCollector keeps the previous /proc/stat snapshot (Linux) so usage can be
// computed as a delta. Windows reads instantaneous WMI counters and ignores it.
type CPUCollector struct {
	lastStats map[string][]uint64
	lastTime  time.Time
}

func NewCPUCollector() *CPUCollector {
	return &CPUCollector{lastStats: make(map[string][]uint64)}
}

// NetworkCollector keeps the previous /proc/net/dev snapshot (Linux) for bps
// rates. Windows reads per-second WMI counters and ignores it.
type NetworkCollector struct {
	lastStats map[string]NetworkStat
	lastTime  time.Time
}

func NewNetworkCollector() *NetworkCollector {
	return &NetworkCollector{lastStats: make(map[string]NetworkStat)}
}
