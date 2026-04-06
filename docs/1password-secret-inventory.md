# Inventario de Segredos para Migracao

Arquivo base: `monitoring-stack/.env`

Este inventario registra apenas chaves, sem valores.

## Classificacao

- `critico`: segredo de autenticacao ou acesso administrativo.
- `operacional`: parametro de operacao sem valor secreto.
- `contexto`: metadado util para roteamento/notificacao.

## Mapeamento proposto para 1Password

| Chave | Classe | Item sugerido no vault | Campo sugerido |
|---|---|---|---|
| `GRAFANA_ADMIN_USER` | operacional | `monitoring/grafana/admin` | `username` |
| `GRAFANA_ADMIN_PASSWORD` | critico | `monitoring/grafana/admin` | `password` |
| `GRAFANA_ROOT_URL` | operacional | `monitoring/grafana/admin` | `url` |
| `GRAFANA_MCP_SERVICE_ACCOUNT_TOKEN` | critico | `monitoring/grafana/mcp` | `credential` |
| `GRAFANA_MCP_ORG_ID` | operacional | `monitoring/grafana/mcp` | `org_id` |
| `GRAFANA_MCP_GRAFANA_URL` | operacional | `monitoring/grafana/mcp` | `url` |
| `PROMETHEUS_RETENTION` | operacional | `monitoring/prometheus/config` | `retention` |
| `PFSENSE_HOST` | operacional | `monitoring/pfsense/exporter` | `host` |
| `PFSENSE_SSH_USER` | operacional | `monitoring/pfsense/exporter` | `username` |
| `EVOLUTION_BASE_URL` | operacional | `monitoring/evolution/relay` | `url` |
| `EVOLUTION_INSTANCE` | contexto | `monitoring/evolution/relay` | `instance` |
| `EVOLUTION_API_KEY` | critico | `monitoring/evolution/relay` | `credential` |
| `WHATSAPP_TO` | contexto | `monitoring/evolution/relay` | `phone` |
| `ALERT_SOURCE` | contexto | `monitoring/evolution/relay` | `source` |

## Convencoes

- Vault: `MCP API Keys`.
- Prefixo de itens: `monitoring/...`.
- Nao duplicar item se a credencial pertence ao mesmo sistema.

## Ordem de migracao recomendada

1. `EVOLUTION_API_KEY`
2. `GRAFANA_ADMIN_PASSWORD`
3. `GRAFANA_MCP_SERVICE_ACCOUNT_TOKEN`
4. Restante das chaves (operacionais/contexto)

## Verificacao por onda

1. Segredos criados no vault e acessiveis pela Service Account.
2. `.env` atualizado sem valor secreto em plaintext.
3. `docker compose --env-file .env config` sem erro.
4. Containers sobem e endpoints de health respondem.
