# tools/documents.py
from __future__ import annotations
import asyncio, json, re
from fhir_client import SharpContext, fetch_fhir, get_entries, code_text, fmt_date


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


async def get_documents(ctx: SharpContext) -> str:
    results = await asyncio.gather(
        fetch_fhir(ctx, "DocumentReference", {"_sort": "-date", "_count": "30"}),
        fetch_fhir(ctx, "Composition", {"_sort": "-date", "_count": "20"}),
        return_exceptions=True,
    )
    docref_b, comp_b = results

    doc_refs = []
    if not isinstance(docref_b, Exception):
        for d in get_entries(docref_b):
            context = d.get("context") or {}
            ctx_period = context.get("period") or {}
            doc_refs.append({
                "type": code_text(d.get("type")),
                "category": code_text((d.get("category") or [None])[0]),
                "status": d.get("status", "unknown"),
                "doc_status": d.get("docStatus"),
                "date": fmt_date(d.get("date")),
                "description": d.get("description"),
                "authors": [a.get("display", "Unknown") for a in d.get("author", [])],
                "custodian": (d.get("custodian") or {}).get("display"),
                "context": {
                    "period": f"{fmt_date(ctx_period.get('start'))} → {fmt_date(ctx_period.get('end'))}" if ctx_period else None,
                    "facility_type": code_text(context.get("facilityType")),
                    "practice_setting": code_text(context.get("practiceSetting")),
                } if context else None,
                "content": [
                    {
                        "content_type": (c.get("attachment") or {}).get("contentType"),
                        "title": (c.get("attachment") or {}).get("title"),
                        "url": (c.get("attachment") or {}).get("url"),
                        "format": code_text(c.get("format")),
                    }
                    for c in d.get("content", [])
                ],
            })

    compositions = []
    if not isinstance(comp_b, Exception):
        for c in get_entries(comp_b):
            sections = []
            for s in c.get("section", []):
                raw_text = _strip_html(
                    (s.get("text") or {}).get("div", "")
                )[:300]
                sections.append({
                    "title": s.get("title", "Section"),
                    "code": code_text(s.get("code")),
                    "text_preview": raw_text or None,
                    "entry_count": len(s.get("entry", [])),
                })
            compositions.append({
                "title": c.get("title", "Untitled"),
                "type": code_text(c.get("type")),
                "status": c.get("status", "unknown"),
                "date": fmt_date(c.get("date")),
                "authors": [a.get("display", "Unknown") for a in c.get("author", [])],
                "custodian": (c.get("custodian") or {}).get("display"),
                "confidentiality": c.get("confidentiality"),
                "categories": [code_text(cat) for cat in c.get("category", [])],
                "sections": sections,
            })

    clinical_notes = [
        d for d in doc_refs
        if any(kw in d["type"].lower() for kw in ["note", "summary", "report", "letter"])
    ]
    discharge_docs = [
        d for d in doc_refs
        if any(kw in d["type"].lower() for kw in ["discharge", "summary"])
    ]

    return json.dumps({
        "tool": "get_documents",
        "counts": {
            "total_document_references": len(doc_refs),
            "clinical_notes": len(clinical_notes),
            "discharge_documents": len(discharge_docs),
            "compositions": len(compositions),
        },
        "recent_documents": doc_refs[:10],
        "clinical_notes": clinical_notes[:5],
        "discharge_documents": discharge_docs[:5],
        "compositions": compositions[:5],
    }, indent=2)
