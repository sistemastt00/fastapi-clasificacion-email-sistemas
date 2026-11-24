# FastAPI — Clasificación Email Sistemas

Bot que clasifica el correo de **sistemas@tutrastero.com** con etiquetas de Gmail.

Nació como réplica del blueprint de Make.com "0. Clasificación Gmail Sistemas",
pero **Make ya no se usa**: el código es la fuente de verdad. El blueprint en
`blueprints/` es sólo histórico; no hay que sincronizarlo ni "verificarlo".

## Cómo clasifica

`handlers/clasificacion.py` → `_route_email` evalúa **todas** las rutas de forma
independiente (no exclusivas) y devuelve `(labels, descripcion, acciones)`.

| Ruta | Detecta | Etiqueta |
|---|---|---|
| 1-8 | Antigravity, MasIP, Microsoft, OpenAI, RADical, Power BI, Zapier, AirTable | una por proveedor |
| 9 | `cgi@tutrastero.com` → sub-router CGI (Clasificación con OpenAI + Airtable, Contratación, Respuestas) | CGI-* |
| 10-13 | Bitrix24, Make (mails de Make), Bot Llamada Morosos, YouSign | una por ruta |
| 14 | **AETYM** (`@aetym.com` en From/To/Cc o "aetym" en asunto/cuerpo) | AETYM |
| 15 | **AccessOne** ("Primer acceso a Tu Trastero…" o "AccessOne" en asunto; no si es AETYM) | AccessOne |
| 16 | **A3 · Contabilidad** (asunto `[A3 · Contabilidad]`) | A3 Contabilidad |

### Acciones (leído / archivado)

- **Todo correo etiquetado sale de Recibidos** (queda sólo en su etiqueta), sin excepciones.
- Si la ruta marca `mark_read` (automáticos), se quita también UNREAD. Los que requieren
  atención quedan **archivados pero sin leer** en su etiqueta.
- `keep_unread=True` **anula** el leído de cualquier ruta ("ante la duda, sin leer").
- Sin ninguna ruta: se etiqueta `Sistemas-Procesado` y también se archiva.
- Nunca se modifican las etiquetas CGI-Clasificación ni `Sistemas-Procesado` desde las reglas 14-16.

### Reglas 14-16 (resumen)

- **AETYM** — sin leer: `[Solicitud]` / "Nueva solicitud desde aetym.com" sin `[Prueba]`,
  reenvíos (`Fw/Fwd/RV`) y correos de personas. Leído y archivado: `[Prueba]`, rebotes
  (mailer-daemon), respuestas automáticas, "Hemos recibido tu solicitud", avisos de GitHub/Google.
- **AccessOne** — leído, salvo que el cuerpo mencione error/alerta/fallo/denegado (queda sin leer).
- **A3** — remitente `notificacion@tutrastero.com`:
  - "sin incidencias" o `A3 ERP: TEST` → leído.
  - `A3 ERP: PROD` con "(N nueva/s)", N>0 → sin leer.
  - PROD con incidencias pero ninguna nueva → leído.
  - `Re/Fw/Fwd/RV` de un aviso → etiqueta y siempre sin leer.
- Los avisos A3 llegan **sólo en HTML**: `_text_body` convierte HTML a texto antes de buscar marcas.

### Variables de entorno de etiquetas

Los IDs de etiqueta de Gmail van en el `.env` del servidor (ver `config.py`). Para las reglas
nuevas: `LABEL_AETYM`, `LABEL_ACCESSONE`, `LABEL_A3`. Si falta una, el bot lo avisa en el log
y no aplica esa etiqueta (las acciones sí se aplican).

## Estructura del proyecto

```
handlers/clasificacion.py   # Router principal (rutas 1-16) + sub-flujos + reglas AETYM/AccessOne/A3
services/gmail.py           # Gmail API (History API, sin marcar como leído)
services/airtable.py        # Airtable REST (search, list, update)
services/openai_svc.py      # OpenAI gpt-4.1 (clasificación CGI)
config.py                   # Label IDs, tabla IDs, credenciales
main.py                     # FastAPI app (poller, /webhook, /pubsub, /monitor)
blueprints/                 # Blueprint Make.com (histórico, ya no se usa)
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
