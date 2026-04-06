# Rollout 1Password MCP no TrueNAS

Este runbook define como executar o MCP Server do 1Password no TrueNAS para substituir segredos em `.env` por itens no vault dedicado.

## Escopo e plataforma

- Host: `10.10.11.2` (TrueNAS)
- Tipo de componente: automacao/secrets helper (nao workload de plataforma K8s)
- Risco esperado: baixo, sem mudancas de rede ou firewall

## Pre-requisitos

1. Vault dedicado criado no 1Password: `MCP API Keys`.
2. Service Account criada com acesso minimo ao vault dedicado.
3. Token da Service Account disponivel apenas no TrueNAS.
4. Node.js 20+ no TrueNAS.

## Estrutura recomendada no TrueNAS

```text
/mnt/pool_fast/db/secrets/
  1password-mcp/
    token            # conteudo: OP_SERVICE_ACCOUNT_TOKEN
    .env             # opcional: variaveis adicionais de runtime
```

Permissoes recomendadas:

- pasta: `0700`
- arquivos: `0600`
- owner: usuario tecnico dedicado (ex: `svc_1password_mcp`)

## Wrapper de execucao (stdio)

Copiar `scripts/1password-mcp-stdio.sh` para o TrueNAS (ou usar direto do repo) e garantir permissao de execucao.

Comando esperado:

```bash
/opt/homelab/bin/1password-mcp-stdio.sh
```

Esse wrapper:

- carrega token de arquivo local protegido
- nao escreve token em logs
- inicia `@takescake/1password-mcp@2.4.1` via `npx`

## Configuracao do cliente MCP (Codex/CLI)

Para nao salvar token no cliente, use conexao SSH para iniciar o MCP remotamente no TrueNAS:

```toml
[mcp_servers."1password"]
command = "ssh"
args = ["truenas", "/opt/homelab/bin/1password-mcp-stdio.sh"]
```

Observacao: o alias `truenas` deve existir em `~/.ssh/config` da maquina cliente.

## Ondas de migracao sugeridas

1. Monitoring stack no TrueNAS (`monitoring-stack/.env`).
2. Demais stacks Compose do homelab.
3. Segredos de workloads K8s, mantendo GitOps sem plaintext.

## Validacao objetiva

1. MCP responde com `vault_list`.
2. Criacao de item de teste com `password_create`.
3. Leitura por `password_read` usando referencia `op://...`.
4. Rotacao com `password_update`.
5. Servicos continuam saudaveis apos trocar `.env`.

## Rollback

1. Manter backup temporario de `.env` fora do Git durante a migracao.
2. Em falha, restaurar `.env` anterior e reiniciar stack.
3. Corrigir mapeamento/permissão e repetir a onda.

## Pendencias para concluir

- Token da Service Account ainda pendente (valor nao compartilhado no repo).
- Vault definido: `MCP API Keys`.
- Usuario tecnico definido: `svc_1password_mcp`.
- Caminho do token definido: `/mnt/pool_fast/db/secrets/1password-mcp/token`.
