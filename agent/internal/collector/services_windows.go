//go:build windows

package collector

import "golang.org/x/sys/windows/svc/mgr"

var winStateStr = map[uint32]string{
	1: "stopped", 2: "start_pending", 3: "stop_pending", 4: "running",
	5: "continue_pending", 6: "pause_pending", 7: "paused",
}

var winStartTypeStr = map[uint32]string{
	0: "boot", 1: "system", 2: "automatic", 3: "manual", 4: "disabled",
}

// CollectServices reports Windows service states. With a watch list, missing
// services are reported as "not_found"; without one, all services are listed.
func CollectServices(watch []string) ([]ServiceStat, error) {
	m, err := mgr.Connect()
	if err != nil {
		return nil, err
	}
	defer m.Disconnect()

	names := watch
	if len(names) == 0 {
		if names, err = m.ListServices(); err != nil {
			return nil, err
		}
	}

	var stats []ServiceStat
	for _, name := range names {
		s, err := m.OpenService(name)
		if err != nil {
			if len(watch) > 0 {
				stats = append(stats, ServiceStat{Name: name, State: "not_found", Running: false})
			}
			continue
		}
		status, qerr := s.Query()
		if qerr != nil {
			s.Close()
			continue
		}
		cfg, _ := s.Config()
		stats = append(stats, ServiceStat{
			Name:        name,
			DisplayName: cfg.DisplayName,
			State:       winStateStr[uint32(status.State)],
			StartType:   winStartTypeStr[uint32(cfg.StartType)],
			Running:     uint32(status.State) == 4,
		})
		s.Close()
	}
	return stats, nil
}
