# FastAPI — Clasificación Email Sistemas

Bot que replica el blueprint de Make.com "0. Clasificación Gmail Sistemas"
para la cuenta **sistemas@tutrastero.com**.

## Blueprint de referencia

La versión activa del blueprint siempre está en:

```
blueprints/clasificacion.blueprint.json
```

**Flujo de actualización:**
1. Exporta el blueprint desde Make.com y cópialo en esa ruta con el mismo nombre.
2. Di "verifica el blueprint" o "actualiza según el blueprint".
3. Claude leerá `blueprints/clasificacion.blueprint.json`, comparará con el código
   actual y aplicará sólo los cambios necesarios.

No hace falta arrastrar el archivo al chat ni indicar la ruta cada vez.

## Estructura del proyecto

```
handlers/clasificacion.py   # Router principal + sub-flujos
services/gmail.py           # Gmail API (History API, sin marcar como leído)
services/airtable.py        # Airtable REST (search, list, update)
services/openai_svc.py      # OpenAI gpt-4.1 (clasificación CGI)
config.py                   # Label IDs, tabla IDs, credenciales
main.py                     # FastAPI app (poller, /webhook, /pubsub, /monitor)
blueprints/                 # Blueprint Make.com de referencia (ver arriba)
```

## Servidor Linux

- **IP:** 192.168.2.197 | **Usuario:** paucosta | **Pass:** TuRasero.com
- **SSH:** `plink.exe` desde Windows (`C:\Program Files\PuTTY\plink.exe`)
- **Servicio:** `fastapi-email-sistemas` (puerto 8005)
- **Directorio:** `/opt/fastapi-email-sistemas`
- **ngrok:** `email-sistemas.ngrok.dev` → puerto 8005

## Despliegue

```bash
git push origin master
# El servidor hace git pull + systemctl restart automáticamente,
# o bien lanzar plink manualmente:
plink -ssh -pw TuRasero.com paucosta@192.168.2.197 "cd /opt/fastapi-email-sistemas && git pull && sudo systemctl restart fastapi-email-sistemas"
```
