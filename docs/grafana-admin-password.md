# Grafana - troca de senha admin

## Metodo recomendado (persistente)
1. Edite `~/monitoring-stack/.env` e ajuste:
   - `GRAFANA_ADMIN_PASSWORD=NOVA_SENHA`
2. Recrie o servico:
```bash
cd ~/monitoring-stack
sudo docker compose --env-file .env up -d grafana
```

## Metodo imediato (sem editar .env)
```bash
sudo docker exec grafana grafana-cli admin reset-admin-password NOVA_SENHA
```

## Validacao
```bash
python3 - <<"PY"
import json, urllib.request, base64
senha = "NOVA_SENHA"
req = urllib.request.Request("http://127.0.0.1:3000/api/user")
req.add_header("Authorization", "Basic " + base64.b64encode(f"admin:{senha}".encode()).decode())
with urllib.request.urlopen(req, timeout=10) as r:
    d = json.load(r)
print(d.get("login"), d.get("isGrafanaAdmin"))
PY
```
