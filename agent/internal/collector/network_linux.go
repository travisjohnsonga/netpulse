//go:build linux

package collector

import (
	"bufio"
	"os"
	"strconv"
	"strings"
	"time"
)

var skipInterfaces = map[string]bool{"lo": true}

func (c *NetworkCollector) Collect() ([]NetworkStat, error) {
	current, err := readNetDev()
	if err != nil {
		return nil, err
	}
	now := time.Now()
	dt := now.Sub(c.lastTime).Seconds()

	var result []NetworkStat
	for iface, curr := range current {
		if skipInterfaces[iface] {
			continue
		}
		stat := curr
		if last, ok := c.lastStats[iface]; ok && dt > 0 {
			if curr.RxBytes >= last.RxBytes {
				stat.RxBps = float64(curr.RxBytes-last.RxBytes) / dt
			}
			if curr.TxBytes >= last.TxBytes {
				stat.TxBps = float64(curr.TxBytes-last.TxBytes) / dt
			}
		}
		result = append(result, stat)
	}
	c.lastStats = current
	c.lastTime = now
	return result, nil
}

func readNetDev() (map[string]NetworkStat, error) {
	f, err := os.Open("/proc/net/dev")
	if err != nil {
		return nil, err
	}
	defer f.Close()

	stats := make(map[string]NetworkStat)
	scanner := bufio.NewScanner(f)
	scanner.Scan() // skip the two header lines
	scanner.Scan()
	for scanner.Scan() {
		parts := strings.SplitN(scanner.Text(), ":", 2)
		if len(parts) != 2 {
			continue
		}
		iface := strings.TrimSpace(parts[0])
		fields := strings.Fields(parts[1])
		if len(fields) < 16 {
			continue
		}
		u := func(i int) uint64 {
			v, _ := strconv.ParseUint(fields[i], 10, 64)
			return v
		}
		stats[iface] = NetworkStat{
			Interface: iface,
			RxBytes:   u(0), RxPackets: u(1), RxErrors: u(2), RxDropped: u(3),
			TxBytes: u(8), TxPackets: u(9), TxErrors: u(10), TxDropped: u(11),
		}
	}
	return stats, scanner.Err()
}
