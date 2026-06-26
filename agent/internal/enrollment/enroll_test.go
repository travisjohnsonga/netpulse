package enrollment

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/travisjohnsonga/netpulse/agent/internal/config"
)

// The server may self-report a useless server_url (e.g. https://localhost for a
// remote agent). Enrollment must persist the operator's --server flag instead —
// the agent just enrolled against it, so it's demonstrably reachable.
func TestEnrollPrefersServerFlagOverResponse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"agent_id":            "abc123",
			"certificate":         "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----",
			"ca_certificate":      "-----BEGIN CERTIFICATE-----\ny\n-----END CERTIFICATE-----",
			"collection_interval": 30,
			"server_url":          "https://localhost", // the buggy self-reported value
		})
	}))
	defer srv.Close()

	cfgPath := filepath.Join(t.TempDir(), "config.json")
	// insecure=true because httptest is plain http.
	if err := Enroll(srv.URL, "tok", cfgPath, "1.0.0", true); err != nil {
		t.Fatalf("Enroll: %v", err)
	}

	var cfg config.Config
	b, err := os.ReadFile(cfgPath)
	if err != nil {
		t.Fatalf("read config: %v", err)
	}
	if err := json.Unmarshal(b, &cfg); err != nil {
		t.Fatalf("parse config: %v", err)
	}
	if cfg.ServerURL != srv.URL {
		t.Fatalf("server_url = %q, want the --server flag %q (not the server's self-reported value)",
			cfg.ServerURL, srv.URL)
	}
}
