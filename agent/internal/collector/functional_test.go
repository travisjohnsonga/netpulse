package collector

import (
	"errors"
	"testing"
)

func TestClassifyHealth(t *testing.T) {
	cases := []struct {
		status int
		err    error
		want   string
	}{
		{200, nil, "healthy"},
		{301, nil, "healthy"},
		{404, nil, "warning"},
		{403, nil, "warning"},
		{500, nil, "degraded"},
		{503, nil, "degraded"},
		{0, errors.New("timeout"), "down"},
	}
	for _, c := range cases {
		if got := classifyHealth(c.status, c.err); got != c.want {
			t.Errorf("classifyHealth(%d,%v) = %q, want %q", c.status, c.err, got, c.want)
		}
	}
}

func TestIsAllowedSelfURL(t *testing.T) {
	allowed := []string{
		"http://localhost/", "https://localhost:443/health",
		"http://127.0.0.1:8080/", "https://[::1]/", "http://0.0.0.0:80/",
	}
	for _, u := range allowed {
		if !IsAllowedSelfURL(u) {
			t.Errorf("IsAllowedSelfURL(%q) = false, want true", u)
		}
	}
	// SSRF: anything off-host (or non-http scheme) must be rejected.
	denied := []string{
		"http://example.com/", "https://169.254.169.254/latest/meta-data/",
		"http://10.0.0.5/", "http://internal.svc/", "ftp://localhost/",
		"file:///etc/passwd", "http://localhost.evil.com/", "",
	}
	for _, u := range denied {
		if IsAllowedSelfURL(u) {
			t.Errorf("IsAllowedSelfURL(%q) = true, want false (SSRF)", u)
		}
	}
}

func TestWebTargets(t *testing.T) {
	spec := RoleSpec{Role: "web", Ports: []PortCheck{{Port: 80, Proto: "tcp"}, {Port: 443, Proto: "tcp"}}}
	// Zero-config: derive http://localhost:80/ + https://localhost:443/ from ports.
	got := webTargets(spec, nil)
	want := []string{"http://localhost:80/", "https://localhost:443/"}
	if len(got) != 2 || got[0] != want[0] || got[1] != want[1] {
		t.Errorf("webTargets(default) = %v, want %v", got, want)
	}
	// Configured URLs take precedence over the port-derived default.
	cfg := []string{"https://localhost/app"}
	if g := webTargets(spec, cfg); len(g) != 1 || g[0] != cfg[0] {
		t.Errorf("webTargets(configured) = %v, want %v", g, cfg)
	}
}
