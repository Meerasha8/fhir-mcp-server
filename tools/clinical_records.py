# tools/clinical_records.py
from __future__ import annotations
import asyncio, json
from fhir_client import SharpContext, fetch_fhir, get_entries, code_text, fmt_date


async def get_clinical_records(ctx: SharpContext) -> str:
    results = await asyncio.gather(
        fetch_fhir(ctx, "Condition"),
        fetch_fhir(ctx, "AllergyIntolerance"),
        fetch_fhir(ctx, "Procedure"),
        fetch_fhir(ctx, "FamilyMemberHistory"),
        return_exceptions=True,
    )

    cond_bundle, allergy_bundle, proc_bundle, fam_bundle = results

    # Conditions
    conditions = []
    if not isinstance(cond_bundle, Exception):
        for c in get_entries(cond_bundle):
            conditions.append({
                "name": code_text(c.get("code")),
                "status": code_text(c.get("clinicalStatus")),
                "verification": code_text(c.get("verificationStatus")),
                "category": code_text((c.get("category") or [None])[0]),
                "onset_date": fmt_date(c.get("onsetDateTime") or (c.get("onsetPeriod") or {}).get("start")),
                "recorded_date": fmt_date(c.get("recordedDate")),
            })

    # Allergies
    allergies = []
    if not isinstance(allergy_bundle, Exception):
        for a in get_entries(allergy_bundle):
            reactions = []
            for r in a.get("reaction", []):
                reactions.append({
                    "substance": code_text(r.get("substance")),
                    "manifestations": [code_text(m) for m in r.get("manifestation", [])],
                    "severity": r.get("severity", "unknown"),
                })
            allergies.append({
                "substance": code_text(a.get("code")),
                "type": a.get("type", "unknown"),
                "category": a.get("category", []),
                "criticality": a.get("criticality", "unknown"),
                "status": code_text(a.get("clinicalStatus")),
                "onset": fmt_date(a.get("onsetDateTime")),
                "reactions": reactions,
            })

    # Procedures
    procedures = []
    if not isinstance(proc_bundle, Exception):
        for p in get_entries(proc_bundle):
            procedures.append({
                "name": code_text(p.get("code")),
                "status": p.get("status", "unknown"),
                "performed_date": fmt_date(
                    p.get("performedDateTime") or (p.get("performedPeriod") or {}).get("start")
                ),
                "performer": (p.get("performer") or [{}])[0].get("actor", {}).get("display", "Unknown"),
                "location": (p.get("location") or {}).get("display", "Unknown"),
                "reason": code_text((p.get("reasonCode") or [None])[0]),
            })

    # Family history
    family = []
    if not isinstance(fam_bundle, Exception):
        for f in get_entries(fam_bundle):
            family.append({
                "relationship": code_text(f.get("relationship")),
                "sex": code_text(f.get("sex")),
                "deceased": f.get("deceasedBoolean"),
                "conditions": [
                    {
                        "code": code_text(fc.get("code")),
                        "outcome": code_text(fc.get("outcome")),
                        "onset_age": f"{fc['onsetAge']['value']} {fc['onsetAge'].get('unit','')}" if fc.get("onsetAge") else None,
                    }
                    for fc in f.get("condition", [])
                ],
            })

    active = [c for c in conditions if "active" in c["status"].lower()]
    resolved = [c for c in conditions if "active" not in c["status"].lower()]

    return json.dumps({
        "tool": "get_clinical_records",
        "counts": {
            "conditions": len(conditions),
            "active_conditions": len(active),
            "allergies": len(allergies),
            "procedures": len(procedures),
            "family_history": len(family),
        },
        "active_conditions": active,
        "resolved_conditions": resolved,
        "allergies_and_intolerances": allergies,
        "procedures": procedures[:20],
        "family_history": family,
    }, indent=2)
