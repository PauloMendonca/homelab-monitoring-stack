# Next-Gen Notifications (FastAPI + Redis Streams)

This folder contains the implementation baseline for the scalable notification pipeline:

- `alert_router/`: business routing for Alertmanager webhooks (TrueNAS runtime).
- `notify_api/`: generic enqueue API (K8s runtime) + Slack DM tool endpoint.
- `notify_worker/`: Redis Streams consumer with retry and DLQ (K8s runtime).
- `notify_mcp/`: MCP server for agents to send messages through notify-api.
- `tests/`: automated tests for notify_api including the Slack DM endpoint.

## Runtime split

- TrueNAS (`10.10.11.2`): `alert_router`
- MicroK8s (`10.10.11.5`): `notify_api`, `notify_worker`, `notify_mcp`, `redis-notify`, `evolution-api`

## Streams and retention

- Main stream: `notify:messages`
- Status stream: `notify:status`
- Dead-letter stream: `notify:dlq`
- Retention: 30 days (`STATUS_RETENTION_DAYS=30`)

## 1Password secret policy

Do not commit real values in git. Create K8s secrets from 1Password items and keep manifests secret-free.

Recommended item naming:

- `notifications/notify_api_key`
- `notifications/evolution_api_key`
- `notifications/evolution_auth_key`
- `SLACK_BOT_TOKEN` — Slack Bot Token (`xoxb-...`)
- `SLACK_PAULO_USER_ID` — Slack User ID (`U...`)
- `NOTIFY_TOOL_API_KEY` — Bearer key for Hermes/MaxHermes to call `/tools/notify-paulo`

## Slack DM — `/tools/notify-paulo`

### Architecture

```
Hermes/MaxHermes
  └── notify_mcp::notify_paulo()     ← receives NOTIFY_TOOL_API_KEY
        └── HTTP POST /tools/notify-paulo  (Bearer auth: NOTIFY_TOOL_API_KEY)
              └── notify-api
                    ├── Bearer auth check
                    ├── Rate limit (5/min, 30/hr per source)
                    ├── Deduplication (~10 min window)
                    ├── Metadata sanitisation
                    └── Slack chat.postMessage  (SLACK_BOT_TOKEN never exposed to callers)
                          └── Slack DM to Paulo (SLACK_PAULO_USER_ID)
```

**Security guarantee:** Slack `xoxb-...` token and Paulo's Slack user ID are held exclusively by the `notify-api` pod. Hermes/MaxHermes and `notify_mcp` only see the `NOTIFY_TOOL_API_KEY` (Bearer auth) and the tool schema.

### Auth

```
Authorization: Bearer <NOTIFY_TOOL_API_KEY>
```

- Token must NOT be in request body or query string.
- Missing/malformed/wrong token → HTTP **401** `{ok: false, error: "invalid_token"}`.
- `NOTIFY_TOOL_API_KEY` not set in backend → HTTP **503** `{ok: false, error: "endpoint_not_configured"}`.
- Token validated strictly; fails **closed** when env is not configured.

### Request

```
POST /tools/notify-paulo
Content-Type: application/json
Authorization: Bearer <NOTIFY_TOOL_API_KEY>

{
  "title":        "Alert: GPU memory high",   // required, max 120 chars
  "message":      "...",                     // required, max 3000 chars
  "severity":     "info | warning | critical", // default: "info"
  "source":       "hermes-memory-agent | maxhermes-agent | coding-agent | system", // default: "system"
  "dedupe_key":   "optional-string",         // optional, max 128 chars
  "metadata":     {"key": "value"}            // optional; flat only; secret keys/values redacted
}
```

**Extra fields are rejected.** Do not include `api_key`, `channel`, `user_id`, `token`, `slack_token`, `destination`, or any other field not listed above.

### Response

Success (HTTP 200):
```json
{ "ok": true, "slack_ts": "1234567890.123456", "destination": "paulo_dm" }
```

Deduplicated (HTTP 200, no Slack call):
```json
{ "ok": true, "slack_ts": null, "destination": "paulo_dm", "deduped": true }
```

Error (HTTP 4xx/5xx):
```json
{ "ok": false, "error": "rate_limit_per_source: 5/minute exceeded for 'hermes-memory-agent'" }
```
All error responses use the same `{ok: false, error: "..."}` shape, regardless of cause (auth, rate limit, validation, Slack failure, config missing).

### Rate limits

| source | per minute | per hour |
|--------|-----------|---------|
| hermes-memory-agent | 5 | 30 |
| maxhermes-agent | 5 | 30 |
| coding-agent | 5 | 30 |
| system | 5 | 30 |

> **Caveat:** In-memory rate limiting is per pod replica. Each of the 2 `notify-api` replicas has independent counters. This is intentional for lightweight deployment; cross-replica coordination can be added later with Redis.

### Metadata policy

`metadata` must be a **flat object** with simple values only. Nested objects and arrays are rejected with HTTP 422.

Accepted value types: `string`, `number`, `boolean`, `null`.

Limits:
- Max 20 key-value pairs
- Max 200 characters per string value
- Max 1024 bytes for the serialized JSON

Secret key redaction: keys containing (case-insensitive substring match) `token`, `password`, `secret`, `key`, `authorization`, `bearer`, `cookie`, `session`, `xoxb`, `xoxp`, `auth`, `credential`, `passwd`, `private`, `apikey`, `api_key`, `api-key`, `access_token`, `refresh_token`, `client_secret` → value replaced with `[redacted]`.

Secret value redaction: string values matching patterns for Slack tokens (`xoxb-...`, `xoxp-...`), Bearer tokens (`Bearer ...`), OpenAI keys (`sk-...`), 1Password refs (`op://...`), or generic credential strings → value replaced with `[redacted]`.

## MCP tool schema (for Hermes/MaxHermes HTTP tool config)

```yaml
tool:
  name: notify_paulo
  type: http
  # Primary: public HTTPS URL via Cloudflare Tunnel (for external Hermes/MaxHermes callers).
  # Cloudflare Tunnel origin → http://notify-api.ai-platform.svc.cluster.local:8080
  url: https://<notify-paulo-hostname>/tools/notify-paulo
  # Alternative: internal K8s URL (for pods inside the ai-platform namespace).
  # url: http://notify-api.ai-platform.svc.cluster.local:8080/tools/notify-paulo
  method: POST
  headers:
    Authorization: Bearer ${NOTIFY_TOOL_API_KEY}
    Content-Type: application/json
  body:
    type: object
    additionalProperties: false   # unknown fields are rejected
    properties:
      title:
        type: string
        maxLength: 120
        description: "Alert title (max 120 chars)"
      message:
        type: string
        maxLength: 3000
        description: "Alert message body (max 3000 chars)"
      severity:
        type: string
        enum: [info, warning, critical]
        default: info
        description: "Alert severity"
      source:
        type: string
        enum: [hermes-memory-agent, maxhermes-agent, coding-agent, system]
        default: system
        description: "Originating agent or system"
      dedupe_key:
        type: string
        maxLength: 128
        description: "Optional dedup key (10-min window)"
      metadata:
        type: object
        additionalProperties:
          type: [string, number, boolean, "null"]
        maxProperties: 20
        description: "Optional flat key-value metadata. Nested objects/arrays rejected. Secret keys/values redacted."
    required: [title, message]
  response:
    ok:
      type: boolean
    slack_ts:
      type: string
      nullable: true
    destination:
      type: string
      enum: [paulo_dm]
    deduped:
      type: boolean
      nullable: true
    error:
      type: string
      description: "Present on errors only"
```

### curl example (dry-run, no real Slack)

```bash
curl -s -X POST http://localhost:8080/tools/notify-paulo \
  -H "Authorization: Bearer your-notify-tool-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test notification",
    "message": "Hello from notify-api dry-run",
    "severity": "info",
    "source": "system"
  }'
```

### Slack manual setup checklist

1. Go to https://api.slack.com/apps and create a new app (or use existing).
2. Under **OAuth & Permissions**, add Bot Token Scopes: `chat:write`.
3. Under **Install App**, click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).
4. Copy your Slack **User ID** (click your profile → three dots → Copy member ID, format `U...`).
5. Save both in 1Password under vault `MCP API Keys` (`yajpg5v7563meqcevu6gsqjsne`):
   - Item name: `SLACK_BOT_TOKEN` → password field: `xoxb-...`
   - Item name: `SLACK_PAULO_USER_ID` → password field: `UXXXXXXXXX`
   - Item name: `NOTIFY_TOOL_API_KEY` → password field: `<generate a random 32+ char token>`
6. Run `ops/secrets/apply_all_k8s_secrets.sh` to populate the K8s `notify-api-secrets` secret.
7. Rolling-restart `notify-api` and `notify-mcp` pods to pick up the new env vars.
8. Validate with the curl example above (dry-run).
9. **Real send test** (requires explicit confirmation): `curl -X POST ...` without the placeholder token.

## Suggested rollout

> **Prerequisites:** Slack App created, tokens saved to 1Password, secrets provisioned via `apply_all_k8s_secrets.sh`, images built and available, Cloudflare Tunnel hostname allocated.

1. **Source changes:** All code changes live in `/home/paulo/workspace/monitoring-stack/nextgen/` (notify_api, notify_mcp, tests).
2. **GitOps manifests:** Updated K8s manifests are under `/home/paulo/gitops-flow-deploy/apps/notifications/`. Commit and push to the GitOps bare repo.
3. **Secrets:** Run `/home/paulo/gitops-flow-deploy/ops/secrets/apply_all_k8s_secrets.sh` to populate `notify-api-secrets` in the `ai-platform` namespace with `SLACK_BOT_TOKEN`, `SLACK_PAULO_USER_ID`, and `NOTIFY_TOOL_API_KEY`.
4. **Images:** Build and push `notify-api` and `notify-mcp` images to `localhost:32000` using the tags referenced in the deployment manifests (currently `v0.1` / `v0.5` or as updated).
5. **Argo CD sync:** Sync the `notifications` application in Argo CD to apply the updated manifests.
6. **Rolling restart:** Restart `notify-api` and `notify-mcp` pods so they pick up the new env vars from the secret.
7. **Cloudflare Tunnel (external access):** Allocate a public hostname (e.g. `notify-paulo.<domain>`) in the Cloudflare dashboard. Create a tunnel origin rule pointing `https://<hostname>` → `http://notify-api.ai-platform.svc.cluster.local:8080`. Add an Access Policy to restrict access if Hermes supports additional auth headers; otherwise rely on the backend Bearer token and rate limiting. Cloudflare control-plane changes are manual/out-of-band today.
8. **Validate:** Run the curl dry-run example (pointing at the internal URL or the Cloudflare tunnel). A real send test requires explicit confirmation.

## Running tests

```bash
cd /home/paulo/workspace/monitoring-stack/nextgen
pip install --quiet pytest httpx
pytest tests/test_notify_paulo.py -v
```

Tests mock Slack and Redis. No real Slack API calls are made.
