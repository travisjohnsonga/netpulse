// Build an ssh:// URL the OS can hand to its default SSH handler.
// Uses management_ip when set, else ip_address. Username/port are optional —
// when unknown we emit a bare ssh://<host>.

export interface SSHTarget {
  ip_address: string
  management_ip?: string | null
}

export function sshHost(d: SSHTarget): string {
  return d.management_ip || d.ip_address
}

export function sshUrl(d: SSHTarget, username?: string | null, port?: number | null): string {
  const host = sshHost(d)
  const user = username ? `${encodeURIComponent(username)}@` : ''
  const p = port && port !== 22 ? `:${port}` : ''
  return `ssh://${user}${host}${p}`
}

export function sshTooltip(hostname: string, d: SSHTarget, username?: string | null, port?: number | null): string {
  const host = sshHost(d)
  const who = username ? ` as ${username}` : ''
  return `SSH to ${hostname}${who} (${host}:${port || 22})`
}
