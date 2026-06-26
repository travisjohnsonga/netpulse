// Package logforward tails the curated security-profile logs (auth/service/
// kernel) plus allowlisted operator paths and ships raw lines to the server,
// which relays them to the log pipeline. The agent runs as root, so the path
// allowlist (mirrored from the server's is_allowed_log_path — defense in depth)
// is the guardrail that stops "add a log path" from becoming file exfiltration.
package logforward

import "strings"

// AllowlistRoot — operator-added paths must live under here.
const AllowlistRoot = "/var/log/"

// denySubstrings are rejected even under the allowlist root.
var denySubstrings = []string{"..", "key", "secret", "shadow", "/.ssh/", "private"}

// IsAllowedLogPath mirrors the server serializer's rule: an absolute path under
// /var/log with no traversal or secret-bearing names. The agent refuses to tail
// anything that fails this, even if a bad/compromised config delivers it.
func IsAllowedLogPath(p string) bool {
	p = strings.TrimSpace(p)
	if p == "" {
		return false
	}
	low := strings.ToLower(p)
	for _, s := range denySubstrings {
		if strings.Contains(low, s) {
			return false
		}
	}
	return strings.HasPrefix(p, AllowlistRoot)
}
