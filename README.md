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

## Mode-Switch remoto via WhatsApp (Fase 2)

### Arquitetura
- **alert-router** (TrueNAS) recebe comando WhatsApp via Evolution API
- Executa SSH para **10.10.11.5** (MicroK8s) com chave dedicada
- SSH usa host key pinning (arquivo known_hosts, NAO bypass de verificacao)
- Forced command no authorized_keys: `command="/opt/mode-switch/mode-switch-executor.sh ..."`
- Nenhuma credencial de usuario, shell, ou execucao arbitraria

### Comandos WhatsApp
| Comando | Descricao |
|---------|-----------|
| `/modo ajuda` | Lista de comandos |
| `/modo status` | Consulta estado real do mode-switch em 10.10.11.5 |
| `/modo normal` | Executa transicao para modo normal (idempotente) |
| `/modo gaming` | Bloqueado/informativo — fase 3 |

### Seguranca
- Chave SSH em `secrets/mode_switch_id_ed25519` (gitignored)
- known_hosts em `secrets/mode_switch_known_hosts` (gitignored)
- Host key pinning: `StrictHostKeyChecking=yes` + `UserKnownHostsFile=...`
- forced command: `command="/opt/mode-switch/mode-switch-executor.sh ..."`
- Sem shell, sem pipe, sem redirecionamento via SSH
- BatchMode=yes (sem prompts de senha)

### Setup (uma vez)
1. Gerar chave SSH no TrueNAS:
   ```bash
   cd ~/monitoring-stack
   ssh-keygen -t ed25519 -f secrets/mode_switch_id_ed25519 -N "" -C "alert-router mode-switch"
   ```
2. Gerar known_hosts com host keys do 10.10.11.5:
   ```bash
   ssh-keyscan -t ed25519,rsa 10.10.11.5 > secrets/mode_switch_known_hosts
   chmod 644 secrets/mode_switch_known_hosts
   chmod 600 secrets/mode_switch_id_ed25519
   ```
3. Copiar chave publica para 10.10.11.5:
   ```bash
   # Copiar pubkey content:
   cat secrets/mode_switch_id_ed25519.pub
   # No host 10.10.11.5 — adicionar em ~/.ssh/authorized_keys:
   echo 'command="/opt/mode-switch/mode-switch-executor.sh $SSH_ORIGINAL_COMMAND",no-agent-forwarding,no-pty,no-user-rc,restrict,ssh-ed25519 AAAAC3...YOUR_KEY_HERE' >> ~/.ssh/authorized_keys
   ```
4. Copiar executor script para 10.10.11.5:
   ```bash
   scp scripts/mode-switch-executor.sh paulo@10.10.11.5:/tmp/
   ssh paulo@10.10.11.5 "sudo mkdir -p /opt/mode-switch && sudo mv /tmp/mode-switch-executor.sh /opt/mode-switch/ && sudo chmod 755 /opt/mode-switch/mode-switch-executor.sh"
   ```
5. Rebuild do alert-router com 1Password:
   ```bash
   sudo ./scripts/run-with-1password.sh --profile nextgen up -d --build alert-router
   ```

## Fase 3: Gaming Mode com Confirmacao

### Estado atual: PREPARADO POREM DESABILITADO (flag OFF por padrao)

A Fase 3 esta implementada e testavel, porem o flag `PHASE3_GAMING_ENABLED=false` por padrao.
Isso significa que `/modo gaming` continua retornando mensagem informativa, sem execucao real.

### Comandos adicionais (Fase 3)
| Comando | Descricao |
|---------|-----------|
| `/modo gaming` | Solicita codigo de confirmacao (quando flag ON) |
| `/modo confirmar <codigo>` | Valida codigo e executa gaming |
| `/modo cancelar` | Cancela confirmacao pendente |

### Feature Flag
```bash
# Para habilitar gaming (quando pronto):
PHASE3_GAMING_ENABLED=true

# Configuracoes tuneaveis:
PHASE3_CONFIRM_TTL_SECONDS=120        # TTL do codigo de confirmacao
PHASE3_GAMING_COOLDOWN_SECONDS=300    # Cooldown apos execucao de gaming
```

### Fluxo de seguranca
1. Usuario envia `/modo gaming`
2. Sistema gera codigo de 6 digitos (valido por 120s)
3. Usuario envia `/modo confirmar <codigo>`
4. Sistema valida codigo e executa `mode-switch switch gaming` via SSH
5. Cooldown de 300s impede flip-flop

### Bloqueios (mesmo com flag ON)
- Cooldown ativo: comando rejeitado com tempo restante
- Codigo invalido/expirado: rejeitado, deve gerar novo
- gaming bloqueado no wrapper se flag OFF (camada dupla)
