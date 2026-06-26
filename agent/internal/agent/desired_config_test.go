package agent

import (
	"testing"
	"time"

	"github.com/travisjohnsonga/netpulse/agent/internal/config"
	"github.com/travisjohnsonga/netpulse/agent/internal/transport"
)

func TestApplyDesiredConfig(t *testing.T) {
	cfg := &config.Config{}
	cfg.Collection = config.Collection{Interval: 30, CPU: true, Memory: true, Disk: true, Network: true}

	dc := &transport.DesiredConfig{
		Collection:      map[string]bool{"network": false}, // toggle off; others untouched
		IntervalSeconds: 120,
	}
	dc.Disk.ExcludeMounts = []string{"D:"}

	if !applyDesiredConfig(cfg, dc) {
		t.Fatal("expected changed=true")
	}
	if cfg.Collection.Network {
		t.Error("network should be disabled")
	}
	if !cfg.Collection.CPU || !cfg.Collection.Disk {
		t.Error("untouched toggles must keep their values")
	}
	if cfg.Collection.Interval != 120 {
		t.Errorf("interval = %d, want 120", cfg.Collection.Interval)
	}
	if len(cfg.Disk.ExcludeMounts) != 1 || cfg.Disk.ExcludeMounts[0] != "D:" {
		t.Errorf("exclude_mounts = %v", cfg.Disk.ExcludeMounts)
	}

	// Re-applying the same config is a no-op (no spurious config writes).
	if applyDesiredConfig(cfg, dc) {
		t.Error("re-applying identical config should report changed=false")
	}
}

func TestValidateDesiredConfig(t *testing.T) {
	ok := &transport.DesiredConfig{Collection: map[string]bool{"cpu": false}, IntervalSeconds: 60}
	if err := validateDesiredConfig(ok); err != nil {
		t.Fatalf("valid config rejected: %v", err)
	}
	// interval 0 = unset, allowed.
	if err := validateDesiredConfig(&transport.DesiredConfig{IntervalSeconds: 0}); err != nil {
		t.Fatalf("interval 0 (unset) should be allowed: %v", err)
	}
	for _, bad := range []*transport.DesiredConfig{
		{IntervalSeconds: 1},                         // below floor
		{IntervalSeconds: 99999},                     // above ceiling
		{Collection: map[string]bool{"bogus": true}}, // unknown key
	} {
		if err := validateDesiredConfig(bad); err == nil {
			t.Errorf("expected validation error for %+v", bad)
		}
	}
}

// A bad server config must NOT mutate the running config (apply-safety): the
// caller validates first, so simulate that gate here.
func TestInvalidConfigPreservesRunning(t *testing.T) {
	cfg := &config.Config{}
	cfg.Collection = config.Collection{Interval: 30, CPU: true}
	bad := &transport.DesiredConfig{IntervalSeconds: 1, Collection: map[string]bool{"cpu": false}}

	if err := validateDesiredConfig(bad); err == nil {
		t.Fatal("expected invalid config to fail validation")
	}
	// Because validation failed, applyDesiredConfig is never called → unchanged.
	if cfg.Collection.Interval != 30 || !cfg.Collection.CPU {
		t.Fatal("running config must be preserved when validation fails")
	}
}

func TestMaybeReschedule(t *testing.T) {
	a := &Agent{cfg: &config.Config{}}
	a.cfg.Collection.Interval = 60
	ticker := time.NewTicker(time.Hour)
	defer ticker.Stop()
	cur := 30

	a.maybeReschedule(ticker, &cur)
	if cur != 60 {
		t.Errorf("interval should reschedule to 60, cur=%d", cur)
	}
	// No change on a second call (no spurious reset).
	a.maybeReschedule(ticker, &cur)
	if cur != 60 {
		t.Errorf("cur drifted to %d", cur)
	}
}
