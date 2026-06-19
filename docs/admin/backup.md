# Backup & Restore

spane can back up its full platform state — PostgreSQL (all config, devices,
credential *references*, compliance results, audit logs), OpenBao secrets
(encrypted snapshot), configuration files (`.env`, `docker-compose.yml`,
`nginx.conf`), and the SSL/agent certificates — to a local directory or a remote
destination (SCP/SFTP, Git, or S3-compatible storage). InfluxDB metrics are
optional (large).

## Quick start

```bash
./netpulse.sh backup                 # run a backup now
./netpulse.sh list-backups           # list local backups
./netpulse.sh restore <backup_file>  # restore from a backup archive
```

Or from the UI: **Settings → System → Backup & Restore** (schedule, includes,
destination, encryption, history, and restore-from-file).

## Encryption (required for sensitive data)

Any backup that includes secrets — PostgreSQL, OpenBao, SSL certificates, or the
`.env` file — **must** be encrypted with a user-provided password. Encryption
uses **AES-256-CBC** with **PBKDF2** key derivation (600,000 iterations, the
NIST-recommended floor), via `openssl enc`.

- The password is required (minimum 12 characters) whenever the backup includes
  Database, Secrets, or Certificates.
- **spane never stores your backup password.** Only an optional, non-secret
  *hint* is recorded alongside the archive.
- The password is passed to the backup script via an environment variable, never
  on the command line (so it can't leak via `ps`).

A plaintext `…manifest.json` is written next to each encrypted archive
(`<name>.enc.tar.gz` + `<name>.manifest.json`) so you can see what a backup
contains, and its encryption hint, without the password.

**Important**

- Store the password separately from the backup files (a password manager or a
  physical safe).
- If you lose the password, the backup **cannot** be restored.

**Recommended practice**

- Use a strong, unique password per backup set.
- Test the restore procedure quarterly.

## Scheduled backups

Configure a schedule (daily/weekly/monthly), retention, includes, and
destination under **Settings → System → Backup & Restore**. For scheduled
backups the encryption password is configured once and stored in OpenBao
(`spane/backup/encryption`, never in the database and never returned by the API);
if encryption is required and no password is configured, the scheduled backup is
skipped rather than written unencrypted.

## Restore

```bash
./netpulse.sh restore spane_backup_YYYYMMDD_HHMMSS.enc.tar.gz
```

You'll be asked to confirm (this overwrites current data) and, for an encrypted
archive, to supply the password. The restore decrypts, shows the manifest, and
restores PostgreSQL.
