//go:build linux

package collector

import (
	"bufio"
	"os"
	"strconv"
	"strings"
)

func CollectMemory() (*MemoryStat, error) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return nil, err
	}
	defer f.Close()

	fields := make(map[string]uint64)
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		parts := strings.Fields(scanner.Text())
		if len(parts) < 2 {
			continue
		}
		val, _ := strconv.ParseUint(parts[1], 10, 64)
		fields[strings.TrimSuffix(parts[0], ":")] = val * 1024 // kB → bytes
	}

	total := fields["MemTotal"]
	free := fields["MemFree"]
	cached := fields["Cached"]
	buffers := fields["Buffers"]
	used := total - free - cached - buffers

	var usagePct float64
	if total > 0 {
		usagePct = float64(used) / float64(total) * 100
	}
	return &MemoryStat{
		TotalBytes:     total,
		UsedBytes:      used,
		FreeBytes:      free,
		CachedBytes:    cached,
		BufferedBytes:  buffers,
		AvailableBytes: fields["MemAvailable"],
		UsagePct:       usagePct,
		SwapTotal:      fields["SwapTotal"],
		SwapFree:       fields["SwapFree"],
		SwapUsed:       fields["SwapTotal"] - fields["SwapFree"],
	}, scanner.Err()
}
