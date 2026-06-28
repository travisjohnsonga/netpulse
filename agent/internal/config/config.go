// Package config loads the agent's on-disk configuration. JSON (encoding/json,
// stdlib) is used instead of YAML to keep the core agent dependency-free.
package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
)

type Collection struct {
	Interval  int  `json:"interval"` // seconds
	CPU       bool `json:"cpu"`
	Memory    bool `json:"memory"`
	Disk      bool `json:"disk"`
	Network   bool `json:"network"`
	Processes bool `json:"processes"`
	Services  bool `json:"services"`
}

// DiskFilter selects which mounts/drives the disk collector reports. Empty
// IncludeMounts = no include-filter (report all); ExcludeMounts always wins.
// See collector.FilterDisks for the matching/normalization rule.
type DiskFilter struct {
	ExcludeMounts []string `json:"exclude_mounts"`
	IncludeMounts []string `json:"include_mounts"`
}

// LogForwarding controls security-log forwarding (Stage 1). SecurityProfile (on
// by default for Linux) tails auth/service/kernel logs; AdditionalPaths is an
// operator escape hatch constrained to the /var/log allowlist (enforced
// agent-side in logforward, mirroring the server serializer).
type LogForwarding struct {
	SecurityProfile bool     `json:"security_profile"`
	AdditionalPaths []string `json:"additional_paths"`
}

type RoleChecks struct {
	Enabled       bool                `json:"enabled"`
	Roles         []string            `json:"roles"`
	ExtraServices map[string][]string `json:"extra_services"` // {"linux": [...], "windows": [...]}
}

// Stability is the role-INDEPENDENT watched-services list. The agent runs the
// existing rich CollectServices() over Services on every check-in and reports
// state so the server can track up/down + restart/flap and alert.
type Stability struct {
	Services []string `json:"services"`
}

// Functional holds per-role functional health-check config (Stage 1: web). URLs
// are SSRF-constrained to the host itself; empty = derive from the role's ports.
type Functional struct {
	Web struct {
		URLs []string `json:"urls"`
	} `json:"web"`
}

type Config struct {
	ServerURL  string `json:"server_url"`
	AgentID    string `json:"agent_id"`
	CertPath   string `json:"cert_path"`
	KeyPath    string `json:"key_path"`
	CACertPath string `json:"ca_cert_path"`

	// InsecureTLS skips server-cert verification (dev / self-signed servers).
	// Set during enrollment from the -insecure flag or an http:// server URL.
	InsecureTLS bool `json:"insecure_tls"`

	// APIKey is an optional Bearer token used only as a fallback when no mTLS
	// client cert is present (e.g. before PKI is set up). mTLS is preferred.
	APIKey string `json:"api_key,omitempty"`

	Collection Collection    `json:"collection"`
	Disk       DiskFilter    `json:"disk"`
	Logs       LogForwarding `json:"logs"`
	Stability  Stability     `json:"stability"`
	Functional Functional    `json:"functional"`
	RoleChecks RoleChecks    `json:"role_checks"`

	Log struct {
		Level  string `json:"level"`
		Output string `json:"output"`
	} `json:"log"`
}

// DefaultPath returns the platform default config location.
func DefaultPath() string {
	if runtime.GOOS == "windows" {
		return `C:\ProgramData\NetPulse\config.json`
	}
	return "/etc/netpulse-agent/config.json"
}

// DefaultDir returns the directory of DefaultPath.
func DefaultDir() string {
	if runtime.GOOS == "windows" {
		return `C:\ProgramData\NetPulse`
	}
	return "/etc/netpulse-agent"
}

func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	if cfg.Collection.Interval == 0 {
		cfg.Collection.Interval = 30
	}
	return &cfg, nil
}

// Save writes cfg back to path as indented JSON (0600 — the file may hold an
// API key). The write is ATOMIC: data goes to a temp file in the same directory
// which is then renamed over path, so a crash mid-write can never leave a
// truncated/corrupt config on a monitored host. Used when the agent applies
// server-pushed config changes (roles, collection toggles, interval, disk
// filters). os.Rename replaces the target atomically on Linux and Windows
// (Go uses MoveFileEx with REPLACE_EXISTING on Windows).
func Save(path string, cfg *Config) error {
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".config-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName) // no-op once the rename succeeds; cleans up on failure
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
