# Notifications manifests

- `infrastructure/`: Redis Streams, Evolution API, namespace and base secrets.
- `services/`: notify-api, notify-worker, notify-mcp.

All persistent workloads request `storageClassName: nfs-production`.

`secret-placeholders.example.yaml` is documentation-only and is not applied by kustomize.
