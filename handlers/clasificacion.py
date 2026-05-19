"""
handlers/clasificacion.py — Pipeline principal de clasificación/etiquetado.

Replica exacta del blueprint "0. Clasificación Gmail Sistemas":

  Trigger: emails nuevos en INBOX de sistemas@tutrastero.com
  Router:  evalúa TODAS las rutas de forma independiente (igual que BasicRouter de Make.com)
           y aplica las etiquetas Gmail que correspondan.

Rutas simples (mover de INBOX a etiqueta):
  1. fromEmail contains "antigravity"                           → Antigravity
  2. fromEmail contains "masip."                                → MasIP
  3. lower(fromName) contains "microsoft" (NOT "power bi")      → Microsoft
  4. fromEmail contains "openai." OR subject contains "openai"  → OpenAI
  5. fromEmail contains "radicalsys.com"                        → RADical Systems
  6. lower(fromName) contains "power bi"                        → Reportes Power Bi
  7. subject contains "zapier" OR (fromName=="zapier" AND NOT "error on your") → Zapier
  8. subject contains "airtable" AND NOT "error en flujo"       → AirTable
  9. fromName=="Tu Trastero CGI" AND NOT "Clasificación CGI -"  → CGI - Respuestas
 10. subject contains "nuevo prospecto sin gestionar"           → Bitrix24
 11. subject contains "make"/"error in"/"has been stopped"      → Make
 12. subject contains "Cobro de Moroso -"/"Oportunidad Única -" → Bot Llamada Morosos
 13. subject contains "Proceso de Contratación:"                → Presup. y Contratación

Ruta compleja:
 14. subject contains "Clasificación CGI -" AND fromEmail=="cgi@tutrastero.com"
     → Busca el registro en Airtable Clasificación por asunto-clasif,
       obtiene el tipo (Requerimiento/Informativo/Interno/Malicioso)
       y aplica la sub-etiqueta correspondiente + la etiqueta padre.
"""
import asyncio
import collections
import datetime
import logging
import re

import config
from services import gmail, airtable

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

# Mapa tipo → label ID para la ruta CGI Clasificación
_TIPO_LABEL = {
    "Requerimiento": config.LABEL_CGI_CLASIF_REQ,
    "Informativo":   config.LABEL_CGI_CLASIF_INF,
    "Interno":       config.LABEL_CGI_CLASIF_INT,
    "Malicioso":     config.LABEL_CGI_CLASIF_MAL,
    "Otros":         config.LABEL_CGI_CLASIF_OTR,
}

_TIPO_LABEL_NAME = {v: k for k, v in _TIPO_LABEL.items()}


# ─── Ruta compleja: Clasificación CGI ─────────────────────────────────────────

async def _handle_cgi_clasificacion(subject: str) -> str | None:
    """
    Busca el registro de Airtable por asunto-clasif y devuelve el label ID del tipo.
    Replica los módulos 54→51→46→sub-router del blueprint.
    """
    asunto_clasif = re.sub(r"\s+", "", subject)

    records = await airtable.search_records(
        config.AT_TBL_CLASIFICACION,
        formula=f'{{asunto-clasif}}="{asunto_clasif}"',
        max_records=1,
        fields=["tipo", "clasificación", "asunto-clasif"],
    )
    if not records:
        logger.warning(f"[CGI-Clasif] Sin registro en Airtable | asunto-clasif={asunto_clasif[:60]}")
        return None

    tipo = records[0]["fields"].get("tipo", "")
    label_id = _TIPO_LABEL.get(tipo)
    logger.info(f"[CGI-Clasif] tipo={tipo!r} → label={_TIPO_LABEL_NAME.get(label_id, '?')}")
    return label_id


# ─── Router principal ─────────────────────────────────────────────────────────

async def _route_email(email: dict, message_id: str) -> tuple[list[str], str]:
    """
    Evalúa TODAS las rutas del blueprint y devuelve (labels_a_aplicar, descripcion).
    Comportamiento idéntico al BasicRouter de Make.com: rutas no exclusivas.
    """
    from_email = email.get("fromEmail", "")
    from_name  = email.get("fromName", "")
    subject    = email.get("subject", "")
    fe_lo      = from_email.lower()
    fn_lo      = from_name.lower()
    su_lo      = subject.lower()

    labels:  list[str] = []
    results: list[str] = []

    # Ruta 1 — Antigravity
    if "antigravity" in fe_lo:
        labels.append(config.LABEL_ANTIGRAVITY)
        results.append("Antigravity")

    # Ruta 2 — MasIP
    if "masip." in fe_lo:
        labels.append(config.LABEL_MASIP)
        results.append("MasIP")

    # Ruta 3 — Microsoft (no Power BI)
    if "microsoft" in fn_lo and "power bi" not in fn_lo:
        labels.append(config.LABEL_MICROSOFT)
        results.append("Microsoft")

    # Ruta 4 — OpenAI
    if "openai." in fe_lo or "openai" in su_lo:
        labels.append(config.LABEL_OPENAI)
        results.append("OpenAI")

    # Ruta 5 — RADical Systems
    if "radicalsys.com" in fe_lo:
        labels.append(config.LABEL_RADICAL)
        results.append("RADical Systems")

    # Ruta 6 — Reportes Power Bi
    if "power bi" in fn_lo:
        labels.append(config.LABEL_POWER_BI)
        results.append("Reportes Power Bi")

    # Ruta 7 — Zapier
    if "zapier" in su_lo or (fn_lo == "zapier" and "error on your" not in su_lo):
        labels.append(config.LABEL_ZAPIER)
        results.append("Zapier")

    # Ruta 8 — AirTable
    if "airtable" in su_lo and "error en flujo" not in su_lo:
        labels.append(config.LABEL_AIRTABLE)
        results.append("AirTable")

    # Ruta 14 — Clasificación CGI (compleja, antes de Respuestas CGI)
    if "Clasificación CGI -" in subject and from_email == "cgi@tutrastero.com":
        tipo_label = await _handle_cgi_clasificacion(subject)
        if tipo_label:
            labels.append(config.LABEL_CGI_CLASIF)
            labels.append(tipo_label)
            results.append(f"CGI-Clasif/{_TIPO_LABEL_NAME.get(tipo_label, '?')}")
        else:
            labels.append(config.LABEL_CGI_CLASIF)
            results.append("CGI-Clasif/Sin-tipo")

    # Ruta 9 — CGI Respuestas (Tu Trastero CGI que no sea clasificación)
    if from_name == "Tu Trastero CGI" and "Clasificación CGI -" not in subject:
        labels.append(config.LABEL_CGI_RESPUESTAS)
        results.append("CGI-Respuestas")

    # Ruta 10 — Bitrix24
    if "nuevo prospecto sin gestionar" in su_lo:
        labels.append(config.LABEL_BITRIX24)
        results.append("Bitrix24")

    # Ruta 11 — Make
    if any(x in su_lo for x in ["make", "error in", "has been stopped"]):
        labels.append(config.LABEL_MAKE)
        results.append("Make")

    # Ruta 12 — Bot Llamada Morosos
    if "cobro de moroso -" in subject or "oportunidad única -" in subject:
        labels.append(config.LABEL_BOT_MOROSOS)
        results.append("Bot-Llamada-Morosos")

    # Ruta 13 — Presupuestos y Contratación Online
    if "proceso de contratación:" in subject:
        labels.append(config.LABEL_CONTRATACION)
        results.append("Contratación-Online")

    descripcion = ", ".join(results) if results else "Sin etiqueta"
    return list(dict.fromkeys(labels)), descripcion   # deduplica manteniendo orden


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

    # Evaluar todas las rutas del router
    labels, descripcion = await _route_email(email, message_id)

    # Aplicar etiquetas + mover de INBOX (una sola llamada a la API)
    if labels:
        await gmail.apply_labels(message_id, add_labels=labels, remove_labels=["INBOX"])
        logger.info(f"[0] Etiquetas aplicadas: {descripcion}")
    else:
        logger.info(f"[0] Sin ruta coincidente — marcado como procesado")

    # Marcar siempre como procesado para no reprocesar en el próximo ciclo
    await gmail.mark_processed(message_id)

    summaries.appendleft({
        "time":        datetime.datetime.now().strftime("%d/%m %H:%M:%S"),
        "from_email":  from_email,
        "from_name":   from_name,
        "subject":     subject,
        "etiquetas":   descripcion,
        "n_labels":    len(labels),
        "logs":        list(_flow_logs),
    })


# ─── Poller ───────────────────────────────────────────────────────────────────

async def process_new_emails():
    if _lock.locked():
        return
    async with _lock:
        messages = await gmail.list_unread_emails()
        if not messages:
            return
        logger.info(f"[0] {len(messages)} email(s) encontrado(s)")
        for msg in messages:
            try:
                await _process_email(msg)
            except Exception as exc:
                logger.error(f"[0] Error procesando {msg.get('id')}: {exc}", exc_info=True)
