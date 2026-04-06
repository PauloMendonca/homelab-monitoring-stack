# Rollout Grafana MCP no TrueNAS

Este runbook padroniza o deploy do `mcp-grafana` no homelab com foco em baixo risco e sem mudancas disruptivas de rede.

## Escopo e plataforma

- Host: `10.10.11.2` (TrueNAS)
- Plataforma: Docker Compose (`monitoring-stack`)
- Tipo de componente: integracao de observabilidade para agentes
- Risco esperado: baixo
- Mudancas em pfSense/firewall: nenhuma

## Pre-requisitos

1. Stack base de monitoramento ja operacional no TrueNAS.
2. Grafana acessivel localmente em `http://grafana:3000` (rede Docker).
3. Item no 1Password (vault `MCP API Keys`): `monitoring-grafana-mcp`.
4. Campos minimos do item:
   - `credential` (token de Service Account do Grafana)
   - `org_id` (ex.: `1`)
   - `url` (ex.: `http://grafana:3000`)

## Deploy

No TrueNAS, dentro do repo `monitoring-stack`:

```bash
cp .env.op.example .env.op
python3 scripts/render_env_from_1password.py --mapping .env.op --output .env.runtime
sudo docker compose --profile mcp --env-file .env.runtime up -d --build
```

## Validacao objetiva

```bash
sudo docker compose ps grafana-mcp
curl -s http://127.0.0.1:8010/healthz
curl -s http://127.0.0.1:8010/metrics
```

Validacao remota opcional (outro host na LAN):

```bash
curl -s http://10.10.11.2:8010/healthz
```

## Configuracao do cliente MCP (OpenCode)

Exemplo de servidor remoto no `opencode.json`:

```json
{
  "mcpServers": {
    "grafana": {
      "type": "remote",
      "url": "http://10.10.11.2:8010/mcp"
    }
  }
}
```

## Seguranca recomendada

- Manter `--disable-write` para modo somente leitura.
- Preferir Service Account com privilegio minimo (Viewer + escopos estritos quando aplicavel).
- Se nao precisar de acesso LAN, trocar publish para `127.0.0.1:8010:8000` e usar tunnel SSH.

## Rollback

```bash
sudo docker compose --profile mcp --env-file .env.runtime stop grafana-mcp
sudo docker compose --profile mcp --env-file .env.runtime rm -f grafana-mcp
```
