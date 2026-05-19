"""
services/gmail.py — Wrapper asíncrono sobre Gmail API (sistemas@tutrastero.com).

Replica el trigger "triggerWatchNewEmails" de Make.com:
  · Primera ejecución: procesa hasta 20 emails recientes del INBOX y almacena historyId.
  · Ejecuciones siguientes: usa la History API para obtener sólo los mensajes NUEVOS
    desde el último historyId conocido (sin filtro UNREAD, sin etiqueta extra).
  · markSeen: false — los emails NO se marcan como leídos al procesarlos.
"""
import asyncio
import base64
import os
import re

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import config

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_STATE_DIR  = ".state"
_STATE_FILE = os.path.join(_STATE_DIR, "history_id.txt")

_last_history_id: str | None = None


# ─── Estado persistente ───────────────────────────────────────────────────────

def _load_state():
    global _last_history_id
    if os.path.exists(_STATE_FILE):
        data = open(_STATE_FILE).read().strip()
        _last_history_id = data or None


def _save_state():
    os.makedirs(_STATE_DIR, exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        f.write(_last_history_id or "")


_load_state()


# ─── Autenticación ────────────────────────────────────────────────────────────

def _build_service():
    creds = Credentials(
        token=None,
        refresh_token=config.GMAIL_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GMAIL_CLIENT_ID,
        client_secret=config.GMAIL_CLIENT_SECRET,
        scopes=_SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ─── Helpers internos ─────────────────────────────────────────────────────────

def _parse_from(from_header: str) -> tuple[str, str]:
    m = re.match(r'^"?([^"<]*)"?\s*<([^>]+)>', from_header.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", from_header.strip()


def _parse_to(to_header: str) -> list[dict]:
    result = []
    for entry in to_header.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, email = _parse_from(entry)
        result.append({"name": name, "email": email})
    return result


def _extract_body(payload: dict, mime_type: str) -> str:
    if payload.get("mimeType") == mime_type:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        result = _extract_body(part, mime_type)
        if result:
            return result
    return ""


def _parse_message(msg: dict) -> dict:
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    from_name, from_email = _parse_from(headers.get("From", ""))
    payload = msg.get("payload", {})
    return {
        "id":           msg["id"],
        "threadId":     msg["threadId"],
        "subject":      headers.get("Subject", ""),
        "fromName":     from_name,
        "fromEmail":    from_email,
        "to":           _parse_to(headers.get("To", "")),
        "headers":      headers,
        "fullTextBody": _extract_body(payload, "text/plain"),
        "htmlBody":     _extract_body(payload, "text/html"),
        "snippet":      msg.get("snippet", ""),
    }


# ─── Funciones síncronas ──────────────────────────────────────────────────────

def _list_new_emails_sync() -> list[dict]:
    """
    Replica triggerWatchNewEmails de Make.com (criteria="all", markSeen=false).
    Primera llamada: devuelve últimos 20 mensajes del INBOX y guarda historyId.
    Siguientes: usa History API para devolver sólo los mensajes añadidos al INBOX.
    """
    global _last_history_id
    svc = _build_service()

    if _last_history_id is None:
        res = svc.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=20,
        ).execute()
        profile = svc.users().getProfile(userId="me").execute()
        _last_history_id = profile["historyId"]
        _save_state()
        return res.get("messages", [])

    try:
        history_res = svc.users().history().list(
            userId="me",
            startHistoryId=_last_history_id,
            historyTypes=["messageAdded"],
            labelId="INBOX",
        ).execute()

        new_history_id = history_res.get("historyId", _last_history_id)
        _last_history_id = new_history_id
        _save_state()

        seen: dict[str, bool] = {}
        for record in history_res.get("history", []):
            for msg_added in record.get("messagesAdded", []):
                msg = msg_added.get("message", {})
                if "INBOX" in msg.get("labelIds", []):
                    seen[msg["id"]] = True

        return [{"id": mid} for mid in seen]

    except Exception as exc:
        if "404" in str(exc) or "historyId" in str(exc).lower():
            _last_history_id = None
            _save_state()
        raise


def _get_email_sync(message_id: str) -> dict:
    svc = _build_service()
    msg = svc.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    return _parse_message(msg)


def _apply_labels_sync(
    message_id: str,
    add_labels: list[str],
    remove_labels: list[str] = None,
):
    svc = _build_service()
    body = {"addLabelIds": add_labels}
    if remove_labels:
        body["removeLabelIds"] = remove_labels
    svc.users().messages().modify(
        userId="me", id=message_id, body=body
    ).execute()


def _setup_watch_sync(topic_name: str) -> dict:
    svc = _build_service()
    return svc.users().watch(userId="me", body={
        "topicName":           topic_name,
        "labelIds":            ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    }).execute()


# ─── API pública asíncrona ────────────────────────────────────────────────────

async def list_new_emails() -> list[dict]:
    return await asyncio.to_thread(_list_new_emails_sync)


async def get_email(message_id: str) -> dict:
    return await asyncio.to_thread(_get_email_sync, message_id)


async def apply_labels(
    message_id: str,
    add_labels: list[str],
    remove_labels: list[str] = None,
):
    await asyncio.to_thread(_apply_labels_sync, message_id, add_labels, remove_labels or [])


async def setup_watch(topic_name: str) -> dict:
    return await asyncio.to_thread(_setup_watch_sync, topic_name)
