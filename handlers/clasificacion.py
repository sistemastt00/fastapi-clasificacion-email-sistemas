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
  12. fromEmail == "contabilidad@tutrastero.com" OR subject contains "Cobro de Moroso" OR "Oportunidad Única -" → Bot Llamada Morosos
  13. fromEmail == "firmas.online@tutrastero.com"                    → YouSign

Rutas añadidas fuera del blueprint (reglas propias, ver _classify_aetym / _classify_accessone):
  14. AETYM     (prioridad sobre AccessOne)  → etiqueta + automático: leído /
                                                requiere atención: se deja sin leer
  15. AccessOne (sólo si no es AETYM)        → etiqueta + leído,
                                                salvo que el cuerpo mencione error/alerta/acceso fallido (sin leer)
  16. A3 · Contabilidad (notificacion@tutrastero.com)  → etiqueta "A3 Contabilidad"; ver _classify_a3
Regla general de acciones: todo correo etiquetado sale de Recibidos (queda sólo en su etiqueta).
Los automáticos además se marcan leídos; los que requieren atención quedan SIN LEER; ante la duda, sin leer.
Sin ninguna ruta: etiqueta Sistemas-Procesado y también sale de Recibidos.
"""
import asyncio
import collections
import datetime
import html
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
    tipo_label = _tipo_label_from_subject(subject)
    tipo_name  = _tipo_name_from_subject(subject)
    fallback   = [config.LABEL_CGI_CLASIF] + ([tipo_label] if tipo_label else [])

    # Módulo 51 — Buscar registro en Airtable Clasificación
    try:
        records = await airtable.search_records(
            config.AT_TBL_CLASIFICACION,
            formula=f'{{asunto-clasif}}="{asunto_clasif}"',
            max_records=1,
            view="viw8oXfMbeVIfQ8tw",
        )
    except Exception as exc:
        logger.error(f"[CGI-Clasif] Error Airtable search: {exc}")
        return fallback, f"CGI-Clasif/{tipo_name}(error-at-search)"

    if not records:
        logger.warning(f"[CGI-Clasif] Sin registro Airtable | asunto-clasif={asunto_clasif[:60]}")
        return fallback, f"CGI-Clasif/{tipo_name}(sin-registro)"

    record        = records[0]
    record_id     = record["id"]
    clasificacion = record["fields"].get("clasificación", "")

    # Módulos 46+47 — Definiciones, módulos 48+49 — Ejemplos
    try:
        definiciones = await airtable.list_all_records(config.AT_TBL_DEFINICIONES)
        ejemplos     = await airtable.list_all_records(config.AT_TBL_EJEMPLOS_CLASIF)
    except Exception as exc:
        logger.error(f"[CGI-Clasif] Error Airtable list definiciones/ejemplos: {exc}")
        return fallback, f"CGI-Clasif/{tipo_name}(error-at-list)"

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


# ─── Reglas AETYM / AccessOne (fuera del blueprint) ───────────────────────────

def _hdr(email: dict, name: str) -> str:
    """Cabecera sin distinguir mayúsculas."""
    name = name.lower()
    for k, v in (email.get("headers") or {}).items():
        if k.lower() == name:
            return v or ""
    return ""


def _html_to_text(html_src: str) -> str:
    """HTML → texto plano en minúsculas (quita script/style/etiquetas y colapsa espacios)."""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html_src)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip().lower()


def _text_body(email: dict) -> str:
    plain = email.get("fullTextBody")
    if plain:
        return plain.lower()
    if email.get("htmlBody"):
        return _html_to_text(email["htmlBody"])
    return (email.get("snippet") or "").lower()


def _is_aetym(email: dict) -> bool:
    """Remitente/destinatario @aetym.com, o 'aetym' en asunto/cuerpo (incluye rebotes con la dirección)."""
    addrs = " ".join(_hdr(email, h) for h in ("From", "To", "Cc", "Reply-To", "Delivered-To")).lower()
    if "@aetym.com" in addrs:
        return True
    return "aetym" in email.get("subject", "").lower() or "aetym" in _text_body(email)


_AUTOREPLY_SUBJECTS = (
    "respuesta automática", "respuesta automatica", "automatic reply", "autoreply",
    "auto-reply", "out of office", "fuera de la oficina", "fuera de oficina",
    "delivery status notification", "undeliverable", "no se puede entregar",
    "mail delivery",
)
_TOOL_SENDERS = ("github.com", "google.com", "googlemail.com", "drive-shares")


def _classify_aetym(email: dict) -> str:
    """
    Devuelve 'auto' (leído) o 'atencion' (sin leer). Siempre se archiva.
    Orden: prueba → solicitud real → automáticos → resto (duda ⇒ atención).
    """
    subject   = email.get("subject", "")
    su_lo     = subject.lower()
    fe_lo     = email.get("fromEmail", "").lower()
    fn_lo     = email.get("fromName", "").lower()
    from_hdr  = _hdr(email, "From").lower()

    # a) Correos de prueba
    if "[prueba]" in su_lo or "aetym - pruebas" in fn_lo:
        return "auto"

    # b) Solicitudes reales del formulario (sin [Prueba])
    if "[solicitud]" in su_lo or "nueva solicitud desde aetym.com" in su_lo:
        return "atencion"

    # Reenvíos: requieren atención aunque vengan de una persona o herramienta
    if re.match(r"^\s*(fw|fwd|rv)\s*:", su_lo):
        return "atencion"

    # a) Rebotes
    if "mailer-daemon" in fe_lo or "mailer-daemon" in from_hdr or "mail delivery subsystem" in from_hdr \
            or "postmaster@" in fe_lo:
        return "auto"

    # a) Respuestas automáticas (cabeceras estándar o asunto típico)
    auto_sub = _hdr(email, "Auto-Submitted").lower()
    if (auto_sub and auto_sub != "no") \
            or _hdr(email, "X-Autoreply") or _hdr(email, "X-Autorespond") \
            or _hdr(email, "Precedence").lower() in ("auto_reply", "bulk", "junk") \
            or any(s in su_lo for s in _AUTOREPLY_SUBJECTS):
        return "auto"

    # a) Confirmación al remitente del formulario
    if "hemos recibido tu solicitud" in su_lo:
        return "auto"

    # a) Avisos de herramientas (GitHub, Google Drive...)
    if any(fe_lo.endswith(d) or f"@{d}" in fe_lo or f".{d}" in fe_lo for d in _TOOL_SENDERS):
        return "auto"

    # b) Persona que escribe / duda → sin leer
    return "atencion"


_ACCESSONE_ALERT_WORDS = (
    "error", "alerta", "fallido", "fallida", "fallo", "denegado", "denegada",
    "failed", "denied", "alert",
)


def _is_accessone(email: dict) -> bool:
    su = email.get("subject", "")
    return su.startswith("Primer acceso a Tu Trastero") or "accessone" in su.lower()


def _accessone_has_alert(email: dict) -> bool:
    body = _text_body(email)
    return any(w in body for w in _ACCESSONE_ALERT_WORDS)


# ─── Regla A3 · Contabilidad ──────────────────────────────────────────────────

_A3_TAG       = "[a3 · contabilidad]"
_REPLY_PREFIX = re.compile(r"^\s*((re|fw|fwd|rv)\s*:\s*)+", re.I)
_A3_NUEVAS    = re.compile(r"\((\d+)\s+nuevas?\)", re.I)


def _classify_a3(email: dict) -> str | None:
    """
    None → no es A3. Si lo es: 'auto' (leído) o 'atencion' (sin leer). Siempre se archiva.
    """
    subject  = email.get("subject", "")
    stripped = _REPLY_PREFIX.sub("", subject).strip().lower()
    if not stripped.startswith(_A3_TAG):
        return None

    # Reenvío / respuesta de un aviso: siempre sin leer
    if _REPLY_PREFIX.match(subject):
        return "atencion"

    # Aviso original: sólo si lo envía App Spaceman
    if email.get("fromEmail", "").lower() != "notificacion@tutrastero.com":
        return None

    body = _text_body(email)
    if "sin incidencias" in stripped or "a3 erp: test" in body:
        return "auto"
    m = _A3_NUEVAS.search(subject)
    if "a3 erp: prod" in body and m and int(m.group(1)) > 0:
        return "atencion"
    return "auto"   # PROD con incidencias pero ninguna nueva


# ─── Router principal — BasicRouter módulo 2 ──────────────────────────────────

async def _route_email(email: dict) -> tuple[list[str], str, dict]:
    """
    Evalúa TODAS las rutas del blueprint de forma independiente (no exclusivas).
    Devuelve (labels, descripcion, acciones). acciones = {"mark_read": bool, "keep_unread": bool}.
    Todo correo se archiva; keep_unread=True anula el marcado como leído de cualquier ruta.
    """
    actions = {"mark_read": False, "keep_unread": False}
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
        try:
            cgi_labels, cgi_desc = await _handle_from_cgi(subject, body)
            labels.extend(cgi_labels)
            if cgi_desc:
                results.append(cgi_desc)
        except Exception as exc:
            logger.error(f"  ❌ CGI sub-router error: {exc}", exc_info=True)
            labels.append(config.LABEL_CGI_CLASIF)
            results.append("CGI(error)")
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
    if from_email == "contabilidad@tutrastero.com" or "cobro de moroso" in su_lo or "oportunidad única -" in subject:
        labels.append(config.LABEL_BOT_MOROSOS); _hit("Bot-Llamada-Morosos")
    else:
        _miss("Bot-Llamada-Morosos")

    # Ruta 13 — YouSign
    if from_email == "firmas.online@tutrastero.com":
        labels.append(config.LABEL_YOUSIGN); _hit("YouSign")
    else:
        _miss("YouSign")

    # Ruta 14 — AETYM (prioridad sobre AccessOne)
    aetym = _is_aetym(email)
    if aetym:
        tipo = _classify_aetym(email)
        if config.LABEL_AETYM:
            labels.append(config.LABEL_AETYM)
        else:
            logger.warning("  ⚠ LABEL_AETYM no configurado — no se aplica etiqueta")
        if tipo == "auto":
            actions["mark_read"] = True
        else:
            actions["keep_unread"] = True
        _hit(f"AETYM({tipo})")
    else:
        _miss("AETYM")

    # Ruta 15 — AccessOne (sólo si no es AETYM)
    if not aetym and _is_accessone(email):
        if config.LABEL_ACCESSONE:
            labels.append(config.LABEL_ACCESSONE)
        else:
            logger.warning("  ⚠ LABEL_ACCESSONE no configurado — no se aplica etiqueta")
        if _accessone_has_alert(email):
            actions["keep_unread"] = True
            _hit("AccessOne(alerta→sin leer)")
        else:
            actions["mark_read"] = True
            _hit("AccessOne")
    else:
        _miss("AccessOne")

    # Ruta 16 — A3 · Contabilidad
    a3 = _classify_a3(email)
    if a3:
        if config.LABEL_A3:
            labels.append(config.LABEL_A3)
        else:
            logger.warning("  ⚠ LABEL_A3 no configurado — no se aplica etiqueta")
        if a3 == "auto":
            actions["mark_read"] = True
        else:
            actions["keep_unread"] = True
        _hit(f"A3-Contabilidad({a3})")
    else:
        _miss("A3-Contabilidad")

    labels = [l for l in labels if l]
    descripcion = ", ".join(results) if results else "Sin etiqueta"
    return list(dict.fromkeys(labels)), descripcion, actions


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

    labels, descripcion, actions = await _route_email(email)

    # Todo correo etiquetado sale de Recibidos (queda sólo en su etiqueta).
    # Los automáticos se marcan leídos; los que requieren atención quedan sin leer.
    remove = ["INBOX"]
    if actions["mark_read"] and not actions["keep_unread"]:
        remove.append("UNREAD")

    if labels:
        await gmail.apply_labels(message_id, add_labels=labels, remove_labels=remove)
        logger.info(f"[0] Etiquetas: {descripcion} | archivado"
                    f"{' y leído' if 'UNREAD' in remove else ' (sin leer)'}")
    else:
        await gmail.apply_labels(message_id, add_labels=[config.LABEL_SISTEMAS_PROCESADO], remove_labels=remove)
        logger.info("[0] Sin ruta — etiquetado Sistemas-Procesado y archivado")

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
