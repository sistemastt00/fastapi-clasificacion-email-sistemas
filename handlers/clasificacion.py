"""
handlers/clasificacion.py — Replica exacta del blueprint "0. Clasificación Gmail Sistemas".

Flujo general:
  1. Obtener emails nuevos del INBOX (History API — sólo los realmente nuevos).
  2. Para cada email:
       a. Módulo 10: sleep 3 segundos.
       b. BasicRouter: evalúa TODAS las rutas de forma independiente.
       c. Aplica las etiquetas Gmail correspondientes (moveAnEmail = add + remove INBOX).

Rutas del BasicRouter (no exclusivas — se evalúan todas):
  1.  fromEmail contains "antigravity"                              → Antigravity
  2.  fromEmail contains "masip."                                   → MasIP
  3.  lower(fromName) contains "microsoft" AND NOT "power bi"       → Microsoft
  4.  fromEmail contains "openai." OR subject contains "openai"     → OpenAI
  5.  fromEmail contains "radicalsys.com"                           → RADical Systems
  6.  lower(fromName) contains "power bi"                           → Reportes Power Bi
  7.  subject contains "zapier" OR (fromName=="zapier" AND NOT "error on your") → Zapier
  8.  subject contains "airtable" AND NOT "error en flujo"          → AirTable
  9.  subject contains "Clasificación CGI -" AND fromEmail=="cgi@tutrastero.com"
        → Flujo complejo (módulos 46-53):
            · Busca registro en Airtable Clasificación por asunto-clasif
            · Carga Definiciones (mod 46) y Ejemplos (mod 48) de Airtable
            · OpenAI gpt-4.1 re-clasifica el email (mod 50) → categoria
            · Compara con clasificación de Airtable (mod 19):
                correcto  → Actualiza evaluacion="correcto"  (mod 52)
                incorrecto→ Actualiza evaluacion="incorrecto" (mod 53)
            · Aplica sub-etiqueta CGI según tipo EN EL ASUNTO (mods 26 / 32)
  10. fromName=="Tu Trastero CGI" AND NOT "Clasificación CGI -" in subject → CGI-Respuestas
  11. subject contains "nuevo prospecto sin gestionar"               → Bitrix24
  12. subject contains "make" OR "error in" OR "has been stopped"   → Make
  13. "Cobro de Moroso -" in subject OR "Oportunidad Única -" in subject → Bot Llamada Morosos
  14. "Proceso de Contratación:" in subject                          → Presupuestos y Contratación Online
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


# ─── Ruta compleja: Clasificación CGI (módulos 46–53 del blueprint) ───────────

async def _handle_cgi_clasificacion(
    subject: str,
    body: str,
) -> tuple[list[str], str]:
    """
    Replica exacta del sub-flujo "Clasificación CGI -" del blueprint.
    Devuelve (label_ids, descripcion).
    """
    asunto_clasif = re.sub(r"\s+", "", subject)

    # Módulo 51 — Buscar registro en Airtable Clasificación
    records = await airtable.search_records(
        config.AT_TBL_CLASIFICACION,
        formula=f'{{asunto-clasif}}="{asunto_clasif}"',
        max_records=1,
        view="viw8oXfMbeVIfQ8tw",
    )
    if not records:
        logger.warning(f"[CGI-Clasif] Sin registro en Airtable | asunto-clasif={asunto_clasif[:60]}")
        # Sin registro no podemos evaluar — aplicamos sólo la etiqueta padre
        label_id = _tipo_label_from_subject(subject)
        return [config.LABEL_CGI_CLASIF] + ([label_id] if label_id else []), "CGI-Clasif/sin-registro"

    record       = records[0]
    record_id    = record["id"]
    clasificacion = record["fields"].get("clasificación", "")

    # Módulos 46 + 47 — Cargar Definiciones y agregarlas
    definiciones = await airtable.list_all_records(config.AT_TBL_DEFINICIONES)

    # Módulos 48 + 49 — Cargar Ejemplos de Clasificación y agregarlos
    ejemplos = await airtable.list_all_records(config.AT_TBL_EJEMPLOS_CLASIF)

    # Módulo 50 — OpenAI re-clasifica el email
    try:
        categoria = await openai_svc.classify_email(
            subject=subject,
            body=body,
            definiciones_records=definiciones,
            ejemplos_records=ejemplos,
        )
        logger.info(f"[CGI-Clasif] OpenAI → categoria={categoria!r} | Airtable → clasificación={clasificacion!r}")
    except Exception as exc:
        logger.error(f"[CGI-Clasif] Error OpenAI: {exc}")
        categoria = None

    # Módulo 19 — BasicRouter "Evaluación de clasificación"
    if categoria and clasificacion and clasificacion == categoria:
        # Ruta correcto → Módulo 52
        try:
            await airtable.update_record(
                config.AT_TBL_CLASIFICACION,
                record_id,
                {"evaluacion": "correcto"},
            )
            logger.info("[CGI-Clasif] evaluacion=correcto guardado en Airtable")
        except Exception as exc:
            logger.error(f"[CGI-Clasif] Error actualizando Airtable (correcto): {exc}")
        evaluacion = "correcto"
    else:
        # Ruta incorrecto → Módulo 53
        try:
            await airtable.update_record(
                config.AT_TBL_CLASIFICACION,
                record_id,
                {"evaluacion": "incorrecto"},
            )
            logger.info("[CGI-Clasif] evaluacion=incorrecto guardado en Airtable")
        except Exception as exc:
            logger.error(f"[CGI-Clasif] Error actualizando Airtable (incorrecto): {exc}")
        evaluacion = "incorrecto"

    # Módulos 26 / 32 — Sub-router por tipo en el ASUNTO del email
    tipo_label = _tipo_label_from_subject(subject)
    tipo_name  = _tipo_name_from_subject(subject)

    labels = [config.LABEL_CGI_CLASIF]
    if tipo_label:
        labels.append(tipo_label)

    descripcion = f"CGI-Clasif/{tipo_name}({evaluacion})"
    return labels, descripcion


def _tipo_label_from_subject(subject: str) -> str | None:
    """Determina la sub-etiqueta de CGI Clasificación según el tipo en el asunto."""
    if "Requerimiento" in subject:
        return config.LABEL_CGI_CLASIF_REQ
    if "Informativo" in subject:
        return config.LABEL_CGI_CLASIF_INF
    if "Interno" in subject:
        return config.LABEL_CGI_CLASIF_INT
    if "Malicioso" in subject:
        return config.LABEL_CGI_CLASIF_MAL
    return config.LABEL_CGI_CLASIF_OTR


def _tipo_name_from_subject(subject: str) -> str:
    if "Requerimiento" in subject:
        return "Requerimiento"
    if "Informativo" in subject:
        return "Informativo"
    if "Interno" in subject:
        return "Interno"
    if "Malicioso" in subject:
        return "Malicioso"
    return "Otros"


# ─── Router principal — replica BasicRouter del blueprint ─────────────────────

async def _route_email(email: dict) -> tuple[list[str], str]:
    """
    Evalúa TODAS las rutas del blueprint de forma independiente (no exclusivas).
    Devuelve (labels_a_aplicar, descripcion).
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

    # Ruta 1 — Antigravity
    if "antigravity" in fe_lo:
        labels.append(config.LABEL_ANTIGRAVITY)
        results.append("Antigravity")

    # Ruta 2 — MasIP
    if "masip." in fe_lo:
        labels.append(config.LABEL_MASIP)
        results.append("MasIP")

    # Ruta 3 — Microsoft (excluye Power BI)
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

    # Ruta 9 — Clasificación CGI (flujo complejo con OpenAI)
    if "Clasificación CGI -" in subject and from_email == "cgi@tutrastero.com":
        cgi_labels, cgi_desc = await _handle_cgi_clasificacion(subject, body)
        labels.extend(cgi_labels)
        results.append(cgi_desc)

    # Ruta 10 — CGI Respuestas (Tu Trastero CGI que NO sea clasificación)
    if from_name == "Tu Trastero CGI" and "Clasificación CGI -" not in subject:
        labels.append(config.LABEL_CGI_RESPUESTAS)
        results.append("CGI-Respuestas")

    # Ruta 11 — Bitrix24
    if "nuevo prospecto sin gestionar" in su_lo:
        labels.append(config.LABEL_BITRIX24)
        results.append("Bitrix24")

    # Ruta 12 — Make
    if any(x in su_lo for x in ["make", "error in", "has been stopped"]):
        labels.append(config.LABEL_MAKE)
        results.append("Make")

    # Ruta 13 — Bot Llamada Morosos
    if "cobro de moroso -" in subject or "oportunidad única -" in subject:
        labels.append(config.LABEL_BOT_MOROSOS)
        results.append("Bot-Llamada-Morosos")

    # Ruta 14 — Presupuestos y Contratación Online
    if "proceso de contratación:" in subject:
        labels.append(config.LABEL_CONTRATACION)
        results.append("Contratación-Online")

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

    # Módulo 10 — sleep 3 segundos (réplica exacta del blueprint)
    await asyncio.sleep(3)

    # BasicRouter — evalúa todas las rutas
    labels, descripcion = await _route_email(email)

    # moveAnEmail → addLabelIds + removeLabelIds=["INBOX"]
    if labels:
        await gmail.apply_labels(message_id, add_labels=labels, remove_labels=["INBOX"])
        logger.info(f"[0] Etiquetas aplicadas: {descripcion}")
    else:
        logger.info("[0] Sin ruta coincidente — email sin etiquetar")

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
