# TODO - Integração 1Password no Monitoring Stack

## Status Atual

**1Password MCP é agora K8s-nativo** (em `apps/tools/1password-mcp` no GitOps repo).

O TrueNAS monitoring-stack acessa 1Password via `op run --env-file=.env.op` em memória.

## Fase 1 - Preparação (Concluída)

- [x] Definir vault dedicado no 1Password: `MCP API Keys`.
- [x] Criar Service Account com acesso minimo ao vault dedicado.
- [x] Token da Service Account armazenado no TrueNAS em `/mnt/pool_fast/db/secrets/1password-mcp/token`.
- [x] op CLI instalado no TrueNAS: `/mnt/pool_fast/opencode/bin/op`.

## Fase 2 - Execução no TrueNAS (Concluída)

- [x] Script `scripts/run-with-1password.sh` disponível e funcional.
- [x] Permissões: pasta 0700, token 0600.
- [x] Validar execução: `sudo ./scripts/run-with-1password.sh config`.

## Fase 3 - Integração com Monitoring Stack (Concluída)

- [x] Criar `.env.op` a partir de `.env.op.example` com referencias reais.
- [x] Subir stack com `sudo ./scripts/run-with-1password.sh up -d --build`.
- [x] Validar health dos servicos (Prometheus, Alertmanager, Grafana).

## Fase 4 - Fluxo Legado (Opcional, Deprecated)

Se necessário usar renderização em arquivo (não recomendado):

- [ ] `python3 scripts/render_env_from_1password.py --mapping .env.op --output .env.runtime`
- [ ] `sudo docker compose --env-file .env.runtime up -d --build`

**Nota:** Prefira `op run` em memória (Fase 3).

## Fase 5 - Rotação de Segredos (Contínua)

- [ ] Definir rotina de rotação (Grafana admin, Evolution API key, etc).
- [ ] Usar `op item edit` ou `password_update` via 1Password MCP.
- [ ] Documentar rollback funcional por stack.
