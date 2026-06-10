// Package config loads the agent's on-disk configuration. JSON (encoding/json,
// stdlib) is used instead of YAML to keep the core agent dependency-free.
package config

import (
	"encoding/json"
	"os"
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

type RoleChecks struct {
	Enabled       bool                `json:"enabled"`
	Roles         []string            `json:"roles"`
	ExtraServices map[string][]string `json:"extra_services"` // {"linux": [...], "windows": [...]}
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

	Collection Collection `json:"collection"`
	RoleChecks RoleChecks `json:"role_checks"`

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
