//go:build !windows

package main

import "github.com/travisjohnsonga/netpulse/agent/internal/agent"

// runAgent on non-Windows always runs in the foreground (systemd runs the binary
// in the foreground and manages it via process signals).
func runAgent(a *agent.Agent) {
	runForeground(a)
}
