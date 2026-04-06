# Next-Gen Notifications (FastAPI + Redis Streams)

This folder contains the implementation baseline for the scalable notification pipeline:

- `alert_router/`: business routing for Alertmanager webhooks (TrueNAS runtime).
- `notify_api/`: generic enqueue API (K8s runtime).
- `notify_worker/`: Redis Streams consumer with retry and DLQ (K8s runtime).
- `notify_mcp/`: MCP server for agents to send messages through notify-api.

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

## Suggested rollout

1. Deploy infra in ArgoCD (`k8s/notifications/infrastructure`).
2. Deploy services in ArgoCD (`k8s/notifications/services`).
3. Point `alert_router` to `notify-api` service endpoint.
4. Move Alertmanager webhook target from relay to `alert_router`.
5. Decommission `whatsapp-relay` after validating end-to-end delivery.
