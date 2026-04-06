# Notifications stack secrets (1Password)

Use 1Password as source of truth and keep Kubernetes manifests without real secret values.

## Required secret items

Vault: `MCP API Keys`

- `notifications/notify_api_key`
- `notifications/evolution_api_key`
- `notifications/evolution_auth_key`

Recommended tags:

- `homelab`
- `notifications`
- `runtime`

## Runtime mapping

K8s Secret `notify-api-secrets`:

- `NOTIFY_API_KEY` <- `notifications/notify_api_key`
- `EVOLUTION_API_KEY` <- `notifications/evolution_api_key`

K8s Secret `evolution-api-secrets`:

- `AUTHENTICATION_API_KEY` <- `notifications/evolution_auth_key`

## Secure workflow

1. Create or rotate values in 1Password.
2. Pull values into local runtime only (never commit them).
3. Apply/update K8s Secrets with `kubectl` from a secure shell.
4. Let ArgoCD manage the non-secret manifests.

Example using id-based references:

```bash
NOTIFY_API_KEY_REF='op://<vault_id>/<item_id_notify_api_key>/password' \
EVOLUTION_API_KEY_REF='op://<vault_id>/<item_id_evolution_instance_key>/password' \
EVOLUTION_AUTH_KEY_REF='op://<vault_id>/<item_id_evolution_global_key>/password' \
./scripts/apply_notify_k8s_secrets.sh
```

## Notes

- Do not keep plaintext in `.env` tracked files.
- Prefer references by `vaultId` and `itemId` in automation scripts.
- Rotate tokens on accidental terminal exposure.
