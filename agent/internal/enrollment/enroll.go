// Package enrollment performs first-time agent enrollment: generate a keypair,
// send a CSR with the one-time token, and persist the signed cert + config.
package enrollment

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/travisjohnsonga/netpulse/agent/internal/config"
)

type enrollRequest struct {
	EnrollmentToken string `json:"enrollment_token"`
	Hostname        string `json:"hostname"`
	OS              string `json:"os"`
	Arch            string `json:"arch"`
	Version         string `json:"version"`
	CSR             string `json:"csr"`
}

type enrollResponse struct {
	AgentID     string `json:"agent_id"`
	Certificate string `json:"certificate"`
	CACert      string `json:"ca_certificate"`
	Interval    int    `json:"collection_interval"`
	ServerURL   string `json:"server_url"`
}

// Enroll generates a key + CSR, enrolls with the server, and writes
// key/cert/CA + a config file next to configPath. version is embedded in the
// request for inventory. insecure (or an http:// server URL) skips server-cert
// verification for dev / self-signed servers.
func Enroll(serverURL, token, configPath, version string, insecure bool) error {
	if serverURL == "" {
		return fmt.Errorf("--server is required for enrollment")
	}
	// Honor http:// vs https:// in the server URL: don't force HTTPS, and skip
	// cert verification when explicitly insecure or talking plain http.
	skipVerify := insecure || strings.HasPrefix(serverURL, "http://")
	transport := &http.Transport{}
	if skipVerify {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
	}
	httpClient := &http.Client{Transport: transport, Timeout: 30 * time.Second}
	dir := filepath.Dir(configPath)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("create config dir: %w", err)
	}

	privateKey, err := ecdsa.GenerateKey(elliptic.P384(), rand.Reader)
	if err != nil {
		return fmt.Errorf("generate key: %w", err)
	}
	hostname, _ := os.Hostname()

	csrDER, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject:  pkix.Name{CommonName: fmt.Sprintf("agent.%s", hostname), Organization: []string{"NetPulse"}},
		DNSNames: []string{hostname},
	}, privateKey)
	if err != nil {
		return fmt.Errorf("create CSR: %w", err)
	}
	csrPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csrDER})

	reqBody, _ := json.Marshal(enrollRequest{
		EnrollmentToken: token, Hostname: hostname,
		OS: runtime.GOOS, Arch: runtime.GOARCH, Version: version, CSR: string(csrPEM),
	})
	httpResp, err := httpClient.Post(serverURL+"/api/agents/enroll/", "application/json", bytes.NewReader(reqBody))
	if err != nil {
		return fmt.Errorf("enroll request: %w", err)
	}
	defer httpResp.Body.Close()
	if httpResp.StatusCode != http.StatusOK && httpResp.StatusCode != http.StatusCreated {
		return fmt.Errorf("enrollment failed: HTTP %d", httpResp.StatusCode)
	}
	var result enrollResponse
	if err := json.NewDecoder(httpResp.Body).Decode(&result); err != nil {
		return fmt.Errorf("decode response: %w", err)
	}

	keyPath := filepath.Join(dir, "agent.key")
	certPath := filepath.Join(dir, "agent.crt")
	caPath := filepath.Join(dir, "ca.crt")

	keyDER, err := x509.MarshalECPrivateKey(privateKey)
	if err != nil {
		return fmt.Errorf("marshal key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})
	if err := os.WriteFile(keyPath, keyPEM, 0o600); err != nil {
		return fmt.Errorf("save key: %w", err)
	}
	if err := os.WriteFile(certPath, []byte(result.Certificate), 0o644); err != nil {
		return fmt.Errorf("save cert: %w", err)
	}
	if err := os.WriteFile(caPath, []byte(result.CACert), 0o644); err != nil {
		return fmt.Errorf("save CA cert: %w", err)
	}

	interval := result.Interval
	if interval == 0 {
		interval = 30
	}
	// Prefer the operator's --server flag: the agent just completed a successful
	// HTTPS enrollment against it, so it's a known-good address. The server's
	// self-reported server_url can be wrong for remote agents (the server can't
	// know how each client reaches it). Fall back to the server value only if
	// the flag was somehow empty (it can't be — Enroll() requires it above).
	serverOut := serverURL
	if serverOut == "" {
		serverOut = result.ServerURL
	}
	cfg := config.Config{
		ServerURL: serverOut, AgentID: result.AgentID,
		CertPath: certPath, KeyPath: keyPath, CACertPath: caPath,
		// Persist so the running agent keeps skipping verification when it pushes
		// metrics to a dev / self-signed (or plain-http) server.
		InsecureTLS: insecure || strings.HasPrefix(serverOut, "http://"),
	}
	cfg.Collection = config.Collection{Interval: interval, CPU: true, Memory: true, Disk: true, Network: true}
	cfg.Log.Level = "info"
	out, _ := json.MarshalIndent(cfg, "", "  ")
	if err := os.WriteFile(configPath, out, 0o600); err != nil {
		return fmt.Errorf("write config: %w", err)
	}

	fmt.Printf("Agent enrolled. ID: %s\nConfig: %s\n", result.AgentID, configPath)
	return nil
}
