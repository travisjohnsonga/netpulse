package collector

import (
	"crypto/tls"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// FunctionalResult is one functional health check (Stage 1: web HTTP + cert).
// Health gradient: healthy (2xx/3xx) · warning (4xx, up but config/auth) ·
// degraded (5xx) · down (no response/timeout).
type FunctionalResult struct {
	URL               string  `json:"url"`
	Health            string  `json:"health"`
	StatusCode        int     `json:"status_code,omitempty"`
	LatencyMs         float64 `json:"latency_ms,omitempty"`
	CertDaysRemaining *int    `json:"cert_days_remaining,omitempty"`
	Error             string  `json:"error,omitempty"`
}

// selfHosts is the SSRF allowlist: the agent checks ITS OWN host only. A
// functional-check URL must resolve to a loopback host — never an arbitrary
// internal/external address. Mirrors the server-side is_allowed_self_url.
var selfHosts = map[string]bool{
	"localhost": true, "127.0.0.1": true, "::1": true, "0.0.0.0": true,
}

// IsAllowedSelfURL reports whether url is http(s) to the host itself (loopback).
func IsAllowedSelfURL(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return false
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return false
	}
	return selfHosts[strings.ToLower(u.Hostname())]
}

// classifyHealth maps an HTTP status (or transport error) to the gradient.
func classifyHealth(status int, err error) string {
	if err != nil {
		return "down"
	}
	switch {
	case status >= 500:
		return "degraded"
	case status >= 400:
		return "warning"
	case status >= 200:
		return "healthy"
	default:
		return "warning"
	}
}

// CheckHTTP runs the web functional check for one URL (SSRF-guarded, incl.
// redirect re-validation). For HTTPS it reads cert days-remaining from the
// handshake (nearly free). InsecureSkipVerify: we read the cert of OUR OWN
// endpoint, not trust-verify it (a self-signed localhost cert is still valid to
// report on).
func CheckHTTP(rawURL string) FunctionalResult {
	res := FunctionalResult{URL: rawURL}
	if !IsAllowedSelfURL(rawURL) {
		res.Health = "down"
		res.Error = "blocked: URL is not on this host"
		return res
	}
	client := &http.Client{
		Timeout: 8 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 5 {
				return fmt.Errorf("too many redirects")
			}
			if !IsAllowedSelfURL(req.URL.String()) {
				return fmt.Errorf("redirect off-host blocked")
			}
			return nil
		},
		Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}},
	}
	start := time.Now()
	resp, err := client.Get(rawURL)
	res.LatencyMs = float64(time.Since(start).Microseconds()) / 1000.0
	if err != nil {
		res.Health = "down"
		res.Error = err.Error()
		return res
	}
	defer resp.Body.Close()
	res.StatusCode = resp.StatusCode
	res.Health = classifyHealth(resp.StatusCode, nil)
	if resp.TLS != nil && len(resp.TLS.PeerCertificates) > 0 {
		days := int(time.Until(resp.TLS.PeerCertificates[0].NotAfter).Hours() / 24)
		res.CertDaysRemaining = &days
	}
	return res
}

// functionalRegistry maps a role → its functional checker. Stage 1 populates
// only "web"; Stage 2 (dns/db/…) is "add an entry here", not a refactor.
var functionalRegistry = map[string]func(spec RoleSpec, urls []string) []FunctionalResult{
	"web": webFunctionalCheck,
}

// webTargets resolves which URLs to check: the configured list, or (zero-config)
// derived from the role's ports on localhost. Pure → unit-testable.
func webTargets(spec RoleSpec, urls []string) []string {
	if len(urls) > 0 {
		return append([]string(nil), urls...)
	}
	var t []string
	for _, p := range spec.Ports {
		scheme := "http"
		if p.Port == 443 || p.Port == 8443 {
			scheme = "https"
		}
		t = append(t, fmt.Sprintf("%s://localhost:%d/", scheme, p.Port))
	}
	return t
}

func webFunctionalCheck(spec RoleSpec, urls []string) []FunctionalResult {
	targets := webTargets(spec, urls)
	out := make([]FunctionalResult, 0, len(targets))
	for _, u := range targets {
		out = append(out, CheckHTTP(u))
	}
	return out
}

// RunFunctional runs the registered functional checker for a role (nil if the
// role has none registered).
func RunFunctional(role string, spec RoleSpec, urls []string) []FunctionalResult {
	if fn, ok := functionalRegistry[role]; ok {
		return fn(spec, urls)
	}
	return nil
}
