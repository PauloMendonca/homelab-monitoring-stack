# ArgoCD plan for notifications stack

## Decision

Redis belongs in ArgoCD-managed infrastructure for this project.

Reasons:

- keeps infra declarative and versioned;
- avoids manual drift in persistent components;
- allows controlled rollback and reproducible recovery.

## Argo applications

- `notifications-infra`
  - namespace
  - redis-notify (Streams + AOF + PVC on `nfs-production`)
  - evolution-api (+ PVC)
  - no runtime secrets in Git (applied separately from 1Password sync flow)

- `notifications-services`
  - notify-api
  - notify-worker
  - notify-mcp
  - notify config map

## Retention and DLQ

- retention policy: 30 days (`STATUS_RETENTION_DAYS=30`)
- worker trims streams by age and keeps status hashes with TTL
- failed events are moved to `notify:dlq` after max retries

## Rollout sequence

1. sync `notifications-infra`
2. create/update runtime secrets from 1Password
3. sync `notifications-services`
4. enable `alert-router` on TrueNAS and repoint Alertmanager
5. validate end-to-end and decommission old relay
