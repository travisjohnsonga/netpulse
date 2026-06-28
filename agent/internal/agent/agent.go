// Package agent wires collectors → transport on a timer.
package agent

import (
	"context"
	"fmt"
	"log"
	"os"
	"runtime"
	"time"

	"github.com/travisjohnsonga/netpulse/agent/internal/collector"
	"github.com/travisjohnsonga/netpulse/agent/internal/config"
	"github.com/travisjohnsonga/netpulse/agent/internal/enrollment"
	"github.com/travisjohnsonga/netpulse/agent/internal/logforward"
	"github.com/travisjohnsonga/netpulse/agent/internal/transport"
)

type Agent struct {
	cfg     *config.Config
	cfgPath string
	version string
	client  *transport.Client
	cpu     *collector.CPUCollector
	network *collector.NetworkCollector
	logfwd  *logforward.Forwarder
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
		logfwd:  logforward.New(client),
		ctx:     ctx, cancel: cancel,
	}, nil
}

// applyLogConfig (re)starts log forwarding from the current config — the security
// profile (auth/service/kernel) plus allowlisted additional paths. Safe to call
// repeatedly; the forwarder reconciles its active set.
func (a *Agent) applyLogConfig() {
	a.logfwd.Apply(a.cfg.Logs.SecurityProfile, a.cfg.Logs.AdditionalPaths)
}

func (a *Agent) Run() error {
	log.Printf("NetPulse Agent %s starting (interval %ds)", a.version, a.cfg.Collection.Interval)
	hostname, _ := os.Hostname()
	interval := a.cfg.Collection.Interval
	ticker := time.NewTicker(time.Duration(interval) * time.Second)
	defer ticker.Stop()

	a.applyLogConfig()  // start security-log forwarding from the persisted config
	a.collect(hostname) // prime collectors + first sample
	a.maybeReschedule(ticker, &interval)
	for {
		select {
		case <-ticker.C:
			a.collect(hostname)
			// A server-pushed interval change (applied during collect) takes
			// effect here by resetting the SAME ticker — no timer leak.
			a.maybeReschedule(ticker, &interval)
		case <-a.ctx.Done():
			return nil
		}
	}
}

// maybeReschedule resets the collection ticker if the (possibly server-updated)
// configured interval no longer matches the running one. *cur tracks the live
// interval; ticker.Reset reuses the existing ticker (no leak).
func (a *Agent) maybeReschedule(ticker *time.Ticker, cur *int) {
	want := a.cfg.Collection.Interval
	if want > 0 && want != *cur {
		ticker.Reset(time.Duration(want) * time.Second)
		log.Printf("collection interval changed: %ds → %ds", *cur, want)
		*cur = want
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
			// Honor the operator's include/exclude mount filter (e.g. drop a
			// recovery partition). Empty filter = all drives.
			metrics["disk"] = collector.FilterDisks(
				disk, a.cfg.Disk.IncludeMounts, a.cfg.Disk.ExcludeMounts)
		}
	}
	if a.cfg.Collection.Network {
		if net, err := a.network.Collect(); err == nil {
			metrics["network"] = net
		}
	}
	if a.cfg.Collection.Services {
		// General running-services list. Send RICH ServiceStat (name + state +
		// start_type) by running the existing CollectServices over the running
		// set, so the Services tab can show state — not just names. Falls back to
		// names if the rich pass fails. (Server-side role auto-detection reads the
		// names out of these dicts.)
		if names := collector.RunningServiceNames(); len(names) > 0 {
			if rich, err := collector.CollectServices(names); err == nil && len(rich) > 0 {
				metrics["services"] = rich
			} else {
				metrics["services"] = names
			}
		}
	}
	// Service stability (role-INDEPENDENT): rich state for the operator's watched
	// services, via the EXISTING CollectServices(). The server tracks transitions
	// + alerts on down/flap. Independent of the Collection.Services toggle.
	if len(a.cfg.Stability.Services) > 0 {
		if svc, err := collector.CollectServices(a.cfg.Stability.Services); err == nil && len(svc) > 0 {
			metrics["watched_services"] = svc
		}
	}
	// os_name/os_version/kernel are ADDITIONAL display detail; runtime.GOOS stays
	// the os_family the agent + server branch on. Sent on every push so an
	// in-place OS upgrade self-corrects (the server refreshes from this).
	osInfo := collector.CollectOSInfo()
	metrics["system"] = map[string]interface{}{
		"hostname": hostname, "os": runtime.GOOS, "arch": runtime.GOARCH,
		"os_name": osInfo.Name, "os_version": osInfo.Version, "kernel": osInfo.Kernel,
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

	// Operator-set desired config (collection toggles, interval, disk filter).
	// Validated first — a bad/compromised server config must never crash the
	// agent or stop monitoring; on invalid input we keep the running config.
	if resp.DesiredConfig != nil {
		if err := validateDesiredConfig(resp.DesiredConfig); err != nil {
			log.Printf("ignoring invalid desired_config from server (keeping current): %v", err)
		} else if applyDesiredConfig(a.cfg, resp.DesiredConfig) {
			changed = true
		}
	}

	if changed {
		if err := config.Save(a.cfgPath, a.cfg); err != nil {
			log.Printf("persist server config update: %v", err)
			return
		}
		a.applyLogConfig() // reconcile log forwarding with the new config
		log.Printf("applied server config: interval=%ds collection=%+v disk(excl=%v incl=%v) logs(profile=%v add=%v) role_checks.enabled=%v",
			a.cfg.Collection.Interval, a.cfg.Collection,
			a.cfg.Disk.ExcludeMounts, a.cfg.Disk.IncludeMounts,
			a.cfg.Logs.SecurityProfile, a.cfg.Logs.AdditionalPaths, a.cfg.RoleChecks.Enabled)
	}
}

// knownCollectionKeys are the toggles an operator may set via desired_config.
// (Processes is intentionally not server-controlled in Stage A/B.)
var knownCollectionKeys = map[string]bool{
	"cpu": true, "memory": true, "disk": true, "network": true, "services": true,
}

// validateDesiredConfig rejects a malformed/compromised server config so it's
// never applied. Mirrors the server-side AgentConfigSerializer bounds.
func validateDesiredConfig(dc *transport.DesiredConfig) error {
	if dc.IntervalSeconds != 0 && (dc.IntervalSeconds < 10 || dc.IntervalSeconds > 3600) {
		return fmt.Errorf("interval_seconds %d out of range [10,3600]", dc.IntervalSeconds)
	}
	for k := range dc.Collection {
		if !knownCollectionKeys[k] {
			return fmt.Errorf("unknown collection key %q", k)
		}
	}
	return nil
}

// applyDesiredConfig mutates cfg from a (validated) desired config and reports
// whether anything changed. Collection toggles are applied only for keys the
// server sent (absent keys keep their running value); interval 0 = unset.
func applyDesiredConfig(cfg *config.Config, dc *transport.DesiredConfig) bool {
	changed := false
	setBool := func(cur *bool, key string) {
		if v, ok := dc.Collection[key]; ok && *cur != v {
			*cur = v
			changed = true
		}
	}
	setBool(&cfg.Collection.CPU, "cpu")
	setBool(&cfg.Collection.Memory, "memory")
	setBool(&cfg.Collection.Disk, "disk")
	setBool(&cfg.Collection.Network, "network")
	setBool(&cfg.Collection.Services, "services")

	if dc.IntervalSeconds != 0 && dc.IntervalSeconds != cfg.Collection.Interval {
		cfg.Collection.Interval = dc.IntervalSeconds
		changed = true
	}

	if !equalStrings(cfg.Disk.ExcludeMounts, dc.Disk.ExcludeMounts) {
		cfg.Disk.ExcludeMounts = append([]string(nil), dc.Disk.ExcludeMounts...)
		changed = true
	}
	if !equalStrings(cfg.Disk.IncludeMounts, dc.Disk.IncludeMounts) {
		cfg.Disk.IncludeMounts = append([]string(nil), dc.Disk.IncludeMounts...)
		changed = true
	}

	if dc.Logs.SecurityProfile != cfg.Logs.SecurityProfile {
		cfg.Logs.SecurityProfile = dc.Logs.SecurityProfile
		changed = true
	}
	if !equalStrings(cfg.Logs.AdditionalPaths, dc.Logs.AdditionalPaths) {
		cfg.Logs.AdditionalPaths = append([]string(nil), dc.Logs.AdditionalPaths...)
		changed = true
	}
	if !equalStrings(cfg.Stability.Services, dc.Stability.Services) {
		cfg.Stability.Services = append([]string(nil), dc.Stability.Services...)
		changed = true
	}
	if !equalStrings(cfg.Functional.Web.URLs, dc.Functional.Web.URLs) {
		cfg.Functional.Web.URLs = append([]string(nil), dc.Functional.Web.URLs...)
		changed = true
	}
	return changed
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
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
	funcURLs := map[string][]string{"web": a.cfg.Functional.Web.URLs}
	results := collector.RunRoleChecks(specs, extra, funcURLs)
	payload := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"hostname":  hostname,
		"roles":     results,
	}
	if err := a.client.SendRoleChecks(payload); err != nil {
		log.Printf("send role checks: %v", err)
	}
}

func (a *Agent) Stop() {
	a.logfwd.Stop()
	a.cancel()
}

// Enroll runs first-time enrollment (delegates to the enrollment package).
// insecure skips server-cert verification (dev / self-signed servers).
func Enroll(serverURL, token, configPath string, insecure bool) error {
	return enrollment.Enroll(serverURL, token, configPath, "", insecure)
}
