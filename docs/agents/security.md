# Agent Security

## Certificate-based authentication

Each agent has a unique EC P-384 certificate:

- Issued by spane's internal CA ("spane agent ca", OpenBao PKI engine)
- Issued for 1 year
- Revocable from **Settings → Agents** (revoke marks the agent and stops
  accepting its pushes)

Enrollment is the only public step and is gated by a one-time
`AgentEnrollmentToken` (expiry + max-uses). The agent generates its keypair
locally and sends only a CSR — the private key never leaves the host.

## Communication security

- **TLS 1.3** for the agent transport (client cert + CA pinning)
- **Mutual TLS** — nginx requests and verifies the agent's client certificate
  against the agent CA, then forwards the verified serial
  (`X-Agent-Cert-Serial`, with `X-Agent-Verified: SUCCESS`) to Django, which
  matches it to the enrolled agent. Requests without a CA-verified cert are
  rejected at nginx with 403; the generic `/api/` path strips those headers so
  they can't be spoofed.
- **Outbound only** — the agent opens no inbound ports
- No credentials are stored in plaintext; the CA cert is published to nginx via
  the shared volume by `setup_agent_pki`.

The CA certificate is also available (public) at `GET /api/agents/ca-certificate/`.

## Agent user (Linux)

The systemd unit runs the agent as a dedicated low-privilege user:

- User `netpulse-agent`, no login shell, no home directory
- Hardened unit (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
  `ReadWritePaths` limited to the config dir)
- Reads only what the collectors need (`/proc`, `/sys`, `/etc/os-release`, and
  `systemctl`/SCM for service state)

## Certificate lifecycle

Certificates are issued for 1 year. To rotate before expiry, re-enroll the agent
with a fresh token (it generates a new keypair + CSR and overwrites its cert).
Automatic in-place renewal and pre-expiry UI alerts are planned but not yet
built — track certificate expiry from the agent's record in the meantime.
