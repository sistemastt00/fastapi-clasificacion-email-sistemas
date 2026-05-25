"""
handlers/clasificacion.py — Replica exacta del blueprint "0. Clasificación Gmail Sistemas".

Flujo general:
  1. Obtener emails nuevos del INBOX (History API).
  2. Para cada email:
       a. Módulo 10: sleep 3 segundos.
       b. BasicRouter (módulo 2): evalúa TODAS las rutas de forma independiente.
       c. Aplica etiquetas Gmail (moveAnEmail = addLabelIds + removeLabelIds: INBOX).

Rutas del BasicRouter principal (módulo 2) — no exclusivas:
  1.  fromEmail contains "antigravity"                              → Antigravity
  2.  fromEmail contains "masip."                                   → MasIP
  3.  lower(fromName) contains "microsoft" AND NOT "power bi"       → Microsoft
  4.  fromEmail contains "openai." OR lower(subject) contains "openai" → OpenAI
  5.  fromEmail contains "radicalsys.com"                           → RADical Systems
  6.  lower(fromName) contains "power bi"                           → Reportes Power Bi
  7.  lower(subject) contains "zapier" OR (lower(fromName)=="zapier" AND NOT "error on your") → Zapier
  8.  lower(subject) contains "airtable" AND NOT "error en flujo"   → AirTable
  9.  fromEmail == "cgi@tutrastero.com"                             → Sub-router "from CGI" (módulo 55):
        9a. subject contains "Clasificación CGI -"                  → Sub-router "Clasificación CGI" (módulo 39):
              · Módulo 54 filter (subject contains -accion- OR -informacion-):
                  Busca en Airtable → carga Definiciones + Ejemplos → OpenAI gpt-4.1
                  Compara con clasificación Airtable → actualiza evaluacion correcto/incorrecto
                  Sub-router por tipo en asunto: -accion- → Requerimiento, -informacion- → Informativo
              · subject contains "Otros-Otros" → CGI-Clasificación/Otros (sin OpenAI)
        9b. subject contains "Proceso de Contratación:"             → Contratación Online
        9c. NOT "Clasificación CGI -" AND NOT "Proceso de Contratación:" → CGI-Respuestas
  10. lower(subject) contains "nuevo prospecto sin gestionar"        → Bitrix24
  11. lower(fromEmail) contains "make"                              → Make
  12. subject contains "Cobro de Moroso -" OR "Oportunidad Única -" → Bot Llamada Morosos
"""
import asyncio
import collections
import datetime
import logging
import re

import config
from services import gmail, airtable
from services import openai_svc

_lock = asyncio.Lock()
summaries: collections.deque = collections.deque(maxlen=100)

logger = logging.getLogger("email-sistemas")

_flow_logs: list = []


class _FlowCaptureHandler(logging.Handler):
    def emit(self, record):
        _flow_logs.append({
            "time":    datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level":   record.levelname,
            "message": self.format(record),
        })


_capture_handler = _FlowCaptureHandler()
_capture_handler.setFormatter(logging.Formatter("%(message)s"))

_TIPOS = ["- accion -", "- informacion -"]  # módulo 54 filter


# ─── Flujo complejo: Clasificación CGI con OpenAI (módulos 54→51→46→48→50→19→52/53→26/32) ──

async def _handle_cgi_clasificacion(subject: str, body: str) -> tuple[list[str], str]:
    """
    Réplica del sub-flujo de clasificación (módulo 54 activo — tipo en asunto).
    Ejecuta OpenAI + actualiza Airtable evaluacion + aplica sub-etiqueta por tipo en asunto.
    """
    asunto_clasif = re.sub(r"\s+", "", subject)

    # Módulo 51 — Buscar registro en Airtable Clasificación
    records = await airtable.search_records(
        config.AT_TBL_CLASIFICACION,
        formula=f'{{asunto-clasif}}="{asunto_clasif}"',
        max_records=1,
        view="viw8oXfMbeVIfQ8tw",
    )

    tipo_label = _tipo_label_from_subject(subject)
    tipo_name  = _tipo_name_from_subject(subject)

    if not records:
        logger.warning(f"[CGI-Clasif] Sin registro Airtable | asunto-clasif={asunto_clasif[:60]}")
        return [config.LABEL_CGI_CLASIF] + ([tipo_label] if tipo_label else []), f"CGI-Clasif/{tipo_name}(sin-registro)"

    record        = records[0]
    record_id     = record["id"]
    clasificacion = record["fields"].get("clasificación", "")

    # Módulos 46+47 — Definiciones, módulos 48+49 — Ejemplos
    definiciones = await airtable.list_all_records(config.AT_TBL_DEFINICIONES)
    ejemplos     = await airtable.list_all_records(config.AT_TBL_EJEMPLOS_CLASIF)

    # Módulo 50 — OpenAI gpt-4.1
    try:
        categoria = await openai_svc.classify_email(
            subject=subject,
            body=body,
            definiciones_records=definiciones,
            ejemplos_records=ejemplos,
        )
        logger.info(f"[CGI-Clasif] OpenAI→{categoria!r} | AT→{clasificacion!r}")
    except Exception as exc:
        logger.error(f"[CGI-Clasif] Error OpenAI: {exc}")
        categoria = None

    # Módulo 19 — Router evaluación (correcto / incorrecto)
    evaluacion = "correcto" if (categoria and clasificacion == categoria) else "incorrecto"
    try:
        await airtable.update_record(
            config.AT_TBL_CLASIFICACION,
            record_id,
            {"evaluacion": evaluacion},
        )
        logger.info(f"[CGI-Clasif] evaluacion={evaluacion} guardado")
    except Exception as exc:
        logger.error(f"[CGI-Clasif] Error actualizando evaluacion: {exc}")

    # Módulos 26/32 — Sub-router tipo en ASUNTO del email
    labels = [config.LABEL_CGI_CLASIF]
    if tipo_label:
        labels.append(tipo_label)

    return labels, f"CGI-Clasif/{tipo_name}({evaluacion})"


def _tipo_label_from_subject(subject: str) -> str | None:
    if "- accion -" in subject:
        return config.LABEL_CGI_CLASIF_REQ
    if "- informacion -" in subject:
        return config.LABEL_CGI_CLASIF_INF
    return None


def _tipo_name_from_subject(subject: str) -> str:
    if "- accion -" in subject:
        return "Requerimiento"
    if "- informacion -" in subject:
        return "Informativo"
    return "?"


# ─── Sub-router "from CGI" (módulo 55) ────────────────────────────────────────

async def _handle_from_cgi(subject: str, body: str) -> tuple[list[str], str]:
    """
    Replica el sub-router "from CGI" (módulo 55):
    agrupa los 3 sub-flujos de emails de cgi@tutrastero.com.
    """
    labels:  list[str] = []
    results: list[str] = []

    # Sub-router "Clasificación CGI" (módulo 39)
    if "Clasificación CGI -" in subject:
        logger.info(f"    ✅ CGI-39: Clasificación CGI")

        # Módulo 54 (SetVariables + filtro): sólo activa el flujo OpenAI si hay tipo en asunto
        if any(t in subject for t in _TIPOS):
            logger.info(f"    ✅ CGI-54: tipo en asunto → OpenAI")
            cgi_labels, cgi_desc = await _handle_cgi_clasificacion(subject, body)
            labels.extend(cgi_labels)
            results.append(cgi_desc)
        else:
            logger.info(f"    ⬜ CGI-54: sin tipo reconocido → sin OpenAI")

        # Módulo 40: "Otros-Otros" en asunto → CGI-Clasificación/Otros (sin OpenAI)
        if "Otros-Otros" in subject:
            labels.append(config.LABEL_CGI_CLASIF)
            labels.append(config.LABEL_CGI_CLASIF_OTR)
            results.append("CGI-Clasif/Otros-Otros")
            logger.info(f"    ✅ CGI-40: Otros-Otros")
    else:
        logger.info(f"    ⬜ CGI-39: no es Clasificación CGI")

    # Módulo 44 — Contratos Online: subject contains "Proceso de Contratación:"
    if "Proceso de Contratación:" in subject:
        labels.append(config.LABEL_CONTRATACION)
        results.append("Contratación-Online")
        logger.info(f"    ✅ CGI-44: Contratación Online")
    else:
        logger.info(f"    ⬜ CGI-44: no es Contratación")

    # Módulo 11 — Respuestas CGI: NOT Clasificación CGI AND NOT Contratación
    if "Clasificación CGI -" not in subject and "Proceso de Contratación:" not in subject:
        labels.append(config.LABEL_CGI_RESPUESTAS)
        results.append("CGI-Respuestas")
        logger.info(f"    ✅ CGI-11: Respuestas CGI")

    return list(dict.fromkeys(labels)), ", ".join(results)


# ─── Router principal — BasicRouter módulo 2 ──────────────────────────────────

async def _route_email(email: dict) -> tuple[list[str], str]:
    """
    Evalúa TODAS las rutas del blueprint de forma independiente (no exclusivas).
    """
    from_email = email.get("fromEmail", "")
    from_name  = email.get("fromName", "")
    subject    = email.get("subject", "")
    body       = email.get("fullTextBody", "")
    fe_lo      = from_email.lower()
    fn_lo      = from_name.lower()
    su_lo      = subject.lower()

    labels:  list[str] = []
    results: list[str] = []

    def _hit(ruta: str):
        results.append(ruta)
        logger.info(f"  ✅ {ruta}")

    def _miss(ruta: str):
        logger.info(f"  ⬜ {ruta}")

    # Ruta 1 — Antigravity
    if "antigravity" in fe_lo:
        labels.append(config.LABEL_ANTIGRAVITY); _hit("Antigravity")
    else:
        _miss("Antigravity")

    # Ruta 2 — MasIP
    if "masip." in fe_lo:
        labels.append(config.LABEL_MASIP); _hit("MasIP")
    else:
        _miss("MasIP")

    # Ruta 3 — Microsoft (excluye Power BI)
    if "microsoft" in fn_lo and "power bi" not in fn_lo:
        labels.append(config.LABEL_MICROSOFT); _hit("Microsoft")
    else:
        _miss("Microsoft")

    # Ruta 4 — OpenAI
    if "openai." in fe_lo or "openai" in su_lo:
        labels.append(config.LABEL_OPENAI); _hit("OpenAI")
    else:
        _miss("OpenAI")

    # Ruta 5 — RADical Systems
    if "radicalsys.com" in fe_lo:
        labels.append(config.LABEL_RADICAL); _hit("RADical Systems")
    else:
        _miss("RADical Systems")

    # Ruta 6 — Reportes Power Bi
    if "power bi" in fn_lo:
        labels.append(config.LABEL_POWER_BI); _hit("Reportes Power Bi")
    else:
        _miss("Reportes Power Bi")

    # Ruta 7 — Zapier
    if "zapier" in su_lo or (fn_lo == "zapier" and "error on your" not in su_lo):
        labels.append(config.LABEL_ZAPIER); _hit("Zapier")
    else:
        _miss("Zapier")

    # Ruta 8 — AirTable
    if "airtable" in su_lo and "error en flujo" not in su_lo:
        labels.append(config.LABEL_AIRTABLE); _hit("AirTable")
    else:
        _miss("AirTable")

    # Ruta 9 — Sub-router "from CGI" (módulo 55)
    if from_email == "cgi@tutrastero.com":
        logger.info(f"  ✅ CGI → sub-router 🔀")
        cgi_labels, cgi_desc = await _handle_from_cgi(subject, body)
        labels.extend(cgi_labels)
        if cgi_desc:
            results.append(cgi_desc)
    else:
        _miss("CGI")

    # Ruta 10 — Bitrix24
    if "nuevo prospecto sin gestionar" in su_lo:
        labels.append(config.LABEL_BITRIX24); _hit("Bitrix24")
    else:
        _miss("Bitrix24")

    # Ruta 11 — Make (fromEmail contains "make", módulo 42)
    if "make" in fe_lo and "error in" not in fe_lo and "has been stopped" not in fe_lo:
        labels.append(config.LABEL_MAKE); _hit("Make")
    else:
        _miss("Make")

    # Ruta 12 — Bot Llamada Morosos
    if "cobro de moroso -" in subject or "oportunidad única -" in subject:
        labels.append(config.LABEL_BOT_MOROSOS); _hit("Bot-Llamada-Morosos")
    else:
        _miss("Bot-Llamada-Morosos")

    descripcion = ", ".join(results) if results else "Sin etiqueta"
    return list(dict.fromkeys(labels)), descripcion


# ─── Procesamiento individual ─────────────────────────────────────────────────

async def _process_email(msg_stub: dict):
    _flow_logs.clear()
    logger.addHandler(_capture_handler)
    try:
        await _process_email_inner(msg_stub)
    finally:
        logger.removeHandler(_capture_handler)


async def _process_email_inner(msg_stub: dict):
    message_id = msg_stub["id"]
    email      = await gmail.get_email(message_id)
    from_email = email.get("fromEmail", "")
    from_name  = email.get("fromName", "")
    subject    = email.get("subject", "")

    logger.info(f"[0] from={from_email} | subject={subject!r}")

    # Módulo 10 — sleep 3 segundos
    await asyncio.sleep(3)

    labels, descripcion = await _route_email(email)

    if labels:
        await gmail.apply_labels(message_id, add_labels=labels, remove_labels=["INBOX"])
        logger.info(f"[0] Etiquetas: {descripcion}")
    else:
        await gmail.apply_labels(message_id, add_labels=[config.LABEL_SISTEMAS_PROCESADO], remove_labels=[])
        logger.info("[0] Sin ruta — etiquetado Sistemas-Procesado (en INBOX)")

    summaries.appendleft({
        "time":       datetime.datetime.now().strftime("%d/%m %H:%M:%S"),
        "from_email": from_email,
        "from_name":  from_name,
        "subject":    subject,
        "etiquetas":  descripcion,
        "n_labels":   len(labels),
        "logs":       list(_flow_logs),
    })


# ─── Poller ───────────────────────────────────────────────────────────────────

async def process_new_emails():
    if _lock.locked():
        return
    async with _lock:
        messages = await gmail.list_new_emails()
        if not messages:
            return
        logger.info(f"[0] {len(messages)} email(s) nuevo(s)")
        for msg in messages:
            try:
                await _process_email(msg)
            except Exception as exc:
                logger.error(f"[0] Error procesando {msg.get('id')}: {exc}", exc_info=True)
