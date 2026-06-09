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
	"net/http"
	"os"
	"time"
)

type Client struct {
	serverURL  string
	agentID    string
	httpClient *http.Client
}

func NewClient(serverURL, agentID, certPath, keyPath, caPath string) (*Client, error) {
	cert, err := tls.LoadX509KeyPair(certPath, keyPath)
	if err != nil {
		return nil, fmt.Errorf("load client cert: %w", err)
	}
	caCert, err := os.ReadFile(caPath)
	if err != nil {
		return nil, fmt.Errorf("read CA cert: %w", err)
	}
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(caCert)

	tlsConfig := &tls.Config{
		Certificates: []tls.Certificate{cert},
		RootCAs:      pool,
		MinVersion:   tls.VersionTLS13,
	}
	return &Client{
		serverURL: serverURL,
		agentID:   agentID,
		httpClient: &http.Client{
			Transport: &http.Transport{TLSClientConfig: tlsConfig},
			Timeout:   30 * time.Second,
		},
	}, nil
}

func (c *Client) post(path string, payload interface{}) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal: %w", err)
	}
	url := fmt.Sprintf("%s/api/agents/%s/%s", c.serverURL, c.agentID, path)
	resp, err := c.httpClient.Post(url, "application/json", bytes.NewReader(data))
	if err != nil {
		return fmt.Errorf("post %s: %w", path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		return fmt.Errorf("server returned %d for %s", resp.StatusCode, path)
	}
	return nil
}

func (c *Client) SendMetrics(payload interface{}) error    { return c.post("metrics/", payload) }
func (c *Client) SendRoleChecks(payload interface{}) error { return c.post("role-checks/", payload) }
