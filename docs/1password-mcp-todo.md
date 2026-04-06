# TODO - Implantacao 1Password MCP

## Fase 1 - Preparacao

- [x] Definir vault dedicado no 1Password: `MCP API Keys`.
- [ ] Criar Service Account com acesso minimo ao vault dedicado.
- [ ] Gerar token da Service Account e armazenar no TrueNAS em `/mnt/pool_fast/db/secrets/1password-mcp/token`.
- [x] Definir usuario tecnico no TrueNAS: `svc_1password_mcp`.

## Fase 2 - Execucao no TrueNAS

- [ ] Copiar `scripts/1password-mcp-stdio.sh` para `/opt/homelab/bin/` no TrueNAS.
- [ ] Garantir permissoes: pasta 0700, token 0600, owner usuario tecnico.
- [ ] Validar execucao local: `/opt/homelab/bin/1password-mcp-stdio.sh`.
- [ ] Configurar alias SSH `truenas` no cliente.

## Fase 3 - Integracao com cliente MCP

- [ ] Aplicar template `docs/codex-mcp-1password.toml.example` na config do cliente.
- [ ] Validar `vault_list`.
- [ ] Validar `password_create` e `password_read` com item de teste.

## Fase 4 - Migracao dos .env (monitoring-stack)

- [ ] Revisar inventario em `docs/1password-secret-inventory.md`.
- [ ] Rodar dry-run de import: `python3 scripts/import_env_to_1password.py --env-file .env --vault <vault> --prefix monitoring/env`.
- [ ] Rodar import com apply apos revisao.
- [ ] Criar `.env.op` a partir de `.env.op.example` com referencias reais.
- [ ] Gerar `.env.runtime` com `scripts/render_env_from_1password.py`.
- [ ] Subir stack com `docker compose --env-file .env.runtime up -d --build`.

## Fase 5 - Pos-migracao

- [ ] Validar health dos servicos (Prometheus, Alertmanager, Grafana).
- [ ] Remover segredos plaintext remanescentes dos ambientes locais.
- [ ] Definir rotina de rotacao (Grafana admin e Evolution API key).
- [ ] Documentar rollback funcional por stack.
