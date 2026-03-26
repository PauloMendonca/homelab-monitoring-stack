# Monitoring Stack (TrueNAS)

Servicos:
- Prometheus: http://10.10.11.2:9090
- Alertmanager: http://10.10.11.2:9093
- Grafana: http://10.10.11.2:3000

## Subir stack
```bash
cd ~/monitoring-stack
sudo docker compose --env-file .env up -d --build
```

## Validar
```bash
sudo docker compose ps
curl -s http://127.0.0.1:9090/-/ready
curl -s http://127.0.0.1:9093/-/ready
curl -s http://127.0.0.1:3000/api/health
```

## Notas pfSense
- Atualize o target SNMP em `prometheus/prometheus.yml` (job `pfsense-snmp`).
- Ajuste comunidade/auth SNMP no `snmp-exporter` conforme politica de seguranca.
- Recomendado SNMPv3 + ACL para IP do TrueNAS.

## WAN Guard automatico
- Container `pfsense-wan-guard` monitora `pfsense_gateway_loss_percent` no Prometheus.
- Regras aplicadas: disable com loss >= 20% por 30s; reavaliacao apos cooldown de 5m; confirmacao de enable com loss == 0 por 1m em janela de probe.
- Em falha simultanea das duas WANs, prioriza desabilitar apenas `WAN_VIVO_DHCP`.
- Metricas do guard em `pfsense-wan-guard:9950/metrics`.
