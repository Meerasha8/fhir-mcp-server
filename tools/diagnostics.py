# tools/diagnostics.py
from __future__ import annotations
import asyncio, json
from fhir_client import SharpContext, fetch_fhir, get_entries, code_text, fmt_date

ABNORMAL_CODES = {"H", "L", "HH", "LL", "A", "AA", "HIGH", "LOW", "ABNORMAL", "CRITICALLY HIGH", "CRITICALLY LOW"}


def _is_abnormal(obs: dict) -> bool:
    for interp in obs.get("interpretation", []):
        for coding in interp.get("coding", []):
            if coding.get("code", "").upper() in ABNORMAL_CODES:
                return True
        if interp.get("text", "").upper() in ABNORMAL_CODES:
            return True
    return False


def _obs_value(obs: dict) -> str:
    if obs.get("valueQuantity"):
        q = obs["valueQuantity"]
        return f"{q.get('value', '')} {q.get('unit', '')}".strip()
    if obs.get("valueCodeableConcept"):
        return code_text(obs["valueCodeableConcept"])
    if obs.get("valueString"):
        return obs["valueString"]
    if obs.get("valueBoolean") is not None:
        return str(obs["valueBoolean"])
    return "N/A"


def _ref_range(obs: dict) -> str | None:
    rr = (obs.get("referenceRange") or [{}])[0]
    if not rr:
        return None
    low = (rr.get("low") or {})
    high = (rr.get("high") or {})
    unit = low.get("unit") or high.get("unit") or ""
    return f"{low.get('value','?')} – {high.get('value','?')} {unit}".strip()


def _map_obs(o: dict) -> dict:
    return {
        "name": code_text(o.get("code")),
        "value": _obs_value(o),
        "date": fmt_date(o.get("effectiveDateTime") or (o.get("effectivePeriod") or {}).get("start")),
        "status": o.get("status", "unknown"),
        "interpretation": code_text((o.get("interpretation") or [None])[0]),
        "reference_range": _ref_range(o),
        "abnormal": _is_abnormal(o),
    }


async def get_diagnostics(ctx: SharpContext) -> str:
    results = await asyncio.gather(
        fetch_fhir(ctx, "Observation", {"_sort": "-date", "_count": "150"}),
        fetch_fhir(ctx, "DiagnosticReport", {"_sort": "-date", "_count": "50"}),
        fetch_fhir(ctx, "ImagingStudy"),
        fetch_fhir(ctx, "Immunization", {"_sort": "-date"}),
        return_exceptions=True,
    )
    obs_b, report_b, imaging_b, immun_b = results

    vitals, labs, other_obs = [], [], []
    if not isinstance(obs_b, Exception):
        for o in get_entries(obs_b):
            cats = [code_text(c).lower() for c in o.get("category", [])]
            mapped = _map_obs(o)
            if any("vital" in c for c in cats):
                vitals.append(mapped)
            elif any("lab" in c or "laboratory" in c for c in cats):
                labs.append(mapped)
            else:
                other_obs.append(mapped)

    abnormal_labs = [l for l in labs if l["abnormal"]]

    reports = []
    if not isinstance(report_b, Exception):
        for r in get_entries(report_b):
            reports.append({
                "name": code_text(r.get("code")),
                "status": r.get("status", "unknown"),
                "category": code_text((r.get("category") or [None])[0]),
                "date": fmt_date(r.get("effectiveDateTime") or (r.get("effectivePeriod") or {}).get("start")),
                "conclusion": r.get("conclusion"),
                "presented_form_title": ((r.get("presentedForm") or [{}])[0]).get("title"),
            })

    imaging = []
    if not isinstance(imaging_b, Exception):
        for s in get_entries(imaging_b):
            first_series = (s.get("series") or [{}])[0]
            imaging.append({
                "description": s.get("description", "No description"),
                "modality": code_text(first_series.get("modality")),
                "body_site": code_text(first_series.get("bodySite")),
                "started": fmt_date(s.get("started")),
                "number_of_series": s.get("numberOfSeries", 0),
                "number_of_instances": s.get("numberOfInstances", 0),
                "status": s.get("status", "unknown"),
            })

    immunizations = []
    if not isinstance(immun_b, Exception):
        for i in get_entries(immun_b):
            proto = (i.get("protocolApplied") or [{}])[0]
            immunizations.append({
                "vaccine": code_text(i.get("vaccineCode")),
                "status": i.get("status", "unknown"),
                "date": fmt_date(i.get("occurrenceDateTime") or i.get("occurrenceString")),
                "dose_number": proto.get("doseNumberPositiveInt") or proto.get("doseNumberString"),
                "lot_number": i.get("lotNumber"),
                "manufacturer": (i.get("manufacturer") or {}).get("display"),
                "site": code_text(i.get("site")),
                "route": code_text(i.get("route")),
            })

    return json.dumps({
        "tool": "get_diagnostics",
        "counts": {
            "total_observations": len(vitals) + len(labs) + len(other_obs),
            "vitals": len(vitals),
            "labs": len(labs),
            "abnormal_labs": len(abnormal_labs),
            "diagnostic_reports": len(reports),
            "imaging_studies": len(imaging),
            "immunizations": len(immunizations),
        },
        "recent_vitals": vitals[:20],
        "recent_labs": labs[:30],
        "abnormal_labs_flagged": abnormal_labs,
        "diagnostic_reports": reports[:10],
        "imaging_studies": imaging,
        "immunizations": immunizations,
    }, indent=2)
