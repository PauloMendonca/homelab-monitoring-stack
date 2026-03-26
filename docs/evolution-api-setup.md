# Evolution API - passo a passo (TrueNAS)

## 1) Preparar stack da Evolution (isolada)
1. Crie um diretorio separado: `~/evolution-stack`.
2. Crie volume persistente (dataset recomendado): `/mnt/pool_fast/db/evolution`.
3. Suba os containers da Evolution conforme documentacao oficial (imagem e variaveis de ambiente atuais).

## 2) Configurar instancia
1. Abra o painel/API da Evolution.
2. Crie a instancia `homelab`.
3. Gere uma API key dedicada para alertas.
4. Pareie o WhatsApp via QR code com um numero dedicado.

## 3) Teste de envio direto
1. Execute um POST para `/message/sendText/homelab`.
2. Exemplo de payload:
```json
{
  "number": "55119XXXXXXXX",
  "text": "Teste de envio Evolution API"
}
```
3. Valide recebimento no WhatsApp.

## 4) Integrar com relay da monitoracao
1. Edite `~/monitoring-stack/.env`:
   - `EVOLUTION_BASE_URL=http://10.10.11.2:8088`
   - `EVOLUTION_INSTANCE=homelab`
   - `EVOLUTION_API_KEY=<SUA_CHAVE>`
   - `WHATSAPP_TO=55119XXXXXXXX`
2. Aplicar:
```bash
cd ~/monitoring-stack
sudo docker compose --env-file .env up -d whatsapp-relay alertmanager
```

## 5) Teste fim-a-fim (Alertmanager para WhatsApp)
1. Envie um alerta manual:
```bash
curl -X POST http://127.0.0.1:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d "[{\"labels\":{\"alertname\":\"PipelineTest\",\"severity\":\"warning\",\"instance\":\"manual\"},\"annotations\":{\"summary\":\"Teste pipeline\",\"description\":\"Alertmanager para WhatsApp\"}}]"
```
2. Valide no WhatsApp.
3. Verifique logs do relay:
```bash
sudo docker logs --tail 100 whatsapp-relay
```

## 6) Hardening minimo
- API key dedicada so para alertas.
- Restrinja acesso ao endpoint da Evolution na LAN.
- Use numero dedicado para alertas de infraestrutura.
- Configure canal de fallback (Telegram/email) para falha da Evolution.
