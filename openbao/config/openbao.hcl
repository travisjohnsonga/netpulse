# NetPulse OpenBao Configuration
#
# Security posture:
#   disable_mlock = false  → mlock IS enabled (secrets never reach swap).
#   Requires the Docker service to have cap_add: [IPC_LOCK].
#
# First-run initialization (one-time, after `docker compose up openbao`):
#   docker compose exec openbao bao operator init
#   # Save the unseal keys and root token securely outside this repo.
#   docker compose exec openbao bao operator unseal   # repeat 3× with different keys
#
# TLS: disabled here for the scaffold. Enable for any non-localhost deployment
# by providing cert/key paths and setting tls_disable = false.

ui = true

listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable     = true
}

storage "file" {
  path = "/openbao/data"
}

api_addr     = "http://openbao:8200"
cluster_addr = "http://openbao:8201"

disable_mlock = false
