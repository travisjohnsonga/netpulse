// Package agent wires collectors → transport on a timer.
package agent

import (
	"context"
	"log"
	"os"
	"runtime"
	"time"

	"github.com/travisjohnsonga/netpulse/agent/internal/collector"
	"github.com/travisjohnsonga/netpulse/agent/internal/config"
	"github.com/travisjohnsonga/netpulse/agent/internal/enrollment"
	"github.com/travisjohnsonga/netpulse/agent/internal/transport"
)

type Agent struct {
	cfg     *config.Config
	cfgPath string
	version string
	client  *transport.Client
	cpu     *collector.CPUCollector
	network *collector.NetworkCollector
	ctx     context.Context
	cancel  context.CancelFunc
}

type metricPayload struct {
	Timestamp string                 `json:"timestamp"`
	Hostname  string                 `json:"hostname"`
	AgentID   string                 `json:"agent_id"`
	Version   string                 `json:"version"`
	Metrics   map[string]interface{} `json:"metrics"`
}

func New(cfg *config.Config, cfgPath, version string) (*Agent, error) {
	client, err := transport.NewClient(cfg.ServerURL, cfg.AgentID, cfg.CertPath, cfg.KeyPath, cfg.CACertPath, cfg.InsecureTLS, cfg.APIKey)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	return &Agent{
		cfg: cfg, cfgPath: cfgPath, version: version, client: client,
		cpu:     collector.NewCPUCollector(),
		network: collector.NewNetworkCollector(),
		ctx:     ctx, cancel: cancel,
	}, nil
}

func (a *Agent) Run() error {
	log.Printf("NetPulse Agent %s starting (interval %ds)", a.version, a.cfg.Collection.Interval)
	hostname, _ := os.Hostname()
	ticker := time.NewTicker(time.Duration(a.cfg.Collection.Interval) * time.Second)
	defer ticker.Stop()

	a.collect(hostname) // prime collectors + first sample
	for {
		select {
		case <-ticker.C:
			a.collect(hostname)
		case <-a.ctx.Done():
			return nil
		}
	}
}

func (a *Agent) collect(hostname string) {
	metrics := make(map[string]interface{})

	if a.cfg.Collection.CPU {
		if cpuStats, err := a.cpu.Collect(); err == nil {
			metrics["cpu"] = cpuStats
		}
		if l1, l5, l15, err := collector.LoadAverage(); err == nil {
			metrics["load"] = map[string]float64{"load1": l1, "load5": l5, "load15": l15}
		}
	}
	if a.cfg.Collection.Memory {
		if mem, err := collector.CollectMemory(); err == nil {
			metrics["memory"] = mem
		}
	}
	if a.cfg.Collection.Disk {
		if disk, err := collector.CollectDisk(); err == nil {
			metrics["disk"] = disk
		}
	}
	if a.cfg.Collection.Network {
		if net, err := a.network.Collect(); err == nil {
			metrics["network"] = net
		}
	}
	if a.cfg.Collection.Services {
		// Running service names → server-side role auto-detection.
		if names := collector.RunningServiceNames(); len(names) > 0 {
			metrics["services"] = names
		}
	}
	metrics["system"] = map[string]interface{}{
		"hostname": hostname, "os": runtime.GOOS, "arch": runtime.GOARCH,
		"go_version": runtime.Version(),
	}

	payload := metricPayload{
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Hostname:  hostname, AgentID: a.cfg.AgentID, Version: a.version, Metrics: metrics,
	}
	if resp, err := a.client.SendMetrics(payload); err != nil {
		log.Printf("send metrics: %v", err)
	} else if resp != nil {
		a.applyServerConfig(resp)
	}

	a.runRoleChecks(hostname)
}

// applyServerConfig reconciles the agent's local config with the server-pushed
// assignments from a metrics response. The server is authoritative for which
// roles a host runs, so newly assigned roles are merged in and role checks are
// enabled automatically — no manual config edit needed. Changes are persisted
// so they survive a restart. The merge is additive (it never drops locally
// declared roles), matching the existing config-declared role behaviour.
func (a *Agent) applyServerConfig(resp *transport.MetricsResponse) {
	changed := false

	if merged, added := mergeRoles(a.cfg.RoleChecks.Roles, resp.AssignedRoles); added {
		a.cfg.RoleChecks.Roles = merged
		changed = true
	}
	if resp.CollectionConfig.RoleChecksEnabled && !a.cfg.RoleChecks.Enabled {
		a.cfg.RoleChecks.Enabled = true
		changed = true
	}
	if resp.CollectionConfig.Services && !a.cfg.Collection.Services {
		a.cfg.Collection.Services = true
		changed = true
	}

	if changed {
		if err := config.Save(a.cfgPath, a.cfg); err != nil {
			log.Printf("persist server config update: %v", err)
			return
		}
		log.Printf("applied server config: role_checks.enabled=%v roles=%v",
			a.cfg.RoleChecks.Enabled, a.cfg.RoleChecks.Roles)
	}
}

// mergeRoles returns the union of existing and incoming role names (existing
// order preserved, new ones appended) and whether anything new was added.
func mergeRoles(existing, incoming []string) ([]string, bool) {
	have := make(map[string]bool, len(existing))
	for _, r := range existing {
		have[r] = true
	}
	merged, added := existing, false
	for _, r := range incoming {
		if r != "" && !have[r] {
			merged = append(merged, r)
			have[r] = true
			added = true
		}
	}
	return merged, added
}

func (a *Agent) runRoleChecks(hostname string) {
	if !a.cfg.RoleChecks.Enabled || len(a.cfg.RoleChecks.Roles) == 0 {
		return
	}
	specs := collector.SpecsFor(a.cfg.RoleChecks.Roles)
	extra := a.cfg.RoleChecks.ExtraServices[runtime.GOOS]
	results := collector.RunRoleChecks(specs, extra)
	payload := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"hostname":  hostname,
		"roles":     results,
	}
	if err := a.client.SendRoleChecks(payload); err != nil {
		log.Printf("send role checks: %v", err)
	}
}

func (a *Agent) Stop() { a.cancel() }

// Enroll runs first-time enrollment (delegates to the enrollment package).
// insecure skips server-cert verification (dev / self-signed servers).
func Enroll(serverURL, token, configPath string, insecure bool) error {
	return enrollment.Enroll(serverURL, token, configPath, "", insecure)
}
