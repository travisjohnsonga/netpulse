# ChatOps

Query spane's inventory, alerts, CVEs, and lifecycle data conversationally —
either from a chat panel inside spane, or from your team's chat platform
(Slack, Microsoft Teams, Google Chat, Discord, Mattermost).

There are two independent surfaces, and you can run either or both:

| Surface | External host needed? | Identity | Best for |
|---|---|---|---|
| **In-UI chat ("Ask spane")** | No | Your spane login | Everyone — zero setup, safest |
| **External chat platforms** | **Yes — public HTTPS** | Mapped chat user | Teams already living in Slack/Teams/etc. |

The in-UI chat needs no networking and no secrets. The external platforms each
deliver messages by calling spane *from the vendor's cloud*, so they require a
publicly reachable, TLS-terminated endpoint — covered in detail below.

---

## The in-UI chat ("Ask spane")

A slide-out panel reachable from the **Ask spane** button (bottom-right of every
page). It persists across navigation, so you can keep a conversation open while
you move around the app.

It is the recommended surface because it sidesteps the entire external attack
surface:

- **First-party authenticated** — it runs as an authenticated call from your
  logged-in session (`POST /api/chatops/query/`). There is no webhook, no
  signature to verify, and no public endpoint to expose.
- **Identity and RBAC are native** — the request *is* your spane user, so there
  is no chat-to-spane identity mapping and no spoofing surface. Your role governs
  what you can ask.
- **Always available to authenticated users** — it is **not** gated behind
  `CHATOPS_ENABLED` (that switch governs the inbound webhooks only).

Every query is audit-logged against your user.

---

## Master switch

The external chat-platform webhooks are gated behind a single environment switch:

```bash
CHATOPS_ENABLED=true        # default: false
```

When `false`, the webhook endpoints return **404** (the route is not revealed)
and no external platform can reach spane. A platform is live only when this
master switch is on **and** that platform's row is enabled (below). The in-UI
chat is unaffected by this switch.

---

## External chat platforms

### Externally accessible host requirements

Slack, Teams, Google Chat, Discord, and Mattermost all work by **POSTing to
spane from the vendor's cloud** when a user invokes the bot. spane does not poll
them. This means the relevant webhook endpoint must be reachable from the public
internet.

Each platform posts to a fixed path:

| Platform | Webhook endpoint |
|---|---|
| Slack | `https://<spane-host>/api/webhooks/slack/` |
| Microsoft Teams | `https://<spane-host>/api/webhooks/teams/` |
| Google Chat | `https://<spane-host>/api/webhooks/gchat/` |
| Discord | `https://<spane-host>/api/webhooks/discord/` |
| Mattermost | `https://<spane-host>/api/webhooks/mattermost/` |

**Requirements for the spane host:**

- **A public DNS name** resolving to your spane ingress (e.g.
  `spane.example.com`).
- **A publicly-trusted TLS certificate** (Let's Encrypt, a commercial CA, or your
  enterprise public CA). Self-signed and private-CA certificates **will be
  rejected** by Slack, Microsoft, Google, and Discord — they validate the chain
  from their cloud.
- **HTTPS on 443 only.** None of these platforms will call a plain-HTTP endpoint.
- **Inbound reachability** from the vendor cloud to the webhook path. The rest of
  the spane API and UI do **not** need to be public — see the least-exposure note
  below.

!!! note "Security model: public, but cryptographically verified"
    The webhook endpoints accept unauthenticated connections at the network edge
    (they have no session/login), but **every request is verified before it is
    processed** — HMAC, Ed25519, or a shared token depending on the platform (see
    each platform below) — and rate-limited. A request that fails verification is
    rejected with **401** before any query runs. The public exposure is therefore
    safe by construction: the endpoint is reachable, but only the genuine platform
    can produce a valid signature.

**Reverse-proxy / ingress configuration**

If you terminate TLS at nginx, a load balancer, or an ingress controller in front
of spane, two things are mandatory:

- **Forward the raw request body unmodified.** Signatures are computed over the
  exact bytes of the body. Any feature that rewrites, re-encodes, pretty-prints,
  or recompresses the body **will break verification**. Disable body buffering
  transforms; pass the payload through verbatim.
- **Preserve the signature/authorization headers** — `X-Slack-Signature` and
  `X-Slack-Request-Timestamp`, `X-Signature-Ed25519` and
  `X-Signature-Timestamp` (Discord), and the `Authorization` header (Teams,
  Google Chat). Strip none of these.

!!! warning "Least-exposure ingress"
    Expose only the webhook paths publicly where you can. A path-scoped ingress
    rule that publishes `^/api/webhooks/` and keeps the rest of spane on your
    internal network gives the vendors what they need while keeping the
    management surface private.

**Optional: restrict ingress by source**

As defense-in-depth you may allowlist the vendor's published egress ranges
(Slack, Microsoft 365, Google, and Discord all publish IP ranges). Treat this as
secondary — those ranges change, and **signature verification is the primary
control**. Do not rely on IP allowlisting alone.

**Verifying reachability**

After configuring a platform, a quick check from outside your network:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://<spane-host>/api/webhooks/slack/
```

A live, verifying endpoint returns **401** (signature missing) — not 404. A
404 means `CHATOPS_ENABLED` is off or that platform's row is disabled.

---

### Slack

1. Create a Slack app at <https://api.slack.com/apps> → **From scratch**.
2. Enable **Event Subscriptions** (or a slash command) and set the **Request
   URL** to `https://<spane-host>/api/webhooks/slack/`.
3. From **Basic Information → App Credentials**, copy the **Signing Secret**.
4. In spane, store it as the Slack platform's `signing_secret` (below).

spane verifies each request with HMAC-SHA256 over the raw body using the signing
secret, with the Slack timestamp to prevent replay.

---

### Microsoft Teams

spane's Teams integration uses an **Outgoing Webhook**, which is configured
**inside Teams — not in Azure**. You do **not** register an Azure AD app or an
Azure Bot resource for this.

1. In the target team, click **••• → Manage team → Apps → Create an outgoing
   webhook**.
2. Name it (this is what members `@mention`), and set the callback URL to
   `https://<spane-host>/api/webhooks/teams/`.
3. Teams displays an **HMAC security token once** — copy it immediately.
4. In spane, store it as the Teams platform's `hmac_secret` (below).

Invoke it by `@mention`-ing the webhook in the channel
(e.g. `@spane status of core-sw-01`). spane verifies the
`Authorization: HMAC <base64>` header against the token.

!!! warning "Teams' 5-second response window"
    Teams times out an outgoing webhook after **5 seconds**. If you enable the
    natural-language fallback, keep its timeout at ~2–3 s so a fallback reply
    still lands inside the window. Regex-matched queries (the common case) answer
    well under it.

!!! note "Registered-bot path not supported"
    The Azure Bot Framework (JWT-authenticated) path is **not** implemented —
    spane verifies HMAC only. An outgoing webhook can also only *reply when
    mentioned*; it cannot push unsolicited messages, so proactive Teams
    notifications will require the Bot Framework path in a future release.

---

### Google Chat

1. In Google Cloud, enable the **Google Chat API** and configure a Chat app.
2. Under the app's **Connection settings**, choose an **HTTP endpoint** and set
   the URL to `https://<spane-host>/api/webhooks/gchat/`.
3. Set a shared **bearer token** of your choosing.
4. In spane, store it as the Google Chat platform's `bearer_token` (below).

spane verifies the `Authorization: Bearer <token>` header with a constant-time
comparison.

!!! note
    Constant-time bearer-token verification is the supported model today.
    Validating Google-issued request JWTs (the fuller Google Chat model) is
    planned; use the shared bearer token for now and do not leave the endpoint
    without one.

---

### Discord

1. Create an application at <https://discord.com/developers/applications>.
2. From **General Information**, copy the **Public Key**.
3. In spane, store it as the Discord platform's `public_key` (below) **first**.
4. Then, back in the Discord portal, set the **Interactions Endpoint URL** to
   `https://<spane-host>/api/webhooks/discord/` and save.

!!! warning "Set the public key in spane before saving the Discord URL"
    When you save the Interactions Endpoint URL, Discord immediately sends a
    signed **PING** and refuses to save the URL unless spane answers it
    correctly. spane can only answer once the public key is stored — so configure
    spane first, then save the URL in Discord. spane verifies the
    `X-Signature-Ed25519` / `X-Signature-Timestamp` headers and auto-answers the
    PING.

---

### Mattermost

1. In Mattermost, go to **Integrations → Outgoing Webhooks → Add**.
2. Set the **Callback URL** to
   `https://<spane-host>/api/webhooks/mattermost/` and choose a trigger word.
3. Copy the generated **Token**.
4. In spane, store it as the Mattermost platform's `token` (below).

spane compares the payload's token against the stored value in constant time.

---

## Configuring platforms in spane

!!! note "Settings UI is forthcoming"
    A **Settings → Integrations → ChatOps** panel is planned. Until it ships,
    configure platforms through the ChatOps API as an administrator.

Each platform is one row, keyed by its slug. Set its `enabled` flag and its
secret field(s) with a `PUT`:

```bash
curl -X PUT https://<spane-host>/api/chatops/platforms/teams/ \
  -H "Authorization: Bearer <spane-admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "display_name": "Microsoft Teams",
       "hmac_secret": "<token from Teams>"}'
```

Then confirm the stored secret is usable:

```bash
curl -X POST https://<spane-host>/api/chatops/platforms/teams/test/ \
  -H "Authorization: Bearer <spane-admin-jwt>"
```

**Secret fields by platform:**

| Platform | Secret field(s) |
|---|---|
| `slack` | `signing_secret`, `bot_token` |
| `teams` | `hmac_secret`, `bot_token` |
| `gchat` | `bearer_token`, `bot_token` |
| `discord` | `public_key`, `bot_token` |
| `mattermost` | `token` |

!!! note "Secrets live in OpenBao"
    Secret fields are write-only. They are stored in OpenBao under
    `spane/chatops/<platform>` and are **never** returned by the API — reads show
    a placeholder, not the value.

---

## Approved channels, identity, and RBAC

Two global policy flags on the ChatOps config (`PUT /api/chatops/config/`):

- **`require_approved_channel`** — when on, queries are only accepted from
  channels you've registered via `/api/chatops/channels/`. Requests from
  unregistered channels are politely rejected.
- **`allow_unmapped_read`** — when on, chat users with no linked spane account
  may run **read-only** queries; when off, they're asked to link an account
  first.

**Identity mapping** (`/api/chatops/identities/`) links a chat user
(`platform` + `platform_user_id`) to a spane user, so their spane **role**
governs what they can do. An authenticated spane user can self-link via
`POST /api/chatops/identities/link/`.

Every external query is audit-logged (`chatops_query` when allowed,
`chatops_denied` when rejected) with the platform, channel, resolved user, and
intent — never any secret. The in-UI chat needs none of this: identity is your
authenticated session.

---

## Commands and natural language

### Built-in commands

The parser recognises these out of the box (many phrasings each):

| Intent | Examples |
|---|---|
| Device status | `status of core-sw-01`, `is core-sw-01 up?`, `how's core-sw-01` |
| Device list | `down devices`, `up devices` (reachable), `all devices`, `devices at site DC1`, `which devices are down` |
| Site status | `status of site DC1`, `site DC1 status` |
| Active alerts | `any alerts?`, `what's firing` |
| CVEs | `cves on edge-rtr-2`, `vulnerabilities on edge-rtr-2` |
| End-of-life | `eol for core-sw-01`, `lifecycle core-sw-01` |
| Help | `help`, `what can you do` |

### Natural-language fallback (optional LLM)

When a message doesn't match a built-in command, spane can fall back to an LLM to
map free text to an intent. This is configured by `nlp_provider`:

| Provider | Meaning |
|---|---|
| `none` *(default)* | Regex only; unmatched messages get help text |
| `local` | A self-hosted model (Ollama) — no data leaves your environment |
| `api` | A hosted model (e.g. Anthropic) — key stored in OpenBao |

The NLP call has a **≤5 s timeout and fails closed** to help text — a slow or
unavailable model never hangs or errors a query.

#### Local model (Ollama) — recommended

spane ships an opt-in `llm` Compose profile that runs the recommended model and
wires spane to it in one command:

```bash
# in .env
CHATOPS_NLP_PROVIDER=local
CHATOPS_NLP_ENDPOINT=http://ollama:11434
CHATOPS_NLP_MODEL=qwen2.5:3b

# bring up the model alongside the stack
docker compose --profile llm up -d
```

This starts an Ollama container on spane's network (reachable as
`http://ollama:11434`), auto-pulls the model, and pins it resident so the first
query after an idle period doesn't pay a cold-start reload. The default model,
**`qwen2.5:3b`**, is Apache-2.0, ~2 GB, and strong at the constrained
"map this command to an intent" task. Until the first pull completes, queries
simply fall through to the built-in commands.

**Sizing (the Ollama host/VM, on top of spane's own requirements):**

| Model tier | Resident RAM | VM RAM | vCPU | Warm latency (CPU-only) |
|---|---|---|---|---|
| 3B 4-bit (`qwen2.5:3b`, `llama3.2:3b`, `phi3:mini`) — **recommended** | ~3 GB | 4–6 GB | 2–4 | 1–3 s |
| 7–8B 4-bit (`mistral:7b`, `qwen2.5:7b`, `llama3.1:8b`) | ~5–6 GB | 8 GB | 4–8 | 3–8 s |

- **No GPU required** — all CPU-only; a GPU only buys sub-second latency.
- **Disk:** ~20 GB covers the OS and weights (~2 GB for a 3B, ~4–5 GB for a 7B);
  the model cache persists in a named volume.
- **RAM is the hard constraint** — if the model doesn't fit it is OOM-killed
  mid-inference; under-provisioned CPU only makes it slower. Don't host-overcommit
  this VM's memory.
- The model is **pinned resident** (`OLLAMA_KEEP_ALIVE=-1`) by the profile.

!!! tip "Teams + NLP"
    If you serve Teams, keep the effective NLP timeout at ~2–3 s so a fallback
    reply stays inside Teams' 5-second window.

#### Hosted model (api)

To use a hosted provider instead, set `CHATOPS_NLP_PROVIDER=api` and `nlp_model`,
and store the provider API key in OpenBao under `spane/chatops/nlp`. No Compose
profile is needed.

---

## Security summary

- **In-UI chat is the safest surface** — authenticated, no public exposure, no
  secrets, native RBAC. Prefer it where you can.
- **External webhooks are public but cryptographically verified** (HMAC /
  Ed25519 / bearer / token) and rate-limited; failed verification is rejected
  before any query runs.
- **Expose only the webhook paths** publicly; keep the management API and UI
  internal.
- **All platform secrets and any NLP API key live in OpenBao**, never in the
  database, the API responses, or logs.
- **Local NLP keeps data on-prem** — no query text leaves your environment.
- **Every external query is audit-logged** with the resolved identity.
