# fhir_client.py
# SHARP context extraction + FHIR fetch helpers

from __future__ import annotations
import httpx
from dataclasses import dataclass
from typing import Any


@dataclass
class SharpContext:
    fhir_server_url: str
    access_token: str
    patient_id: str


def extract_sharp_context(headers: dict[str, str]) -> SharpContext:
    """Extract SHARP context from incoming MCP request headers."""
    # Normalise header keys to lowercase
    h = {k.lower(): v for k, v in headers.items()}

    url   = h.get("x-fhir-server-url", "").rstrip("/")
    token = h.get("x-fhir-access-token", "")
    pid   = h.get("x-patient-id", "")

    if not url:
        raise ValueError("Missing X-FHIR-Server-URL header")
    if not token:
        raise ValueError("Missing X-FHIR-Access-Token header")
    if not pid:
        raise ValueError("Missing X-Patient-ID header")

    return SharpContext(fhir_server_url=url, access_token=token, patient_id=pid)


async def fetch_fhir(
    ctx: SharpContext,
    resource_type: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch a FHIR resource bundle for the patient."""
    query: dict[str, str] = {"patient": ctx.patient_id}
    if params:
        query.update(params)

    url = f"{ctx.fhir_server_url}/{resource_type}"
    headers = {
        "Authorization": f"Bearer {ctx.access_token}",
        "Accept": "application/fhir+json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=query, headers=headers)
        resp.raise_for_status()
        return resp.json()


def get_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract resource entries from a FHIR Bundle."""
    if not bundle or bundle.get("resourceType") != "Bundle":
        return []
    return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]


def code_text(cc: Any) -> str:
    """Extract display text from a CodeableConcept or string."""
    if cc is None:
        return "Unknown"
    if isinstance(cc, str):
        return cc
    if isinstance(cc, dict):
        if cc.get("text"):
            return cc["text"]
        codings = cc.get("coding", [])
        if codings:
            return codings[0].get("display") or codings[0].get("code") or "Unknown"
    return "Unknown"


def fmt_date(date_str: str | None) -> str:
    """Format a FHIR date string to a readable form."""
    if not date_str:
        return "Unknown"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except Exception:
        return date_str
