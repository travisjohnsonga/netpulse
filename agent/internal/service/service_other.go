//go:build !windows

// Package service installs/uninstalls the agent as an OS service. On non-Windows
// platforms the systemd unit is created by scripts/install.sh, so these are no-ops.
package service

import "fmt"

func Install(configPath string) error {
	return fmt.Errorf("--install-service is Windows-only; the Linux installer creates a systemd unit")
}

func Uninstall() error {
	return fmt.Errorf("--uninstall-service is Windows-only; use 'systemctl disable --now netpulse-agent'")
}
