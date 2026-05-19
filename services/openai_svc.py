"""
services/openai_svc.py — Clasificación de emails con OpenAI (módulo 50 del blueprint).

Replica el módulo transformTextToStructuredData del blueprint:
  · Modelo: gpt-4.1
  · Input: subject + fullTextBody del email
  · Contexto: Definiciones + Ejemplos de Clasificación de Airtable
  · Output: campo "categoria" (string)
"""
import asyncio
import json

from openai import AsyncOpenAI

import config

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _format_records(records: list[dict]) -> str:
    """Convierte registros de Airtable en texto (replica el BasicAggregator del blueprint)."""
    parts = []
    for rec in records:
        fields = rec.get("fields", {})
        parts.append(json.dumps(fields, ensure_ascii=False))
    return "\n".join(parts)


async def classify_email(
    subject: str,
    body: str,
    definiciones_records: list[dict],
    ejemplos_records: list[dict],
) -> str:
    """
    Clasifica el email y devuelve la categoría (string).
    Replica el módulo 50 (transformTextToStructuredData) del blueprint.
    """
    definiciones_text = _format_records(definiciones_records)
    ejemplos_text     = _format_records(ejemplos_records)

    prompt = (
        "Eres un asistente experto en la clasificación de correos para un negocio de self-storage. "
        "Tu única tarea es clasificar el correo electrónico proporcionado en una de las siguientes categorias.\n\n"
        "Utiliza las definiciones como guía general y los ejemplos como casos prácticos de alta prioridad.\n\n"
        f"--- DEFINICIONES DE CATEGORÍAS ---\n{definiciones_text}\n\n"
        f"--- EJEMPLOS DE CLASIFICACIÓN ---\n{ejemplos_text}\n\n"
        "--- INSTRUCCIÓN ---\n"
        "Basado en las definiciones, los ejemplos y el contenido del correo, ¿a qué categoría pertenece? "
        "Responde únicamente con la palabra exacta de la \"categoria\""
    )

    client = _get_client()
    response = await client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "user", "content": prompt + f"\n\n--- CORREO ---\n{subject}\n{body}"},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()
