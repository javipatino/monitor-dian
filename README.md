# Monitor DIAN – Devoluciones

Revisa cada 10 minutos si hay citas de **Videoatención > Devoluciones** (persona natural) en https://agendamiento.dian.gov.co/ y avisa por Telegram.

- **Abierto** → alerta con sonido y captura.
- **Error** → un aviso (no se repite hasta que se arregle).
- **Diario** → mensaje silencioso de "sigue vivo".

Corre en GitHub Actions. Necesita dos secrets en *Settings → Secrets and variables → Actions*: `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`.

Para probar: *Actions → Monitor DIAN → Run workflow*.

Nota: GitHub desactiva los workflows programados de repos públicos tras 60 días sin actividad; si pasa, vuelve a activarlo en la pestaña Actions.
