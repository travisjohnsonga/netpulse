package collector

import (
	"fmt"
	"net"
	"runtime"
	"time"
)

// PortCheck is the result of probing a role-defined port.
type PortCheck struct {
	Port      int     `json:"port"`
	Proto     string  `json:"proto"`
	Name      string  `json:"name"`
	Open      bool    `json:"open"`
	LatencyMs float64 `json:"latency_ms"`
}

// RoleCheck is the per-role result the agent reports.
type RoleCheck struct {
	Role     string        `json:"role"`
	Services []ServiceStat `json:"services"`
	Ports    []PortCheck   `json:"ports"`
}

// CheckPort dials host:port and reports reachability + connect latency. UDP has
// no handshake, so a successful "connection" only means the socket opened.
func CheckPort(host string, port int, proto string) (bool, float64) {
	if proto == "" {
		proto = "tcp"
	}
	addr := fmt.Sprintf("%s:%d", host, port)
	start := time.Now()
	conn, err := net.DialTimeout(proto, addr, 3*time.Second)
	if err != nil {
		return false, 0
	}
	defer conn.Close()
	return true, float64(time.Since(start).Microseconds()) / 1000.0
}

// RoleSpec is the subset of a server-role profile the agent needs to run checks.
type RoleSpec struct {
	Role           string
	WindowsService []string
	LinuxService   []string
	Ports          []PortCheck
}

// RunRoleChecks probes each role's services (per-OS) and ports against
// localhost, plus any extra services supplied in config. Returns one RoleCheck
// per spec.
func RunRoleChecks(specs []RoleSpec, extraServices []string) []RoleCheck {
	out := make([]RoleCheck, 0, len(specs))
	for _, spec := range specs {
		want := spec.LinuxService
		if runtime.GOOS == "windows" {
			want = spec.WindowsService
		}
		want = append(append([]string{}, want...), extraServices...)

		svc, _ := CollectServices(want)
		ports := make([]PortCheck, 0, len(spec.Ports))
		for _, p := range spec.Ports {
			open, latency := CheckPort("127.0.0.1", p.Port, p.Proto)
			p.Open, p.LatencyMs = open, latency
			ports = append(ports, p)
		}
		out = append(out, RoleCheck{Role: spec.Role, Services: svc, Ports: ports})
	}
	return out
}
