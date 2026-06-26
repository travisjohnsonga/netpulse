//go:build windows

package main

import (
	"log"

	"github.com/travisjohnsonga/netpulse/agent/internal/agent"
	"github.com/travisjohnsonga/netpulse/agent/internal/service"
)

// runAgent on Windows detects whether the process was launched by the Service
// Control Manager. If so it runs under the SCM (reporting Running and handling
// Stop/Shutdown); otherwise it runs in the foreground exactly like Linux — so
// an interactive `netpulse-agent.exe --config X` is unchanged.
func runAgent(a *agent.Agent) {
	isSvc, err := service.IsWindowsService()
	if err != nil {
		log.Fatalf("Failed to detect service mode: %v", err)
	}
	if isSvc {
		if err := service.RunWindowsService(a); err != nil {
			log.Fatalf("Service run failed: %v", err)
		}
		return
	}
	runForeground(a)
}
