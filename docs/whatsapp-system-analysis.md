# Sistema de Mensagens WhatsApp — Analise e Diagnostico

> **Repositorio:** `/home/paulo/workspace/monitoring-stack/`
> **Namespace K8s:** `ai-platform`
> **Data da analise:** 29 de marco de 2026
> **Versao do documento:** 1.0

---

## Indice

1. [Visao Geral](#1-visao-geral)
2. [Status Atual dos Componentes](#2-status-atual-dos-componentes)
3. [Arquitetura](#3-arquitetura)
4. [Componentes em Detalhe](#4-componentes-em-detalhe)
5. [Fluxo de Dados](#5-fluxo-de-dados)
6. [Topologia Redis Streams](#6-topologia-redis-streams)
7. [Secrets e Credenciais](#7-secrets-e-credenciais)
8. [Diagnostico — Por que o MCP nao esta Rodando](#8-diagnostico--por-que-o-mcp-nao-esta-rodando)
9. [Riscos e Caveats](#9-riscos-e-caveats)
10. [Plano de Remediacao](#10-plano-de-remediacao)
11. [Referencia de Arquivos](#11-referencia-de-arquivos)

---

## 1. Visao Geral

O sistema de mensagens WhatsApp do homelab envia notificacoes via WhatsApp por dois caminhos:

- **Alertas de infraestrutura:** Prometheus dispara alertas, Alertmanager encaminha via webhook para um roteador que classifica por severidade e envia via WhatsApp.
- **Agentes de IA:** Agentes autonomos (via protocolo MCP) enviam mensagens programaticas.

Ambos os caminhos convergem para uma API central (`notify-api`) que enfileira mensagens em Redis Streams. Um worker consome a fila e despacha para a Evolution API, que efetivamente envia pelo WhatsApp.

**Stack tecnologica:**

| Camada         | Tecnologia                       |
|----------------|----------------------------------|
| Fila           | Redis 7.4 (Streams, AOF, NFS)   |
| API            | FastAPI (Python 3.12)            |
| Worker         | Python puro (XREADGROUP)         |
| MCP Server     | FastMCP (mcp==1.13.1)           |
| WhatsApp       | Evolution API v2.3.7             |
| Orquestracao   | MicroK8s + ArgoCD (GitOps)       |
| Alert routing  | Docker Compose (TrueNAS)         |

---

## 2. Status Atual dos Componentes

| Componente       | Codigo | Dockerfile | Manifests K8s | Imagem Buildada | Secrets K8s | Deployed | Rodando |
|------------------|:------:|:----------:|:-------------:|:---------------:|:-----------:|:--------:|:-------:|
| notify-api       |   OK   |     OK     |      OK       |       NAO       |     NAO     |   NAO    |   NAO   |
| notify-worker    |   OK   |     OK     |      OK       |       NAO       |     NAO     |   NAO    |   NAO   |
| notify-mcp       |   OK   |     OK     |      OK       |       NAO       |     NAO     |   NAO    |   NAO   |
| alert-router     |   OK   |     OK     |  Compose OK   |       NAO       |    N/A *    |   NAO    |   NAO   |
| redis-notify     |  N/A   |   Publica  |      OK       |       N/A       |     N/A     |   NAO    |   NAO   |
| evolution-api    |  N/A   |   Publica  |      OK       |       N/A       |     NAO     |   NAO    |   NAO   |
| ArgoCD Apps      |  N/A   |    N/A     |   PLACEHOLDER |       N/A       |     N/A     |   NAO    |   NAO   |

> \* alert-router roda no TrueNAS via Docker Compose; secrets vem de variaveis de ambiente no `.env`.

**Resumo:** Todo o codigo e manifests estao prontos, mas nenhum componente foi efetivamente deployado. A stack inteira esta parada.

---

## 3. Arquitetura

### 3.1 Diagrama de Componentes

```
                     CAMINHO DE ALERTAS
  ┌────────────┐    ┌──────────────┐    ┌──────────────┐
  │ Prometheus │───►│ Alertmanager │───►│ alert-router │
  │ (TrueNAS)  │    │ (TrueNAS     │    │ (TrueNAS     │
  │  :9090     │    │  :9093)      │    │  :8081)      │
  └────────────┘    └──────────────┘    └──────┬───────┘
                                               │ POST /v1/messages/policy
                                               ▼
                     CAMINHO DE AGENTES    ┌──────────────┐
  ┌────────────┐    ┌──────────────┐      │  notify-api  │
  │ AI Agent   │───►│  notify-mcp  │─────►│  (K8s LB)    │
  │ (OpenCode) │    │  (K8s        │      │  :8080       │
  │            │    │   :8000)     │      └──────┬───────┘
  └────────────┘    └──────────────┘             │ XADD
                                                 ▼
                                        ┌──────────────┐
                                        │ redis-notify │
                                        │ (K8s SS)     │
                                        │  :6379       │
                                        │  DB 0:streams│
                                        │  DB 6:evo    │
                                        └──────┬───────┘
                                               │ XREADGROUP
                                               ▼
                                        ┌──────────────┐
                                        │notify-worker │
                                        │ (K8s x2)     │
                                        └──────┬───────┘
                                               │ POST /message/sendText/{instance}
                                               ▼
                                        ┌──────────────┐
                                        │Evolution API │
                                        │ (K8s)        │    ┌───────────┐
                                        │  :8080       │───►│ WhatsApp  │
                                        └──────────────┘    └───────────┘
```

### 3.2 Topologia de Rede

| Componente      | Tipo de Service | Endereco Interno (K8s)                                       | Porta |
|-----------------|-----------------|--------------------------------------------------------------|-------|
| notify-api      | LoadBalancer    | `notify-api.ai-platform.svc.cluster.local`                   | 8080  |
| notify-mcp      | ClusterIP       | `notify-mcp.ai-platform.svc.cluster.local`                   | 8000  |
| notify-worker   | Sem Service     | N/A (consumer, nao recebe trafego)                           | —     |
| redis-notify    | ClusterIP       | `redis-notify.ai-platform.svc.cluster.local`                 | 6379  |
| evolution-api   | ClusterIP       | `evolution-api.ai-platform.svc.cluster.local`                | 8080  |
| alert-router    | Docker bridge   | `alert-router` (rede `monitoring`, TrueNAS)                  | 8081  |
| alertmanager    | Docker bridge   | `alertmanager` (rede `monitoring`, TrueNAS)                  | 9093  |

---

## 4. Componentes em Detalhe

### 4.1 notify-api

**Funcao:** API HTTP que recebe requisicoes de envio de mensagem, gera UUID, persiste metadata em Redis e enfileira no stream.

| Propriedade      | Valor                                                       |
|------------------|-------------------------------------------------------------|
| Codigo-fonte     | `nextgen/notify_api/app.py` (141 linhas)                    |
| Framework        | FastAPI 0.116.1 + Uvicorn 0.35.0                           |
| Imagem Docker    | `localhost:32000/notify-api:v0.1`                           |
| Replicas         | 2                                                           |
| Service          | LoadBalancer :8080                                          |
| Health check     | `GET /healthz` (readiness 8s, liveness 20s)                 |
| Recursos         | Request: 100m CPU, 128Mi RAM / Limit: 512Mi RAM            |

**Endpoints:**

| Metodo | Path                   | Descricao                                      | Auth    |
|--------|------------------------|-------------------------------------------------|---------|
| POST   | `/v1/messages`         | Enfileira mensagem para lista de destinatarios  | API Key |
| POST   | `/v1/messages/policy`  | Enfileira com policy (general/critical)         | API Key |
| GET    | `/v1/messages/{id}`    | Consulta status de mensagem por UUID            | API Key |
| GET    | `/healthz`             | Health check                                    | Nenhum  |

**Schema do request (`POST /v1/messages`):**

```json
{
  "text": "string (1-4096 chars, obrigatorio)",
  "recipients": ["5527999999999"],
  "source": "agent | alertmanager | unknown",
  "priority": "normal | critical",
  "metadata": {}
}
```

**Autenticacao:** Header `X-API-Key` validado contra a env `NOTIFY_API_KEY`.

> **CAVEAT:** Se `NOTIFY_API_KEY` estiver vazio ou nao definido, a funcao `_require_api_key()` retorna sem erro — auth e completamente bypassed. Ver `app.py` linhas 20-25.

### 4.2 notify-worker

**Funcao:** Consumer que le mensagens do Redis Stream via XREADGROUP e despacha para a Evolution API.

| Propriedade      | Valor                                                       |
|------------------|-------------------------------------------------------------|
| Codigo-fonte     | `nextgen/notify_worker/worker.py` (187 linhas)              |
| Runtime          | Python 3.12 puro (sem framework web)                       |
| Imagem Docker    | `localhost:32000/notify-worker:v0.1`                        |
| Replicas         | 2                                                           |
| Service          | Nenhum (consumer, nao recebe trafego)                       |
| Health check     | Nenhum (sem probes configurados)                            |
| Recursos         | Request: 100m CPU, 128Mi RAM / Limit: 512Mi RAM            |

**Logica de processamento (`worker.py` linhas 124-145):**

1. Le batch de ate 10 mensagens via `XREADGROUP` (block 5s)
2. Para cada mensagem, itera sobre `recipients` e chama `send_to_evolution()`
3. Se todos envios OK → atualiza status para `sent`
4. Se algum falhar → chama `enqueue_retry_or_dlq()`
5. Sempre faz `XACK` no finally (linha 183)

**Retry e DLQ (`worker.py` linhas 99-121):**

- Max retries: `MAX_RETRIES=5` (env, default 5)
- Backoff: `min(2^attempt, 60)` segundos — valores: 2s, 4s, 8s, 16s, 32s
- Apos esgotar tentativas: XADD no stream `notify:dlq` com reason
- Status atualizado em cada etapa: `retrying` → `failed`

> **CAVEAT:** O backoff usa `time.sleep()` (linha 115), que bloqueia a thread de processamento. Durante o sleep, nenhuma outra mensagem e processada por aquele worker.

**Trimming de streams (`worker.py` linhas 63-76):**

- Executado a cada 1 hora (quando nao ha mensagens)
- `XTRIM MINID` com retencao de 30 dias
- Aplica em: `notify:messages`, `notify:status`, `notify:dlq`

### 4.3 notify-mcp

**Funcao:** Servidor MCP (Model Context Protocol) que expoe tools para agentes de IA enviarem mensagens WhatsApp.

| Propriedade      | Valor                                                       |
|------------------|-------------------------------------------------------------|
| Codigo-fonte     | `nextgen/notify_mcp/server.py` (71 linhas)                  |
| Framework        | FastMCP (mcp==1.13.1)                                       |
| Imagem Docker    | `localhost:32000/notify-mcp:v0.2`                           |
| Replicas         | 1                                                           |
| Service          | ClusterIP :8000                                             |
| Health check     | Nenhum (sem probes configurados)                            |
| Transport        | `streamable-http` (env `MCP_TRANSPORT`)                     |
| Recursos         | Request: 50m CPU, 64Mi RAM / Limit: 256Mi RAM              |

**Tools MCP expostas:**

| Tool              | Parametros                                    | Descricao                          |
|-------------------|-----------------------------------------------|------------------------------------|
| `send_message`    | `number`, `text`, `source?`, `priority?`      | Envia para um destinatario         |
| `send_bulk`       | `numbers[]`, `text`, `source?`, `priority?`   | Envia para multiplos destinatarios |
| `delivery_status` | `message_id`                                  | Consulta status de entrega         |

**Implementacao:** Proxy HTTP puro para `notify-api` — nao toca Redis diretamente. O URL base e configurado via env `NOTIFY_API_URL` (default: `http://notify-api.ai-platform.svc.cluster.local:8080`).

### 4.4 alert-router

**Funcao:** Webhook receiver que recebe alertas do Alertmanager, classifica por severidade e envia via `notify-api`.

| Propriedade      | Valor                                                       |
|------------------|-------------------------------------------------------------|
| Codigo-fonte     | `nextgen/alert_router/app.py` (106 linhas)                  |
| Framework        | FastAPI 0.116.1 + Uvicorn 0.35.0                           |
| Deploy           | Docker Compose (TrueNAS), profile `nextgen`                 |
| Porta            | 8081                                                        |
| Health check     | `GET /healthz`                                              |

**Roteamento por severidade (`app.py` linhas 27-44):**

| Severidade   | Destinatarios                                |
|--------------|----------------------------------------------|
| `critical`   | `WHATSAPP_TO_GENERAL` + `WHATSAPP_TO_CRITICAL` |
| Outras       | `WHATSAPP_TO_GENERAL` apenas                  |

**Formato da mensagem gerada (`app.py` linhas 47-55):**

```
homelab-monitoring | Alertmanager | 2026-03-29 12:00:00Z

[FIRING] HighCPUUsage (critical)
Instancia: 10.10.11.5:9100
Inicio: 2026-03-29T11:55:00Z
Resumo: CPU acima de 90% por 5 minutos
Detalhe: O uso de CPU no node principal esta acima do limiar

... e mais N alertas
```

- Trunca em 5 alertas por mensagem; indica quantidade restante.
- Chama `POST /v1/messages/policy` no notify-api com campo `policy` correspondente.

**Configuracao Alertmanager** (`alertmanager/alertmanager.yml`):

```yaml
route:
  receiver: alert-router
  group_by: ["alertname", "severity", "instance"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 2h

receivers:
  - name: alert-router
    webhook_configs:
      - url: http://alert-router:8081/alertmanager
        send_resolved: true
```

### 4.5 redis-notify

**Funcao:** Broker de mensagens (Redis Streams) e cache da Evolution API.

| Propriedade      | Valor                                                       |
|------------------|-------------------------------------------------------------|
| Imagem           | `redis:7.4-alpine`                                          |
| Deploy           | StatefulSet K8s, 1 replica                                  |
| Service          | ClusterIP :6379                                             |
| Persistencia     | PVC 20Gi, StorageClass `nfs-production`, AOF `everysec`     |
| Recursos         | Request: 100m CPU, 256Mi RAM / Limit: 1Gi RAM              |

**Uso de databases:**

| DB  | Finalidade                        |
|-----|-----------------------------------|
| 0   | Streams de notificacao (padrao)   |
| 6   | Cache da Evolution API            |

### 4.6 evolution-api

**Funcao:** Backend WhatsApp que gerencia sessoes e envia/recebe mensagens.

| Propriedade      | Valor                                                       |
|------------------|-------------------------------------------------------------|
| Imagem           | `evoapicloud/evolution-api:v2.3.7`                          |
| Deploy           | Deployment K8s, 1 replica                                   |
| Service          | ClusterIP :8080                                             |
| Persistencia     | PVC `evolution-instances` 20Gi NFS                          |
| Database         | Postgres (`platform_ai`, schema `public`)                   |
| Cache            | Redis DB 6 (`redis://redis-notify:6379/6`)                  |
| Instance name    | `homelab`                                                   |
| Recursos         | Request: 200m CPU, 512Mi RAM / Limit: 1Gi RAM              |

> **CAVEAT:** A connection string do Postgres esta hardcoded no manifest com senha `change-me-please`. Ver `evolution-deployment.yaml` linha 34.

---

## 5. Fluxo de Dados

### 5.1 Caminho de Alertas (Prometheus → WhatsApp)

```
Prometheus          Alertmanager         alert-router         notify-api
(TrueNAS :9090)    (TrueNAS :9093)     (TrueNAS :8081)     (K8s LB :8080)
     │                    │                    │                    │
     │  firing/resolved   │                    │                    │
     ├───────────────────►│                    │                    │
     │                    │  POST /alertmanager│                    │
     │                    ├───────────────────►│                    │
     │                    │                    │ _group_by_severity │
     │                    │                    │ _build_message     │
     │                    │                    │ POST /v1/messages/ │
     │                    │                    │        policy      │
     │                    │                    ├───────────────────►│
     │                    │                    │                    │ HSET + XADD
     │                    │                    │                    ├─────► Redis
```

```
Redis               notify-worker        Evolution API        WhatsApp
(K8s :6379)         (K8s x2)             (K8s :8080)
     │                    │                    │                    │
     │  XREADGROUP        │                    │                    │
     ├───────────────────►│                    │                    │
     │                    │ POST /message/     │                    │
     │                    │   sendText/homelab │                    │
     │                    ├───────────────────►│                    │
     │                    │                    │   envia via WA     │
     │                    │                    ├───────────────────►│
     │  XACK + status     │                    │                    │
     │◄───────────────────┤                    │                    │
```

### 5.2 Caminho de Agentes (MCP → WhatsApp)

```
AI Agent     notify-mcp          notify-api           Redis
(OpenCode)   (K8s :8000)         (K8s LB :8080)       (K8s :6379)
     │              │                    │                    │
     │ tool_call:   │                    │                    │
     │ send_message │                    │                    │
     ├─────────────►│                    │                    │
     │              │ POST /v1/messages  │                    │
     │              ├───────────────────►│                    │
     │              │                    │ HSET + XADD        │
     │              │                    ├───────────────────►│
     │              │  {message_id, ok}  │                    │
     │◄─────────────┤◄───────────────────┤                    │
```

A partir do Redis, o fluxo segue identico ao caminho de alertas (worker → Evolution → WhatsApp).

### 5.3 Ciclo de Vida de uma Mensagem

```
  queued ──────► sent
    │
    │ (falha)
    ▼
  retrying ───► retrying ───► ... ───► failed (DLQ)
  (attempt 1)   (attempt 2)         (attempt 5)
```

| Status     | Onde gravado                   | Condicao                                |
|------------|--------------------------------|-----------------------------------------|
| `queued`   | `notify:message:{id}`, stream  | Imediatamente apos POST no notify-api   |
| `sent`     | `notify:message:{id}`, stream  | Todos os recipients recebidos com 2xx   |
| `retrying` | `notify:message:{id}`, stream  | Algum recipient falhou, attempt < 5     |
| `failed`   | `notify:message:{id}`, DLQ     | Esgotou MAX_RETRIES (5)                 |

---

## 6. Topologia Redis Streams

### 6.1 Streams e Estruturas

```
Redis DB 0
├── notify:messages          Stream — fila principal
│   └── Consumer Group: notify-workers
│       ├── worker-{PID-1}   Consumer (replica 1)
│       └── worker-{PID-2}   Consumer (replica 2)
│
├── notify:status            Stream — log de eventos de status
│
├── notify:dlq               Stream — dead letter queue
│
└── notify:message:{uuid}    Hash — metadata individual (TTL 30d)
    ├── id
    ├── status
    ├── source
    ├── priority
    ├── text
    ├── recipients (JSON)
    ├── metadata (JSON)
    ├── attempt
    ├── created_at
    ├── updated_at
    └── detail (se houver erro)
```

### 6.2 Fluxo no Stream

```
notify-api                        notify:messages                     notify-worker
   │                                    │                                   │
   │  XADD {id, text, recipients...}    │                                   │
   ├───────────────────────────────────►│                                   │
   │                                    │  XREADGROUP notify-workers        │
   │                                    │  worker-{PID} > count=10 block=5s │
   │                                    ├──────────────────────────────────►│
   │                                    │                                   │
   │                                    │  XACK (apos processamento)        │
   │                                    │◄──────────────────────────────────┤
```

### 6.3 ConfigMap de Referencia

Definido em `k8s/notifications/services/notify-configmap.yaml`:

```yaml
REDIS_URL: redis://redis-notify:6379/0
REDIS_STREAM_MESSAGES: notify:messages
REDIS_STREAM_STATUS: notify:status
REDIS_STREAM_DLQ: notify:dlq
STATUS_RETENTION_DAYS: "30"
MAX_RETRIES: "5"
EVOLUTION_BASE_URL: http://evolution-api.ai-platform.svc.cluster.local:8080
EVOLUTION_INSTANCE: homelab
```

### 6.4 Comandos Uteis para Debug

```bash
# Listar streams
kubectl exec -n ai-platform redis-notify-0 -- redis-cli KEYS "notify:*"

# Ver tamanho da fila principal
kubectl exec -n ai-platform redis-notify-0 -- redis-cli XLEN notify:messages

# Ver consumer group info
kubectl exec -n ai-platform redis-notify-0 -- redis-cli XINFO GROUPS notify:messages

# Ver consumers ativos
kubectl exec -n ai-platform redis-notify-0 -- redis-cli XINFO CONSUMERS notify:messages notify-workers

# Ver ultimas 5 mensagens na DLQ
kubectl exec -n ai-platform redis-notify-0 -- redis-cli XREVRANGE notify:dlq + - COUNT 5

# Ver status de uma mensagem especifica
kubectl exec -n ai-platform redis-notify-0 -- redis-cli HGETALL notify:message:<UUID>

# Ver ultimos 10 eventos de status
kubectl exec -n ai-platform redis-notify-0 -- redis-cli XREVRANGE notify:status + - COUNT 10
```

---

## 7. Secrets e Credenciais

### 7.1 Secrets no 1Password

| Secret (1Password)                       | Vault           | Usado por                | Campo K8s             |
|------------------------------------------|-----------------|--------------------------|-----------------------|
| `notifications/notify_api_key`           | MCP API Keys    | notify-api, notify-mcp, alert-router | `NOTIFY_API_KEY`     |
| `notifications/evolution_api_key`        | MCP API Keys    | notify-worker            | `EVOLUTION_API_KEY`   |
| `notifications/evolution_auth_key`       | MCP API Keys    | evolution-api            | `AUTHENTICATION_API_KEY` |

### 7.2 Secrets Kubernetes

| Secret K8s               | Keys                                   | Usado por                      |
|--------------------------|----------------------------------------|--------------------------------|
| `notify-api-secrets`     | `NOTIFY_API_KEY`, `EVOLUTION_API_KEY`  | notify-api, notify-worker      |
| `evolution-api-secrets`  | `AUTHENTICATION_API_KEY`               | evolution-api                  |

### 7.3 Script de Provisionamento

Arquivo: `scripts/apply_notify_k8s_secrets.sh`

```bash
# Uso:
NOTIFY_API_KEY_REF="op://<vault_id>/<item_id>/password" \
EVOLUTION_API_KEY_REF="op://<vault_id>/<item_id>/password" \
EVOLUTION_AUTH_KEY_REF="op://<vault_id>/<item_id>/password" \
  bash scripts/apply_notify_k8s_secrets.sh
```

O script:
1. Le cada secret do 1Password via `op read`
2. Cria/atualiza os Secrets K8s via `kubectl create secret --dry-run=client | kubectl apply`
3. Requer sessao ativa do `op` CLI e `kubectl` apontando para o MicroK8s

### 7.4 Variaveis de Ambiente do alert-router (Docker Compose)

Definidas no `.env` do TrueNAS (nao versionado):

| Variavel                | Descricao                                  |
|-------------------------|--------------------------------------------|
| `ALERT_SOURCE`          | Prefixo da mensagem (ex: `homelab-monitoring`) |
| `WHATSAPP_TO_GENERAL`   | Numero WhatsApp para alertas gerais        |
| `WHATSAPP_TO_CRITICAL`  | Numero WhatsApp adicional para criticos    |
| `NOTIFY_API_URL`        | URL do notify-api (IP LoadBalancer K8s)    |
| `NOTIFY_API_KEY`        | Mesma key do 1Password                     |

---

## 8. Diagnostico — Por que o MCP nao esta Rodando

A stack de notificacoes WhatsApp esta 100% parada. Tres causas raiz independentes impedem o funcionamento:

### CAUSA 1 — MCP Server nao registrado no OpenCode

O arquivo de configuracao do OpenCode (`~/.config/opencode/opencode.json`) contem apenas o MCP do 1Password. Nao existe nenhuma entrada para `notify-mcp`.

**Impacto:** O agente de IA nao sabe que o servidor MCP existe. Nenhum tool call `send_message`, `send_bulk` ou `delivery_status` e possivel.

**Correcao:** Adicionar entrada em `opencode.json` — ver [Plano de Remediacao, passo 6](#passo-6--registrar-notify-mcp-no-opencodejson).

### CAUSA 2 — ArgoCD Applications com repoURL placeholder

Os dois Application manifests em `argocd/` tem `repoURL: ssh://git@your-gitops-repo` — um placeholder que nunca foi substituido pelo endereco real do repositorio Git.

**Evidencia:**

```yaml
# argocd/notifications-infra-application.yaml (linha 9)
repoURL: ssh://git@your-gitops-repo

# argocd/notifications-services-application.yaml (linha 9)
repoURL: ssh://git@your-gitops-repo
```

**Impacto:** Sem ArgoCD Applications validos, nenhum recurso K8s do notification stack e criado. redis-notify, evolution-api, notify-api, notify-worker e notify-mcp — todos inexistentes no cluster.

### CAUSA 3 — Imagens Docker nao foram buildadas

Os Deployments referenciam imagens no registry local do MicroK8s:

| Componente    | Imagem esperada                       |
|---------------|---------------------------------------|
| notify-api    | `localhost:32000/notify-api:v0.1`     |
| notify-worker | `localhost:32000/notify-worker:v0.1`  |
| notify-mcp    | `localhost:32000/notify-mcp:v0.2`     |

Nao existe pipeline de CI/CD. O build e push devem ser feitos manualmente no no K8s (10.10.11.5). Sem as imagens no registry, os Pods ficariam em `ImagePullBackOff` mesmo que os manifests fossem aplicados.

### Causas Secundarias

| Causa                                  | Impacto                                                |
|----------------------------------------|--------------------------------------------------------|
| Secrets K8s nao provisionados          | Pods falhariam ao montar `secretKeyRef`                |
| Script de secrets nunca executado       | `notify-api-secrets` e `evolution-api-secrets` nao existem |
| alert-router precisa de `--profile nextgen` | Sem o profile, `docker compose up` nao starta o container |
| Evolution API com Postgres hardcoded   | Senha `change-me-please` no manifest (linha 34)        |
| Instance WhatsApp nao pareada          | Mesmo com tudo rodando, precisa escanear QR code       |

---

## 9. Riscos e Caveats

### 9.1 Seguranca

| Risco | Severidade | Arquivo | Descricao |
|-------|:----------:|---------|-----------|
| Auth bypass | **ALTA** | `notify_api/app.py` L20-23 | Se `NOTIFY_API_KEY` vazio, qualquer request e aceito sem autenticacao |
| Postgres hardcoded | **ALTA** | `evolution-deployment.yaml` L34 | Connection string com `change-me-please` em plaintext no manifest |
| ClusterIP inacessivel | MEDIA | `notify-mcp-service.yaml` | notify-mcp e ClusterIP — agentes fora do cluster nao conseguem conectar sem port-forward |
| Secrets placeholder | MEDIA | `secret-placeholders.example.yaml` | Arquivo exemplo com `change-me-with-1password` — risco se aplicado acidentalmente |

### 9.2 Confiabilidade

| Risco | Severidade | Arquivo | Descricao |
|-------|:----------:|---------|-----------|
| Blocking sleep | **ALTA** | `notify_worker/worker.py` L115 | `time.sleep(backoff)` bloqueia o loop do worker durante retry. Com backoff de 60s, um worker fica 1 minuto parado |
| Sem health probes | MEDIA | `notify-worker-deployment.yaml` | Worker sem liveness/readiness probes — K8s nao detecta travamento |
| Sem health probes | MEDIA | `notify-mcp-deployment.yaml` | MCP server sem liveness/readiness probes |
| Single-threaded worker | MEDIA | `notify_worker/worker.py` | Cada replica processa sequencialmente; blocking retry amplifica o problema |

### 9.3 Operacional

| Risco | Severidade | Descricao |
|-------|:----------:|-----------|
| Sem CI/CD | MEDIA | Build de imagens e 100% manual; risco de drift entre codigo e imagem rodando |
| Sem metricas | MEDIA | Nenhum componente expoe metricas Prometheus; monitoramento cego |
| Sem alertas sobre a propria stack | BAIXA | Se o notify-worker morrer, ninguem e notificado |

---

## 10. Plano de Remediacao

Checklist ordenado para colocar a stack em funcionamento. Cada passo depende dos anteriores.

### Passo 1 — Corrigir repoURL nos ArgoCD Applications

Substituir `ssh://git@your-gitops-repo` pelo endereco real do repositorio GitOps.

```bash
# Descobrir o repoURL correto (ja configurado para outros apps no ArgoCD):
kubectl get applications -n argocd -o jsonpath='{.items[0].spec.source.repoURL}'

# Editar os dois arquivos:
#   argocd/notifications-infra-application.yaml   (linha 9)
#   argocd/notifications-services-application.yaml (linha 9)
# Trocar: ssh://git@your-gitops-repo
# Por:    ssh://git@10.10.11.5/home/paulo/gitops-bare.git  (ou o valor encontrado)
```

- [ ] `notifications-infra-application.yaml` — repoURL corrigido
- [ ] `notifications-services-application.yaml` — repoURL corrigido

### Passo 2 — Buildar e pushar imagens Docker

Executar no no K8s (10.10.11.5), onde o registry `localhost:32000` esta acessivel:

```bash
cd /home/paulo/workspace/monitoring-stack

# notify-api
docker build -t localhost:32000/notify-api:v0.1 ./nextgen/notify_api/
docker push localhost:32000/notify-api:v0.1

# notify-worker
docker build -t localhost:32000/notify-worker:v0.1 ./nextgen/notify_worker/
docker push localhost:32000/notify-worker:v0.1

# notify-mcp
docker build -t localhost:32000/notify-mcp:v0.2 ./nextgen/notify_mcp/
docker push localhost:32000/notify-mcp:v0.2
```

- [ ] `notify-api:v0.1` buildada e pushada
- [ ] `notify-worker:v0.1` buildada e pushada
- [ ] `notify-mcp:v0.2` buildada e pushada

### Passo 3 — Provisionar Secrets K8s via 1Password

```bash
# Obter os IDs do 1Password (vault MCP API Keys):
# notifications/notify_api_key
# notifications/evolution_api_key
# notifications/evolution_auth_key

NOTIFY_API_KEY_REF="op://<vault_id>/<item_id_notify>/password" \
EVOLUTION_API_KEY_REF="op://<vault_id>/<item_id_evolution>/password" \
EVOLUTION_AUTH_KEY_REF="op://<vault_id>/<item_id_auth>/password" \
  bash scripts/apply_notify_k8s_secrets.sh
```

Verificar:

```bash
kubectl get secrets -n ai-platform | grep -E "notify-api-secrets|evolution-api-secrets"
```

- [ ] `notify-api-secrets` criado em `ai-platform`
- [ ] `evolution-api-secrets` criado em `ai-platform`

### Passo 4 — Deployer infraestrutura (redis-notify + evolution-api)

Opcao A — via ArgoCD (recomendado, apos passo 1):

```bash
kubectl apply -f argocd/notifications-infra-application.yaml
```

Opcao B — via kubectl direto (para teste rapido):

```bash
kubectl apply -k k8s/notifications/infrastructure/
```

Verificar:

```bash
kubectl get pods -n ai-platform -l 'app in (redis-notify,evolution-api)'
# Esperado: redis-notify-0 Running, evolution-api-xxx Running

kubectl exec -n ai-platform redis-notify-0 -- redis-cli PING
# Esperado: PONG
```

- [ ] redis-notify rodando e respondendo PING
- [ ] evolution-api rodando e respondendo em `GET /`

### Passo 5 — Deployer services (notify-api + notify-worker + notify-mcp)

Opcao A — via ArgoCD:

```bash
kubectl apply -f argocd/notifications-services-application.yaml
```

Opcao B — via kubectl direto:

```bash
kubectl apply -k k8s/notifications/services/
```

Verificar:

```bash
kubectl get pods -n ai-platform -l 'app in (notify-api,notify-worker,notify-mcp)'
# Esperado: 2x notify-api Running, 2x notify-worker Running, 1x notify-mcp Running

# Testar health do notify-api:
NOTIFY_API_IP=$(kubectl get svc notify-api -n ai-platform -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl http://${NOTIFY_API_IP}:8080/healthz
# Esperado: {"status":"ok"}
```

- [ ] notify-api (2 replicas) rodando
- [ ] notify-worker (2 replicas) rodando
- [ ] notify-mcp (1 replica) rodando
- [ ] `GET /healthz` retornando 200

### Passo 6 — Registrar notify-mcp no opencode.json

Adicionar o servidor MCP no arquivo de configuracao do OpenCode.

**Opcao A — Remote HTTP (recomendado se acessivel via LoadBalancer ou port-forward):**

Primeiro, expor o notify-mcp (atualmente ClusterIP). Trocar para LoadBalancer no service ou fazer port-forward:

```bash
# Port-forward temporario:
kubectl port-forward -n ai-platform svc/notify-mcp 8000:8000 &
```

Adicionar em `~/.config/opencode/opencode.json`:

```json
{
  "mcpServers": {
    "notify-mcp": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

**Opcao B — Stdio local (rodar o MCP localmente):**

```json
{
  "mcpServers": {
    "notify-mcp": {
      "type": "stdio",
      "command": "python",
      "args": ["/home/paulo/workspace/monitoring-stack/nextgen/notify_mcp/server.py"],
      "env": {
        "NOTIFY_API_URL": "http://<NOTIFY_API_LB_IP>:8080",
        "NOTIFY_API_KEY": "op://<vault_id>/<item_id>/password",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

- [ ] notify-mcp registrado no opencode.json
- [ ] Verificar que tools `send_message`, `send_bulk`, `delivery_status` aparecem no agente

### Passo 7 — Startar alert-router no TrueNAS

```bash
cd /home/paulo/workspace/monitoring-stack

# Garantir que as variaveis estao no .env:
# ALERT_SOURCE, WHATSAPP_TO_GENERAL, WHATSAPP_TO_CRITICAL,
# NOTIFY_API_URL (IP LoadBalancer do notify-api), NOTIFY_API_KEY

docker compose --profile nextgen up -d alert-router
```

Verificar:

```bash
docker compose --profile nextgen ps alert-router
# Esperado: running

curl http://localhost:8081/healthz
# Esperado: {"status":"ok"}
```

- [ ] alert-router rodando no TrueNAS
- [ ] Health check retornando OK
- [ ] Alertmanager configurado para apontar para `http://alert-router:8081/alertmanager`

### Passo 8 — Parear instancia WhatsApp na Evolution API

```bash
# Obter IP/porta da Evolution API:
EVOLUTION_IP=$(kubectl get svc evolution-api -n ai-platform -o jsonpath='{.spec.clusterIP}')

# Criar instancia (se nao existir):
curl -X POST "http://${EVOLUTION_IP}:8080/instance/create" \
  -H "apikey: <AUTHENTICATION_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"instanceName": "homelab", "integration": "WHATSAPP-BAILEYS"}'

# Obter QR code:
curl "http://${EVOLUTION_IP}:8080/instance/connect/homelab" \
  -H "apikey: <AUTHENTICATION_API_KEY>"
```

Escanear o QR code com o WhatsApp do numero desejado.

- [ ] Instancia `homelab` criada
- [ ] QR code escaneado e sessao ativa
- [ ] Teste de envio E2E bem-sucedido

### Passo 9 — Teste End-to-End

```bash
# Via curl (teste direto no notify-api):
NOTIFY_API_IP=$(kubectl get svc notify-api -n ai-platform -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
API_KEY="<NOTIFY_API_KEY>"

curl -X POST "http://${NOTIFY_API_IP}:8080/v1/messages" \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Teste E2E do sistema de notificacoes WhatsApp",
    "recipients": ["5527999999999"],
    "source": "manual-test",
    "priority": "normal"
  }'
# Esperado: {"message_id":"<UUID>","status":"queued"}

# Verificar status apos ~5s:
curl "http://${NOTIFY_API_IP}:8080/v1/messages/<UUID>" \
  -H "X-API-Key: ${API_KEY}"
# Esperado: status "sent"
```

- [ ] Mensagem enviada via curl e recebida no WhatsApp
- [ ] Status transicionou para `sent`
- [ ] Nenhuma mensagem na DLQ

---

## 11. Referencia de Arquivos

### Codigo-fonte

| Componente    | Arquivo                                | Linhas | Linguagem       |
|---------------|----------------------------------------|--------|-----------------|
| notify-api    | `nextgen/notify_api/app.py`            | 141    | Python (FastAPI) |
| notify-worker | `nextgen/notify_worker/worker.py`      | 187    | Python           |
| notify-mcp    | `nextgen/notify_mcp/server.py`         | 71     | Python (FastMCP) |
| alert-router  | `nextgen/alert_router/app.py`          | 106    | Python (FastAPI) |

### Dockerfiles

| Componente    | Arquivo                                | Base image        | CMD                                             |
|---------------|----------------------------------------|-------------------|-------------------------------------------------|
| notify-api    | `nextgen/notify_api/Dockerfile`        | python:3.12-slim  | `uvicorn app:app --host 0.0.0.0 --port 8080`   |
| notify-worker | `nextgen/notify_worker/Dockerfile`     | python:3.12-slim  | `python worker.py`                              |
| notify-mcp    | `nextgen/notify_mcp/Dockerfile`        | python:3.12-slim  | `python server.py`                              |
| alert-router  | `nextgen/alert_router/Dockerfile`      | python:3.12-slim  | `uvicorn app:app --host 0.0.0.0 --port 8081`   |

### Manifests Kubernetes

| Recurso                  | Arquivo                                                      |
|--------------------------|--------------------------------------------------------------|
| Namespace                | `k8s/notifications/infrastructure/namespace.yaml`            |
| Redis StatefulSet        | `k8s/notifications/infrastructure/redis-statefulset.yaml`    |
| Redis Service            | `k8s/notifications/infrastructure/redis-service.yaml`        |
| Evolution Deployment     | `k8s/notifications/infrastructure/evolution-deployment.yaml` |
| Evolution Service        | `k8s/notifications/infrastructure/evolution-service.yaml`    |
| Evolution PVC            | `k8s/notifications/infrastructure/evolution-pvc.yaml`        |
| Kustomization (infra)    | `k8s/notifications/infrastructure/kustomization.yaml`        |
| ConfigMap                | `k8s/notifications/services/notify-configmap.yaml`           |
| notify-api Deployment    | `k8s/notifications/services/notify-api-deployment.yaml`      |
| notify-api Service       | `k8s/notifications/services/notify-api-service.yaml`         |
| notify-worker Deployment | `k8s/notifications/services/notify-worker-deployment.yaml`   |
| notify-mcp Deployment    | `k8s/notifications/services/notify-mcp-deployment.yaml`      |
| notify-mcp Service       | `k8s/notifications/services/notify-mcp-service.yaml`         |
| Kustomization (services) | `k8s/notifications/services/kustomization.yaml`              |

### ArgoCD Applications

| Application              | Arquivo                                              | Status    |
|--------------------------|------------------------------------------------------|-----------|
| notifications-infra      | `argocd/notifications-infra-application.yaml`        | PLACEHOLDER — repoURL invalido |
| notifications-services   | `argocd/notifications-services-application.yaml`     | PLACEHOLDER — repoURL invalido |

### Alertmanager

| Config                   | Arquivo                                              | Status    |
|--------------------------|------------------------------------------------------|-----------|
| Ativo (nextgen)          | `alertmanager/alertmanager.yml`                      | Aponta para alert-router |
| Nextgen (backup)         | `alertmanager/alertmanager.nextgen.yml`               | Identico ao ativo |
| Legacy                   | `alertmanager/alertmanager.legacy-relay.yml`          | Aponta para whatsapp-relay (depreciado) |

### Dependencias (requirements.txt)

| Componente    | Dependencias                                          |
|---------------|-------------------------------------------------------|
| notify-api    | fastapi==0.116.1, uvicorn==0.35.0, redis==6.4.0, pydantic==2.11.7 |
| notify-worker | redis==6.4.0, requests==2.32.5                       |
| notify-mcp    | mcp==1.13.1, requests==2.32.5                        |
| alert-router  | fastapi==0.116.1, uvicorn==0.35.0, requests==2.32.5  |

---

> **Nota:** O diretorio `whatsapp-relay/` contem o sistema legado que esta sendo substituido por esta stack. Nao deve ser usado para novos deploys.
