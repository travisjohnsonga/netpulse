//go:build linux

package collector

import (
	"os/exec"
	"strings"
)

// CollectServices reports systemd unit states. With a watch list, each unit is
// queried explicitly (missing units reported as "not_found"); otherwise nothing
// is returned (enumerating every unit is noisy — roles supply the watch list).
func CollectServices(watch []string) ([]ServiceStat, error) {
	var stats []ServiceStat
	for _, name := range watch {
		unit := name
		if !strings.Contains(unit, ".") {
			unit += ".service"
		}
		out, _ := exec.Command("systemctl", "show", unit,
			"--property=ActiveState,SubState,UnitFileState,LoadState,Description").Output()
		props := parseShow(string(out))
		if props["LoadState"] == "not-found" || props["LoadState"] == "" {
			stats = append(stats, ServiceStat{Name: name, State: "not_found", Running: false})
			continue
		}
		active := props["ActiveState"]
		stats = append(stats, ServiceStat{
			Name:        name,
			DisplayName: props["Description"],
			State:       active,
			StartType:   props["UnitFileState"],
			Running:     active == "active",
		})
	}
	return stats, nil
}

func parseShow(out string) map[string]string {
	m := make(map[string]string)
	for _, line := range strings.Split(out, "\n") {
		if k, v, ok := strings.Cut(strings.TrimSpace(line), "="); ok {
			m[k] = v
		}
	}
	return m
}
