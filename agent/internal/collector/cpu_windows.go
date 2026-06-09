//go:build windows

package collector

import "github.com/StackExchange/wmi"

type win32PerfProcessor struct {
	Name                  string
	PercentProcessorTime  uint64
	PercentUserTime       uint64
	PercentPrivilegedTime uint64
	PercentIdleTime       uint64
}

func (c *CPUCollector) Collect() ([]CPUStat, error) {
	var procs []win32PerfProcessor
	if err := wmi.Query(
		`SELECT Name, PercentProcessorTime, PercentUserTime, PercentPrivilegedTime, PercentIdleTime `+
			`FROM Win32_PerfFormattedData_PerfOS_Processor`, &procs); err != nil {
		return nil, err
	}
	var stats []CPUStat
	for _, p := range procs {
		idle := float64(p.PercentIdleTime)
		stats = append(stats, CPUStat{
			Core:   p.Name,
			User:   float64(p.PercentUserTime),
			System: float64(p.PercentPrivilegedTime),
			Idle:   idle,
			Usage:  100 - idle,
		})
	}
	return stats, nil
}

// LoadAverage has no direct Windows equivalent; approximate load1 with the
// processor queue length and leave the 5/15-minute figures at zero.
func LoadAverage() (load1, load5, load15 float64, err error) {
	var rows []struct{ ProcessorQueueLength uint32 }
	err = wmi.Query(`SELECT ProcessorQueueLength FROM Win32_PerfFormattedData_PerfOS_System`, &rows)
	if err != nil || len(rows) == 0 {
		return 0, 0, 0, err
	}
	return float64(rows[0].ProcessorQueueLength), 0, 0, nil
}
