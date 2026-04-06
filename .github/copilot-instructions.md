# Project Guidelines

## Code Style
- Preserve existing style in each component; avoid broad refactors unrelated to the task.
- Python services in `pfsense-exporter/`, `wan-guard/`, and `nextgen/` are container-first; keep dependency changes pinned in each `requirements.txt`.
- Keep infra changes declarative: Docker Compose in `docker-compose.yml`, Kubernetes manifests under `k8s/notifications/`, and ArgoCD apps under `argocd/`.

## Architecture
- This repository is a hybrid stack:
  - TrueNAS runtime (Docker Compose): Prometheus, Alertmanager, Grafana, exporters, WAN guard, and optional `alert-router` profile.
  - MicroK8s runtime (ArgoCD-managed): notify-api, notify-worker, notify-mcp, redis-notify, and evolution-api.
- Core boundaries:
  - Monitoring and alerting config: `prometheus/`, `alertmanager/`, `grafana/`
  - pfSense integration: `pfsense-exporter/`, `wan-guard/`, `snmp-exporter/`
  - Next-gen notifications: `nextgen/`, `k8s/notifications/`, `argocd/`

## Build and Test
- Primary local deploy (TrueNAS):
  - `sudo docker compose --env-file .env up -d --build`
- 1Password flow (preferred for secrets):
  - `sudo ./scripts/setup_truenas_1password_mcp.sh`
  - `cp .env.op.example .env.op`
  - `python3 scripts/render_env_from_1password.py --mapping .env.op --output .env.runtime`
  - `sudo docker compose --env-file .env.runtime up -d --build`
- Optional next-gen router on TrueNAS:
  - `sudo docker compose --env-file .env.runtime up -d --build --profile nextgen alert-router`
- Health checks:
  - `sudo docker compose ps`
  - `curl -s http://127.0.0.1:9090/-/ready`
  - `curl -s http://127.0.0.1:9093/-/ready`
  - `curl -s http://127.0.0.1:3000/api/health`
- There is no global Makefile/npm/pytest automation at repo root. Prefer targeted validation and service health checks.

## Conventions
- Secrets policy:
  - Never commit `.env`, `.env.op`, `.env.runtime`, or plaintext credentials/tokens.
  - Use 1Password references and runtime rendering workflow.
  - Keep Kubernetes manifests secret-free; apply runtime secrets separately via scripts.
- pfSense prerequisites:
  - `secrets/pfsense_monitoring_rsa` and `secrets/known_hosts` must exist for exporter and WAN guard.
- Notifications migration:
  - Prefer linking to runbooks instead of duplicating procedures.
  - See `docs/notifications-migration-runbook.md` and `docs/notifications-argocd-plan.md` for rollout/rollback.

## Release and Commit Policy
- Follow Conventional Commits for all commit messages.
- Pull request titles must follow Conventional Commits with the appropriate type.
- Changes should remain compatible with Semantic Release expectations.

## Reference Docs
- `README.md` for baseline stack setup and validation.
- `nextgen/README.md` for runtime split and Redis Streams model.
- `docs/notifications-1password-secrets.md` for secret handling policy.
- `docs/whatsapp-system-analysis.md` for messaging flow context.
