// Package transport sends agent payloads to the NetPulse server over mTLS.
// The client presents its enrollment-issued certificate; the server's reverse
// proxy terminates mTLS and authenticates the agent by the cert serial.
package transport

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"
)

type Client struct {
	serverURL  string
	agentID    string
	apiKey     string
	httpClient *http.Client
}

// NewClient builds the HTTPS client. It uses mTLS when the enrollment client
// cert+key are present (the normal path); if they're absent/unloadable it
// degrades to a plain TLS client, optionally sending apiKey as a Bearer token,
// so the agent still starts before PKI is set up. insecure skips server-cert
// verification (dev / self-signed).
func NewClient(serverURL, agentID, certPath, keyPath, caPath string, insecure bool, apiKey string) (*Client, error) {
	tlsConfig := &tls.Config{
		MinVersion:         tls.VersionTLS13,
		InsecureSkipVerify: insecure, // dev / self-signed servers
	}

	mtls := false
	if certPath != "" && keyPath != "" {
		if cert, err := tls.LoadX509KeyPair(certPath, keyPath); err == nil {
			tlsConfig.Certificates = []tls.Certificate{cert}
			mtls = true
		} else {
			log.Printf("transport: client cert unavailable (%v); falling back to non-mTLS", err)
		}
	}
	if !mtls && apiKey == "" {
		log.Printf("transport: no client cert or API key — requests will be unauthenticated")
	}

	// Trust the enrollment CA for server verification when it's available.
	if caCert, err := os.ReadFile(caPath); err == nil {
		pool := x509.NewCertPool()
		if pool.AppendCertsFromPEM(caCert) {
			tlsConfig.RootCAs = pool
		}
	}

	return &Client{
		serverURL: serverURL,
		agentID:   agentID,
		apiKey:    apiKey,
		httpClient: &http.Client{
			Transport: &http.Transport{TLSClientConfig: tlsConfig},
			Timeout:   30 * time.Second,
		},
	}, nil
}

// MetricsResponse is the server's reply to a metrics push. The server is
// authoritative for role assignments and pushes them back here so the agent can
// auto-enable role checks without a manual config edit.
type MetricsResponse struct {
	Accepted         bool     `json:"accepted"`
	PointsWritten    int      `json:"points_written"`
	AssignedRoles    []string `json:"assigned_roles"`
	CollectionConfig struct {
		Services          bool `json:"services"`
		RoleChecksEnabled bool `json:"role_checks_enabled"`
	} `json:"collection_config"`
	// DesiredConfig is the operator-set config the agent should apply this cycle
	// (collection toggles, interval, disk filter). Pointer so an older server
	// that omits it (nil) is a no-op rather than applying a zero config.
	DesiredConfig *DesiredConfig `json:"desired_config"`
}

// DesiredConfig mirrors the server's effective_config() shape exactly (see
// apps/agents). Collection is a map so absent keys leave the running value
// untouched.
type DesiredConfig struct {
	Collection      map[string]bool `json:"collection"`
	IntervalSeconds int             `json:"interval_seconds"`
	Disk            struct {
		ExcludeMounts []string `json:"exclude_mounts"`
		IncludeMounts []string `json:"include_mounts"`
	} `json:"disk"`
	Logs struct {
		SecurityProfile bool     `json:"security_profile"`
		AdditionalPaths []string `json:"additional_paths"`
	} `json:"logs"`
	Stability struct {
		Services []string `json:"services"`
	} `json:"stability"`
	Functional struct {
		Web struct {
			URLs []string `json:"urls"`
		} `json:"web"`
	} `json:"functional"`
}

// post sends payload to the agent endpoint at path. When out is non-nil the JSON
// response body is decoded into it.
func (c *Client) post(path string, payload interface{}, out interface{}) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal: %w", err)
	}
	url := fmt.Sprintf("%s/api/agents/%s/%s", c.serverURL, c.agentID, path)
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(data))
	if err != nil {
		return fmt.Errorf("new request %s: %w", path, err)
	}
	req.Header.Set("Content-Type", "application/json")
	if c.apiKey != "" { // Bearer fallback when not using mTLS
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("post %s: %w", path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		return fmt.Errorf("server returned %d for %s", resp.StatusCode, path)
	}
	if out != nil {
		if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
			return fmt.Errorf("decode %s response: %w", path, err)
		}
	}
	return nil
}

// SendMetrics pushes a metrics payload and returns the server's response (which
// carries the agent's assigned roles + desired collection config).
func (c *Client) SendMetrics(payload interface{}) (*MetricsResponse, error) {
	var r MetricsResponse
	if err := c.post("metrics/", payload, &r); err != nil {
		return nil, err
	}
	return &r, nil
}

func (c *Client) SendRoleChecks(payload interface{}) error {
	return c.post("role-checks/", payload, nil)
}

// SendLogs ships a batch of RAW log lines for a source (auth/service/kernel/
// custom) to the server, which relays them to NATS for the log pipeline. No
// local parsing — raw lines only.
func (c *Client) SendLogs(source string, lines []string) error {
	return c.post("logs/", map[string]interface{}{"source": source, "lines": lines}, nil)
}
