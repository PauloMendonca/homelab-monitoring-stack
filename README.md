# Monitoring Stack (TrueNAS)

Servicos:
- Prometheus: http://10.10.11.2:9090
- Alertmanager: http://10.10.11.2:9093
- Grafana: http://10.10.11.2:3000
- Grafana MCP (profile `mcp`): http://10.10.11.2:8010/mcp
- cAdvisor: interno na rede Docker (`cadvisor:8080`)

## Subir stack
```bash
cd ~/monitoring-stack
sudo docker compose --env-file .env up -d --build

# incluir o Grafana MCP (opcional)
sudo docker compose --profile mcp --env-file .env up -d --build
```

## Segredos com 1Password (novo fluxo)

Preparacao no TrueNAS (uma vez):
```bash
sudo ./scripts/setup_truenas_1password_mcp.sh
```

1) Criar mapeamento local de refs:
```bash
cp .env.op.example .env.op
```

2) Gerar env de runtime a partir do 1Password:
```bash
python3 scripts/render_env_from_1password.py --mapping .env.op --output .env.runtime
```

3) Subir stack usando env gerado:
```bash
sudo docker compose --env-file .env.runtime up -d --build

# incluir o Grafana MCP (opcional)
sudo docker compose --profile mcp --env-file .env.runtime up -d --build
```

Observacoes:
- `.env.op` e `.env.runtime` estao no `.gitignore`.
- Nao comitar token de Service Account nem valores secretos em plaintext.

## Grafana MCP (profile `mcp`)
- O container usa `grafana/mcp-grafana` em modo `streamable-http` com endpoint `/mcp`.
- Por padrao os tools de escrita ficam desabilitados (`--disable-write`).
- Defina `GRAFANA_MCP_SERVICE_ACCOUNT_TOKEN` no env runtime antes de ativar o profile.
- Runbook de rollout: `docs/grafana-mcp-rollout.md`.

## Validar
```bash
sudo docker compose ps
curl -s http://127.0.0.1:9090/-/ready
curl -s http://127.0.0.1:9093/-/ready
curl -s http://127.0.0.1:3000/api/health
curl -s 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22cadvisor%22%7D'
# se profile mcp estiver ativo
curl -s http://127.0.0.1:8010/healthz
```

## Dashboard novo (fase 1)
- `grafana/dashboards/truenas-cadvisor-overview.json`
- Foco: CPU, memoria, rede, disco e uptime dos containers Docker no TrueNAS.

## Notas pfSense
- Atualize o target SNMP em `prometheus/prometheus.yml` (job `pfsense-snmp`).
- Ajuste comunidade/auth SNMP no `snmp-exporter` conforme politica de seguranca.
- Recomendado SNMPv3 + ACL para IP do TrueNAS.

## WAN Guard automatico
- Container `pfsense-wan-guard` monitora `pfsense_gateway_loss_percent` no Prometheus.
- Regras aplicadas: disable com loss >= 20% por 30s; reavaliacao apos cooldown de 5m; confirmacao de enable com loss == 0 por 1m em janela de probe.
- Em falha simultanea das duas WANs, prioriza desabilitar apenas `WAN_NETMINAS_DHCP`.
- Metricas do guard em `pfsense-wan-guard:9950/metrics`.

## Proxima geracao de notificacoes
- Implementacao base em `nextgen/`.
- Manifests K8s/ArgoCD em `k8s/notifications/` e `argocd/notifications-*.yaml`.
- Segredos e mapeamento 1Password em `docs/notifications-1password-secrets.md`.
