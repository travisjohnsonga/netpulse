package logforward

import "testing"

func TestIsAllowedLogPath(t *testing.T) {
	allowed := []string{
		"/var/log/auth.log", "/var/log/secure", "/var/log/syslog",
		"/var/log/myapp/app.log", "/var/log/nginx/access.log",
	}
	for _, p := range allowed {
		if !IsAllowedLogPath(p) {
			t.Errorf("expected %q allowed", p)
		}
	}
	denied := []string{
		"/etc/shadow",                  // not under /var/log + "shadow"
		"/root/.ssh/id_rsa",            // "/.ssh/"
		"/var/log/../../etc/passwd",    // traversal
		"/home/user/app.log",           // outside allowlist root
		"/var/log/private/secrets.txt", // "private"/"secret"
		"/var/log/server.key",          // "key"
		"relative.log",                 // not absolute under /var/log
		"",                             // empty
	}
	for _, p := range denied {
		if IsAllowedLogPath(p) {
			t.Errorf("expected %q DENIED", p)
		}
	}
}
