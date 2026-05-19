"""
services/gmail.py — Wrapper asíncrono sobre Gmail API (sistemas@tutrastero.com).

Las llamadas al SDK de Google son síncronas; se ejecutan en hilo con asyncio.to_thread().
"""
import asyncio
import base64
import re

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import config

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_SISTEMAS_PROCESADO_LABEL_ID: str | None = None


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

def _list_unread_sync() -> list[dict]:
    svc = _build_service()
    res = svc.users().messages().list(
        userId="me",
        labelIds=["INBOX", "UNREAD"],
        q="-label:Sistemas-Procesado",
        maxResults=20,
    ).execute()
    return res.get("messages", [])


def _get_email_sync(message_id: str) -> dict:
    svc = _build_service()
    msg = svc.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    return _parse_message(msg)


def _get_or_create_label_sync(name: str) -> str:
    global _SISTEMAS_PROCESADO_LABEL_ID
    if _SISTEMAS_PROCESADO_LABEL_ID:
        return _SISTEMAS_PROCESADO_LABEL_ID
    svc = _build_service()
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"].lower() == name.lower():
            _SISTEMAS_PROCESADO_LABEL_ID = label["id"]
            return label["id"]
    result = svc.users().labels().create(userId="me", body={"name": name}).execute()
    _SISTEMAS_PROCESADO_LABEL_ID = result["id"]
    return _SISTEMAS_PROCESADO_LABEL_ID


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


def _mark_processed_sync(message_id: str):
    label_id = _get_or_create_label_sync("Sistemas-Procesado")
    svc = _build_service()
    svc.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
    ).execute()


def _setup_watch_sync(topic_name: str) -> dict:
    svc = _build_service()
    return svc.users().watch(userId="me", body={
        "topicName":           topic_name,
        "labelIds":            ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    }).execute()


# ─── API pública asíncrona ────────────────────────────────────────────────────

async def list_unread_emails() -> list[dict]:
    return await asyncio.to_thread(_list_unread_sync)


async def get_email(message_id: str) -> dict:
    return await asyncio.to_thread(_get_email_sync, message_id)


async def apply_labels(
    message_id: str,
    add_labels: list[str],
    remove_labels: list[str] = None,
):
    """Aplica/quita etiquetas a un mensaje Gmail."""
    await asyncio.to_thread(_apply_labels_sync, message_id, add_labels, remove_labels or [])


async def move_to_label(message_id: str, label_id: str):
    """Mueve el email de INBOX a la etiqueta destino (replica moveAnEmail del blueprint)."""
    await asyncio.to_thread(_apply_labels_sync, message_id, [label_id], ["INBOX"])


async def mark_processed(message_id: str):
    """Añade Sistemas-Procesado y quita UNREAD para evitar reprocesar."""
    await asyncio.to_thread(_mark_processed_sync, message_id)


async def setup_watch(topic_name: str) -> dict:
    return await asyncio.to_thread(_setup_watch_sync, topic_name)
