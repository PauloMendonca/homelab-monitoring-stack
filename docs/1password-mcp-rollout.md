# 1Password Integration — TrueNAS Monitoring Stack

## Status Atual

**1Password MCP é agora K8s-nativo** (em `apps/tools/1password-mcp` no GitOps repo).

Este documento descreve como o TrueNAS monitoring-stack acessa 1Password para resolver segredos em tempo de execução, **sem SSH relay ou stdio MCP remoto**.

## Fluxo Recomendado: `op run` em Memória

O TrueNAS executa `op run --env-file=.env.op` para resolver referências `op://` em memória durante o startup do Docker Compose:

```bash
# 1) Criar mapeamento local (gitignored):
cp .env.op.example .env.op

# 2) Subir stack com op run (resolve refs em memoria):
sudo ./scripts/run-with-1password.sh up -d --build
```

**Vantagens:**
- Nenhum arquivo `.env.runtime` com secrets no disco.
- Segredos resolvidos apenas em memória do processo.
- Simples e direto para Docker Compose.

## Pré-requisitos

1. **op CLI** instalado no TrueNAS: available via PATH (v2.33.0+).
2. **OP_SERVICE_ACCOUNT_TOKEN** disponível em `/var/lib/homelab/1password/op-service-account-token`.
3. **Vault** no 1Password: `MCP API Keys` (ID: `yajpg5v7563meqcevu6gsqjsne`).
4. **Mapeamento** em `.env.op` com referências `op://vault_id/item_id/password`.

## Estrutura de Segredos no TrueNAS

```text
/var/lib/homelab/1password/
  op-service-account-token    # OP_SERVICE_ACCOUNT_TOKEN (0600, nao comitar)
```

## Fluxo Legado (Deprecated): Renderização em Arquivo

Se necessário, o fluxo antigo ainda funciona:

```bash
python3 scripts/render_env_from_1password.py --mapping .env.op --output .env.runtime
sudo docker compose --env-file .env.runtime up -d --build
```

**Desvantagem:** `.env.runtime` fica no disco com secrets em plaintext. Prefira `op run`.

## Validação

```bash
# Testar op run sem subir stack:
sudo ./scripts/run-with-1password.sh config

# Verificar health apos startup:
sudo docker compose ps
curl -s http://127.0.0.1:9090/-/ready
curl -s http://127.0.0.1:3000/api/health
```

## Rollback

1. Restaurar `.env` anterior (backup local).
2. Reiniciar stack: `sudo docker compose up -d --build`.
3. Corrigir mapeamento em `.env.op` e repetir.

## Referência: 1Password MCP em K8s

Para agentes e ferramentas que precisam acessar 1Password via MCP:
- **Endpoint K8s:** `http://onepassword-mcp.tools.svc.cluster.local:8000/mcp`
- **Endpoint LiteLLM Gateway:** `http://10.10.11.203:4000/mcp/` (centralizado)
- **Auth:** Bearer token em K8s secret `1password-mcp-auth` (tools namespace)

Ver `/homelab-1password-secrets` skill para detalhes de criação/rotação de itens.
