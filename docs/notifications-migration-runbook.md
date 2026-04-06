# Notifications migration runbook

## Scope

- keep alert business rules in TrueNAS (`alert-router`)
- move delivery plane to K8s (`notify-api`, `notify-worker`, `redis-notify`, `evolution-api`, `notify-mcp`)

## Preconditions

- ArgoCD healthy on MicroK8s
- NFS StorageClass `nfs-production` ready
- secrets available in 1Password vault (`MCP API Keys`)

## Steps

1. Sync `notifications-infra` application in ArgoCD.
2. Apply runtime secrets from 1Password:
   - run `scripts/apply_notify_k8s_secrets.sh` from a host with `op` and `kubectl`.
3. Sync `notifications-services` in ArgoCD.
4. Start `alert-router` in TrueNAS:
   - `sudo docker compose --env-file .env --profile nextgen up -d --build alert-router`
5. Use `alertmanager/alertmanager.yml` (next-gen router target).
6. Reload Alertmanager and validate test alerts.
7. Decommission `whatsapp-relay`.

## Validation checks

- `notify-api` health endpoint returns 200.
- `notify-worker` consumes stream without errors.
- `notify:status` stream gets `queued` and `sent` events.
- critical alerts reach general and critical numbers.
- warning alerts reach only general number.

## Rollback

1. Restore relay config from `alertmanager/alertmanager.legacy-relay.yml` to `alertmanager/alertmanager.yml`.
2. Reload Alertmanager.
3. Stop `alert-router` profile if needed.
4. Keep K8s services running for diagnosis; no data loss in streams/DLQ.
