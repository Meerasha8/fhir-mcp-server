# tools/encounters.py
from __future__ import annotations
import asyncio, json
from fhir_client import SharpContext, fetch_fhir, get_entries, code_text, fmt_date


async def get_encounters(ctx: SharpContext) -> str:
    results = await asyncio.gather(
        fetch_fhir(ctx, "Encounter", {"_sort": "-date", "_count": "50"}),
        fetch_fhir(ctx, "EpisodeOfCare"),
        fetch_fhir(ctx, "Appointment", {"_sort": "-date", "_count": "20"}),
        return_exceptions=True,
    )
    enc_b, eoc_b, appt_b = results

    encounters = []
    if not isinstance(enc_b, Exception):
        for e in get_entries(enc_b):
            period = e.get("period") or {}
            cls = e.get("class") or {}
            enc_class = cls.get("display") or cls.get("code") or "Unknown"
            encounters.append({
                "type": ", ".join(code_text(t) for t in e.get("type", [])) or "Unknown",
                "class": enc_class,
                "status": e.get("status", "unknown"),
                "start": fmt_date(period.get("start")),
                "end": fmt_date(period.get("end")),
                "duration": f"{e['length']['value']} {e['length'].get('unit','min')}" if e.get("length") else None,
                "service_provider": (e.get("serviceProvider") or {}).get("display"),
                "participants": [
                    {
                        "role": code_text((p.get("type") or [None])[0]),
                        "name": (p.get("individual") or {}).get("display", "Unknown"),
                    }
                    for p in e.get("participant", [])
                ],
                "reason_codes": [code_text(r) for r in e.get("reasonCode", [])],
                "diagnoses": [
                    {
                        "condition": (d.get("condition") or {}).get("display", "Unknown"),
                        "use": code_text(d.get("use")),
                        "rank": d.get("rank"),
                    }
                    for d in e.get("diagnosis", [])
                ],
                "location": ((e.get("location") or [{}])[0].get("location") or {}).get("display"),
                "hospitalization": {
                    "admit_source": code_text((e.get("hospitalization") or {}).get("admitSource")),
                    "discharge_disposition": code_text((e.get("hospitalization") or {}).get("dischargeDisposition")),
                } if e.get("hospitalization") else None,
            })

    episodes = []
    if not isinstance(eoc_b, Exception):
        for e in get_entries(eoc_b):
            period = e.get("period") or {}
            episodes.append({
                "status": e.get("status", "unknown"),
                "types": [code_text(t) for t in e.get("type", [])],
                "period": f"{fmt_date(period.get('start'))} → {fmt_date(period.get('end'))}" if period else None,
                "managing_organization": (e.get("managingOrganization") or {}).get("display"),
                "care_manager": (e.get("careManager") or {}).get("display"),
                "diagnoses": [
                    {
                        "condition": (d.get("condition") or {}).get("display", "Unknown"),
                        "role": code_text(d.get("role")),
                        "rank": d.get("rank"),
                    }
                    for d in e.get("diagnosis", [])
                ],
            })

    appointments = []
    if not isinstance(appt_b, Exception):
        for a in get_entries(appt_b):
            appointments.append({
                "status": a.get("status", "unknown"),
                "service_type": ", ".join(code_text(s) for s in a.get("serviceType", [])) or None,
                "specialty": ", ".join(code_text(s) for s in a.get("specialty", [])) or None,
                "appointment_type": code_text(a.get("appointmentType")),
                "start": fmt_date(a.get("start")),
                "end": fmt_date(a.get("end")),
                "minutes_duration": a.get("minutesDuration"),
                "description": a.get("description"),
                "comment": a.get("comment"),
                "participants": [
                    {
                        "role": code_text((p.get("type") or [None])[0]),
                        "name": (p.get("actor") or {}).get("display", "Unknown"),
                        "status": p.get("status", "unknown"),
                    }
                    for p in a.get("participant", [])
                ],
            })

    inpatient  = [e for e in encounters if any(x in e["class"].upper() for x in ["IMP", "INPATIENT"])]
    outpatient = [e for e in encounters if any(x in e["class"].upper() for x in ["AMB", "AMBULATORY", "OUTPATIENT"])]
    emergency  = [e for e in encounters if any(x in e["class"].upper() for x in ["EMER", "EMERGENCY"])]

    upcoming = [a for a in appointments if a["status"] in ("booked", "pending")]
    past     = [a for a in appointments if a["status"] in ("fulfilled", "noshow", "cancelled")]

    return json.dumps({
        "tool": "get_encounters",
        "counts": {
            "total_encounters": len(encounters),
            "inpatient": len(inpatient),
            "outpatient": len(outpatient),
            "emergency": len(emergency),
            "episodes_of_care": len(episodes),
            "appointments": len(appointments),
        },
        "recent_encounters": encounters[:10],
        "episodes_of_care": episodes,
        "upcoming_appointments": upcoming,
        "past_appointments": past,
    }, indent=2)
