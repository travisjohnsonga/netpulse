//go:build linux

package collector

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

func (c *CPUCollector) Collect() ([]CPUStat, error) {
	stats, err := readProcStat()
	if err != nil {
		return nil, err
	}
	now := time.Now()
	result := []CPUStat{}
	for core, values := range stats {
		if last, ok := c.lastStats[core]; ok {
			if dt := now.Sub(c.lastTime).Seconds(); dt > 0 {
				result = append(result, calcCPUStat(core, last, values))
			}
		}
	}
	c.lastStats = stats
	c.lastTime = now
	return result, nil
}

func readProcStat() (map[string][]uint64, error) {
	f, err := os.Open("/proc/stat")
	if err != nil {
		return nil, err
	}
	defer f.Close()

	stats := make(map[string][]uint64)
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "cpu") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 8 {
			continue
		}
		values := make([]uint64, len(fields)-1)
		for i, v := range fields[1:] {
			values[i], _ = strconv.ParseUint(v, 10, 64)
		}
		stats[fields[0]] = values
	}
	return stats, scanner.Err()
}

// /proc/stat fields: user nice system idle iowait irq softirq steal ...
func calcCPUStat(core string, prev, curr []uint64) CPUStat {
	diff := func(i int) float64 {
		if i >= len(curr) || i >= len(prev) || curr[i] < prev[i] {
			return 0
		}
		return float64(curr[i] - prev[i])
	}
	user := diff(0) + diff(1) // user + nice
	system := diff(2)
	idle := diff(3)
	iowait := diff(4)
	irq := diff(5) + diff(6) // irq + softirq
	steal := diff(7)
	total := user + system + idle + iowait + irq + steal

	pct := func(v float64) float64 {
		if total == 0 {
			return 0
		}
		return v / total * 100
	}
	return CPUStat{
		Core:   core,
		User:   pct(user),
		System: pct(system),
		Idle:   pct(idle),
		IOWait: pct(iowait),
		Steal:  pct(steal),
		Usage:  pct(user + system + iowait + irq + steal),
	}
}

// LoadAverage reads /proc/loadavg.
func LoadAverage() (load1, load5, load15 float64, err error) {
	data, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return
	}
	_, err = fmt.Sscanf(string(data), "%f %f %f", &load1, &load5, &load15)
	return
}
