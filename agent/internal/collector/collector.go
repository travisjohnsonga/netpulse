// Package collector gathers host metrics. Shared types + constructors live
// here; the per-OS Collect/read implementations live in *_linux.go / *_windows.go
// behind build tags, all exposing the same cross-platform API
// (CollectMemory/CollectDisk/CollectServices/LoadAverage and the
// CPUCollector/NetworkCollector Collect methods).
package collector

import (
	"strings"
	"time"
)

// normalizeMount canonicalizes a mount/drive identifier so an operator's list
// entry matches what the collector emits as DiskStat.Mount across OSes:
//   - trailing path separators are stripped ("/var/"→"/var", "D:\\"→"D:"),
//     but a bare root ("/" or "\\") is preserved as "/";
//   - a two-char Windows drive ("d:") is upper-cased ("D:") — Windows drive
//     letters are case-insensitive. Linux paths keep their case (case-sensitive).
//
// So "D:", "D:\\" and "d:" all match the Windows collector's "D:\\".
func normalizeMount(m string) string {
	m = strings.TrimSpace(m)
	if m == "" {
		return ""
	}
	trimmed := strings.TrimRight(m, `/\`)
	if trimmed == "" {
		return "/" // a bare root mount
	}
	m = trimmed
	if len(m) == 2 && m[1] == ':' { // drive letter, e.g. "C:"
		m = strings.ToUpper(m)
	}
	return m
}

// Windows GetDriveType return values (winbase.h). Defined here (not importing
// x/sys/windows) so the skip policy is testable on any platform.
const (
	driveRemovable = 2 // USB / floppy
	driveFixed     = 3 // hard disk — kept
	driveRemote    = 4 // network/mapped drive — kept by default
	driveCDROM     = 5 // optical / DVD / mounted ISO
)

// skipWindowsDriveType reports whether a GetDriveType result is removable or
// optical media (DVD/ISO/USB) — inherently noisy (a full, read-only disc reports
// 100% forever) and not worth monitoring by default. DRIVE_FIXED and DRIVE_REMOTE
// (network drives) are kept. Auto-skip runs BEFORE the manual exclude_mounts
// filter, so an operator never has to exclude a DVD by hand.
func skipWindowsDriveType(dt uint32) bool {
	return dt == driveRemovable || dt == driveCDROM
}

func normalizeSet(items []string) map[string]bool {
	out := make(map[string]bool, len(items))
	for _, it := range items {
		if n := normalizeMount(it); n != "" {
			out[n] = true
		}
	}
	return out
}

// FilterDisks applies an operator's include/exclude mount filter to collected
// disk stats. Rules (cross-OS, via normalizeMount):
//   - empty include AND empty exclude → all disks (the default; must NOT regress
//     to "monitor nothing");
//   - non-empty include → only listed mounts;
//   - exclude drops a mount and takes PRECEDENCE over include.
//
// The OS collectors are unchanged; this runs on their output so Linux and
// Windows share one filtering rule.
func FilterDisks(stats []DiskStat, include, exclude []string) []DiskStat {
	inc := normalizeSet(include)
	exc := normalizeSet(exclude)
	if len(inc) == 0 && len(exc) == 0 {
		return stats
	}
	out := make([]DiskStat, 0, len(stats))
	for _, d := range stats {
		n := normalizeMount(d.Mount)
		if exc[n] {
			continue
		}
		if len(inc) > 0 && !inc[n] {
			continue
		}
		out = append(out, d)
	}
	return out
}

type CPUStat struct {
	Core   string  `json:"core"`
	User   float64 `json:"user"`
	System float64 `json:"system"`
	Idle   float64 `json:"idle"`
	IOWait float64 `json:"iowait"`
	Steal  float64 `json:"steal"`
	Usage  float64 `json:"usage_pct"`
}

// AggregateCPUCore is the Core name the server treats as the whole-host CPU
// aggregate (vs. a per-core entry) — the chart and Overview stat key off it (see
// the server's metrics_read.py: r.core == "cpu"). Linux uses it directly (the
// /proc/stat "cpu" line); Windows maps its "_Total" WMI row to it.
const AggregateCPUCore = "cpu"

// normalizeCPUCore maps a platform CPU identifier to the cross-platform Core
// name. Win32_PerfFormattedData_PerfOS_Processor returns "_Total" for the
// aggregate row; renaming it to the aggregate key makes Windows emit the same
// shape as Linux (one aggregate + N per-core entries) so the aggregate feeds the
// chart/Overview and isn't drawn as a spurious per-core bar.
func normalizeCPUCore(name string) string {
	if name == "_Total" {
		return AggregateCPUCore
	}
	return name
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
