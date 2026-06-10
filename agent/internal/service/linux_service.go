//go:build !windows

// Package service installs/uninstalls the agent as an OS service. On Linux (and
// other non-Windows systemd hosts) this writes a hardened systemd unit, reloads
// the daemon, and enables+starts it. The unit mirrors the one scripts/install.sh
// used to write by hand: a low-privilege netpulse-agent user with filesystem
// hardening (per the project's security rules — the agent never needs root to
// read /proc system-wide metrics).
package service

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

const (
	serviceName = "netpulse-agent"
	unitPath    = "/etc/systemd/system/netpulse-agent.service"
	serviceUser = "netpulse-agent"
)

// unitTemplate args: ExecStart binary, config path, run-as user, config dir.
const unitTemplate = `[Unit]
Description=NetPulse Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%s --config %s
Restart=always
RestartSec=30
User=%s
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=%s

[Install]
WantedBy=multi-user.target
`

func Install(configPath string) error {
	exePath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("get executable path: %w", err)
	}

	unit := fmt.Sprintf(unitTemplate, exePath, configPath, serviceUser, filepath.Dir(configPath))

	if err := os.WriteFile(unitPath, []byte(unit), 0644); err != nil {
		return fmt.Errorf("write service file: %w", err)
	}

	cmds := [][]string{
		{"systemctl", "daemon-reload"},
		{"systemctl", "enable", serviceName},
		{"systemctl", "start", serviceName},
	}
	for _, cmd := range cmds {
		if err := exec.Command(cmd[0], cmd[1:]...).Run(); err != nil {
			return fmt.Errorf("run %v: %w", cmd, err)
		}
	}

	fmt.Println("Service installed and started!")
	fmt.Println("Check: systemctl status netpulse-agent")
	return nil
}

func Uninstall() error {
	// Best-effort stop/disable; the service may not be running.
	for _, cmd := range [][]string{
		{"systemctl", "stop", serviceName},
		{"systemctl", "disable", serviceName},
	} {
		_ = exec.Command(cmd[0], cmd[1:]...).Run()
	}
	if err := os.Remove(unitPath); err != nil {
		return fmt.Errorf("remove service file: %w", err)
	}
	return exec.Command("systemctl", "daemon-reload").Run()
}
