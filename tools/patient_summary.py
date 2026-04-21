# tools/patient_summary.py
from __future__ import annotations
import json
from datetime import date
from fhir_client import SharpContext, fetch_fhir, get_entries, code_text, fmt_date


async def get_patient_summary(ctx: SharpContext) -> str:
    bundle = await fetch_fhir(ctx, "Patient")
    entries = get_entries(bundle)

    # Patient endpoint may return Patient directly or a Bundle
    patient = bundle if bundle.get("resourceType") == "Patient" else next(
        (e for e in entries if e.get("resourceType") == "Patient"), None
    )

    if not patient:
        return json.dumps({"error": "No patient record found"})

    # Name
    name_obj = (patient.get("name") or [{}])[0]
    given = " ".join(name_obj.get("given", []))
    family = name_obj.get("family", "")
    full_name = f"{given} {family}".strip() or "Unknown"

    # Address
    addr = (patient.get("address") or [{}])[0]
    address_str = ", ".join(filter(None, [
        " ".join(addr.get("line", [])),
        addr.get("city"),
        addr.get("state"),
        addr.get("postalCode"),
        addr.get("country"),
    ])) or "Unknown"

    # Age
    birth = patient.get("birthDate")
    age = None
    if birth:
        try:
            bd = date.fromisoformat(birth)
            today = date.today()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except Exception:
            pass

    # Extensions
    extensions = patient.get("extension", [])
    race = next((
        sub.get("valueString", "Unknown")
        for ext in extensions if "us-core-race" in ext.get("url", "")
        for sub in ext.get("extension", []) if sub.get("url") == "text"
    ), "Unknown")

    ethnicity = next((
        sub.get("valueString", "Unknown")
        for ext in extensions if "us-core-ethnicity" in ext.get("url", "")
        for sub in ext.get("extension", []) if sub.get("url") == "text"
    ), "Unknown")

    # MRN
    mrn = next((
        i.get("value", "Unknown")
        for i in patient.get("identifier", [])
        if (i.get("type") or {}).get("text") == "Medical Record Number"
    ), "Unknown")

    summary = {
        "tool": "get_patient_summary",
        "patient_id": patient.get("id", "Unknown"),
        "full_name": full_name,
        "gender": patient.get("gender", "Unknown"),
        "birth_date": fmt_date(birth),
        "age": age,
        "address": address_str,
        "phone": next((
            t.get("value") for t in patient.get("telecom", []) if t.get("system") == "phone"
        ), "Unknown"),
        "marital_status": code_text(patient.get("maritalStatus")),
        "language": (patient.get("communication") or [{}])[0].get("language", {}).get("text", "Unknown"),
        "race": race,
        "ethnicity": ethnicity,
        "deceased": patient.get("deceasedBoolean", False),
        "deceased_date": fmt_date(patient.get("deceasedDateTime")),
        "mrn": mrn,
    }
    return json.dumps(summary, indent=2)
