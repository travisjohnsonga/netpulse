"""Built-in ServerRole profiles, seeded idempotently (data migration + reseed)."""
from __future__ import annotations

BUILTIN_ROLES = [
    {
        "name": "DHCP Server", "role_type": "dhcp",
        "description": "Windows/Linux DHCP service",
        "windows_services": ["DHCPServer"],
        "linux_services": ["isc-dhcp-server", "dhcpd", "kea-dhcp4"],
        "port_checks": [{"port": 67, "proto": "udp", "name": "DHCP"}],
        "custom_checks": [
            {"name": "dhcp_pool_utilization", "type": "windows_powershell",
             "script": "Get-DhcpServerv4Scope | Select-Object ScopeId,PercentageInUse",
             "metric": "dhcp_pool_pct"},
        ],
    },
    {
        "name": "DNS Server", "role_type": "dns",
        "description": "Windows DNS / BIND / Unbound",
        "windows_services": ["DNS"],
        "linux_services": ["named", "bind9", "unbound", "dnsmasq", "systemd-resolved"],
        "port_checks": [
            {"port": 53, "proto": "udp", "name": "DNS"},
            {"port": 53, "proto": "tcp", "name": "DNS TCP"},
            {"port": 853, "proto": "tcp", "name": "DNS-over-TLS", "optional": True},
        ],
        "custom_checks": [
            {"name": "dns_query_test", "type": "dns_resolve", "query": "google.com",
             "expected_type": "A", "metric": "dns_resolves"},
        ],
    },
    {
        "name": "Network Policy Server", "role_type": "nps",
        "description": "Windows NPS/RADIUS server",
        "windows_services": ["IAS"],
        "linux_services": ["freeradius", "radiusd"],
        "port_checks": [
            {"port": 1812, "proto": "udp", "name": "RADIUS Auth"},
            {"port": 1813, "proto": "udp", "name": "RADIUS Accounting"},
            {"port": 1645, "proto": "udp", "name": "RADIUS Alt Auth", "optional": True},
        ],
        "custom_checks": [],
    },
    {
        "name": "Domain Controller", "role_type": "dc",
        "description": "Active Directory DC",
        "windows_services": ["NTDS", "ADWS", "DNS", "Kerberos", "Netlogon", "W32Time"],
        "linux_services": [],
        "port_checks": [
            {"port": 389, "proto": "tcp", "name": "LDAP"},
            {"port": 636, "proto": "tcp", "name": "LDAPS"},
            {"port": 88, "proto": "tcp", "name": "Kerberos"},
            {"port": 445, "proto": "tcp", "name": "SMB"},
            {"port": 3268, "proto": "tcp", "name": "Global Catalog"},
        ],
        "custom_checks": [],
    },
    {
        "name": "Web Server", "role_type": "web",
        "description": "IIS / nginx / Apache",
        "windows_services": ["W3SVC", "WAS"],
        "linux_services": ["nginx", "apache2", "httpd"],
        "port_checks": [
            {"port": 80, "proto": "tcp", "name": "HTTP"},
            {"port": 443, "proto": "tcp", "name": "HTTPS"},
        ],
        "custom_checks": [
            {"name": "http_response", "type": "http_check", "url": "http://localhost",
             "expected_status": 200, "metric": "http_response_ms"},
        ],
    },
    {
        "name": "Database Server", "role_type": "db",
        "description": "MSSQL / PostgreSQL / MySQL / Mongo",
        "windows_services": ["MSSQLSERVER", "SQLSERVERAGENT", "MSSQLFDLauncher"],
        "linux_services": ["postgresql", "mysql", "mariadb", "mongod"],
        "port_checks": [
            {"port": 1433, "proto": "tcp", "name": "MSSQL", "optional": True},
            {"port": 5432, "proto": "tcp", "name": "PostgreSQL", "optional": True},
            {"port": 3306, "proto": "tcp", "name": "MySQL", "optional": True},
        ],
        "custom_checks": [],
    },
    {
        "name": "Syslog Server", "role_type": "syslog",
        "description": "rsyslog / syslog-ng",
        "windows_services": [],
        "linux_services": ["rsyslog", "syslog-ng", "syslogd"],
        "port_checks": [
            {"port": 514, "proto": "udp", "name": "Syslog UDP"},
            {"port": 514, "proto": "tcp", "name": "Syslog TCP"},
            {"port": 6514, "proto": "tcp", "name": "Syslog TLS", "optional": True},
        ],
        "custom_checks": [],
    },
]


def seed_builtin_roles(role_model) -> int:
    """Upsert built-in roles (idempotent). Returns the count seeded/updated."""
    n = 0
    for spec in BUILTIN_ROLES:
        role_model.objects.update_or_create(
            role_type=spec["role_type"], is_builtin=True,
            defaults={
                "name": spec["name"], "description": spec["description"],
                "windows_services": spec["windows_services"],
                "linux_services": spec["linux_services"],
                "port_checks": spec["port_checks"], "custom_checks": spec["custom_checks"],
            },
        )
        n += 1
    return n
