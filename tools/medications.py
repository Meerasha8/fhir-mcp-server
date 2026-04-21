# tools/medications.py
from __future__ import annotations
import asyncio, json
from fhir_client import SharpContext, fetch_fhir, get_entries, code_text, fmt_date


async def get_medications(ctx: SharpContext) -> str:
    results = await asyncio.gather(
        fetch_fhir(ctx, "MedicationRequest"),
        fetch_fhir(ctx, "MedicationStatement"),
        fetch_fhir(ctx, "MedicationAdministration"),
        fetch_fhir(ctx, "MedicationDispense"),
        return_exceptions=True,
    )
    req_b, stmt_b, admin_b, disp_b = results

    def med_name(r: dict) -> str:
        return (
            code_text(r.get("medicationCodeableConcept"))
            or (r.get("medicationReference") or {}).get("display", "Unknown")
        )

    # Prescriptions
    requests = []
    if not isinstance(req_b, Exception):
        for m in get_entries(req_b):
            di = (m.get("dosageInstruction") or [{}])[0]
            requests.append({
                "medication": med_name(m),
                "status": m.get("status", "unknown"),
                "intent": m.get("intent", "unknown"),
                "authored_on": fmt_date(m.get("authoredOn")),
                "requester": (m.get("requester") or {}).get("display", "Unknown"),
                "dosage_instruction": di.get("text"),
                "route": code_text(di.get("route")),
                "frequency": code_text((di.get("timing") or {}).get("code")),
                "reason": code_text((m.get("reasonCode") or [None])[0]),
            })

    # Statements
    statements = []
    if not isinstance(stmt_b, Exception):
        for m in get_entries(stmt_b):
            statements.append({
                "medication": med_name(m),
                "status": m.get("status", "unknown"),
                "date_asserted": fmt_date(m.get("dateAsserted")),
                "effective_date": fmt_date(
                    m.get("effectiveDateTime") or (m.get("effectivePeriod") or {}).get("start")
                ),
                "dosage": (m.get("dosage") or [{}])[0].get("text"),
                "information_source": (m.get("informationSource") or {}).get("display", "Unknown"),
            })

    # Administrations
    admins = []
    if not isinstance(admin_b, Exception):
        for m in get_entries(admin_b):
            dosage = m.get("dosage") or {}
            dose = dosage.get("dose") or {}
            admins.append({
                "medication": med_name(m),
                "status": m.get("status", "unknown"),
                "effective_date": fmt_date(
                    m.get("effectiveDateTime") or (m.get("effectivePeriod") or {}).get("start")
                ),
                "performer": ((m.get("performer") or [{}])[0].get("actor") or {}).get("display", "Unknown"),
                "dose": f"{dose.get('value', '')} {dose.get('unit', '')}".strip() or None,
                "route": code_text(dosage.get("route")),
            })

    # Dispenses
    dispenses = []
    if not isinstance(disp_b, Exception):
        for m in get_entries(disp_b):
            qty = m.get("quantity") or {}
            dispenses.append({
                "medication": med_name(m),
                "status": m.get("status", "unknown"),
                "quantity": f"{qty.get('value', '')} {qty.get('unit', '')}".strip() or None,
                "days_supply": (m.get("daysSupply") or {}).get("value"),
                "when_prepared": fmt_date(m.get("whenPrepared")),
                "when_handed_over": fmt_date(m.get("whenHandedOver")),
                "performer": ((m.get("performer") or [{}])[0].get("actor") or {}).get("display", "Unknown"),
            })

    active = [r for r in requests if r["status"] == "active"]
    historical = [r for r in requests if r["status"] != "active"]

    return json.dumps({
        "tool": "get_medications",
        "counts": {
            "prescriptions": len(requests),
            "active_prescriptions": len(active),
            "statements": len(statements),
            "administrations": len(admins),
            "dispenses": len(dispenses),
        },
        "active_prescriptions": active,
        "historical_prescriptions": historical,
        "reported_medications": statements,
        "administrations": admins[:10],
        "recent_dispenses": dispenses[:10],
    }, indent=2)
