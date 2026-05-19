"""
services/airtable.py — Cliente asíncrono para la API REST de Airtable.
"""
import httpx
import config

_BASE_URL = f"https://api.airtable.com/v0/{config.AIRTABLE_BASE_ID}"
_TIMEOUT  = 30


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.AIRTABLE_TOKEN}",
        "Content-Type":  "application/json",
    }


async def search_records(
    table_id: str,
    formula: str,
    fields: list[str] = None,
    max_records: int = 1,
    view: str = None,
) -> list[dict]:
    params: dict = {
        "filterByFormula": formula,
        "maxRecords":      max_records,
    }
    if view:
        params["view"] = view

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_BASE_URL}/{table_id}",
            headers=_headers(),
            params=params,
        )
        r.raise_for_status()
        records = r.json().get("records", [])

    if fields and records:
        for rec in records:
            rec["fields"] = {k: v for k, v in rec["fields"].items() if k in fields}

    return records


async def list_all_records(table_id: str) -> list[dict]:
    """Descarga todos los registros de una tabla (sigue la paginación)."""
    records = []
    offset  = None

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            params: dict = {}
            if offset:
                params["offset"] = offset
            r = await client.get(
                f"{_BASE_URL}/{table_id}",
                headers=_headers(),
                params=params,
            )
            r.raise_for_status()
            body    = r.json()
            records.extend(body.get("records", []))
            offset  = body.get("offset")
            if not offset:
                break

    return records


async def update_record(table_id: str, record_id: str, fields: dict) -> dict:
    """Actualiza campos de un registro existente (PATCH)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.patch(
            f"{_BASE_URL}/{table_id}/{record_id}",
            headers=_headers(),
            json={"fields": fields},
        )
        r.raise_for_status()
        return r.json()
