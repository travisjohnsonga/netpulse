package collector

// BuiltinRoleSpecs mirrors the server's built-in ServerRole profiles so the
// agent can run role checks self-contained (selected by name via config
// role_checks.roles). Keep in sync with services/api/apps/agents/seed.py.
var BuiltinRoleSpecs = map[string]RoleSpec{
	"dhcp": {
		Role: "dhcp", WindowsService: []string{"DHCPServer"},
		LinuxService: []string{"isc-dhcp-server", "dhcpd", "kea-dhcp4"},
		Ports:        []PortCheck{{Port: 67, Proto: "udp", Name: "DHCP"}},
	},
	"dns": {
		Role: "dns", WindowsService: []string{"DNS"},
		LinuxService: []string{"named", "bind9", "unbound", "dnsmasq", "systemd-resolved"},
		Ports: []PortCheck{
			{Port: 53, Proto: "udp", Name: "DNS"},
			{Port: 53, Proto: "tcp", Name: "DNS TCP"},
		},
	},
	"nps": {
		Role: "nps", WindowsService: []string{"IAS"},
		LinuxService: []string{"freeradius", "radiusd"},
		Ports: []PortCheck{
			{Port: 1812, Proto: "udp", Name: "RADIUS Auth"},
			{Port: 1813, Proto: "udp", Name: "RADIUS Accounting"},
		},
	},
	"dc": {
		Role: "dc", WindowsService: []string{"NTDS", "ADWS", "DNS", "Netlogon", "W32Time"},
		Ports: []PortCheck{
			{Port: 389, Proto: "tcp", Name: "LDAP"},
			{Port: 636, Proto: "tcp", Name: "LDAPS"},
			{Port: 88, Proto: "tcp", Name: "Kerberos"},
			{Port: 445, Proto: "tcp", Name: "SMB"},
			{Port: 3268, Proto: "tcp", Name: "Global Catalog"},
		},
	},
	"web": {
		Role: "web", WindowsService: []string{"W3SVC", "WAS"},
		LinuxService: []string{"nginx", "apache2", "httpd"},
		Ports: []PortCheck{
			{Port: 80, Proto: "tcp", Name: "HTTP"},
			{Port: 443, Proto: "tcp", Name: "HTTPS"},
		},
	},
	"db": {
		Role: "db", WindowsService: []string{"MSSQLSERVER", "SQLSERVERAGENT"},
		LinuxService: []string{"postgresql", "mysql", "mariadb", "mongod"},
		Ports: []PortCheck{
			{Port: 1433, Proto: "tcp", Name: "MSSQL"},
			{Port: 5432, Proto: "tcp", Name: "PostgreSQL"},
			{Port: 3306, Proto: "tcp", Name: "MySQL"},
		},
	},
	"syslog": {
		Role: "syslog", LinuxService: []string{"rsyslog", "syslog-ng", "syslogd"},
		Ports: []PortCheck{
			{Port: 514, Proto: "udp", Name: "Syslog UDP"},
			{Port: 514, Proto: "tcp", Name: "Syslog TCP"},
		},
	},
}

// SpecsFor resolves role names to their built-in specs (unknown names skipped).
func SpecsFor(roles []string) []RoleSpec {
	out := make([]RoleSpec, 0, len(roles))
	for _, r := range roles {
		if spec, ok := BuiltinRoleSpecs[r]; ok {
			out = append(out, spec)
		}
	}
	return out
}
