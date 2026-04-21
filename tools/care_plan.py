# tools/care_plan.py
from __future__ import annotations
import asyncio, json
from fhir_client import SharpContext, fetch_fhir, get_entries, code_text, fmt_date


async def get_care_plan(ctx: SharpContext) -> str:
    results = await asyncio.gather(
        fetch_fhir(ctx, "CarePlan"),
        fetch_fhir(ctx, "Goal"),
        fetch_fhir(ctx, "ServiceRequest"),
        fetch_fhir(ctx, "QuestionnaireResponse"),
        fetch_fhir(ctx, "NutritionOrder"),
        return_exceptions=True,
    )
    cp_b, goal_b, sr_b, qr_b, nutr_b = results

    care_plans = []
    if not isinstance(cp_b, Exception):
        for c in get_entries(cp_b):
            period = c.get("period") or {}
            care_plans.append({
                "title": c.get("title") or code_text((c.get("category") or [None])[0]) or "Care Plan",
                "status": c.get("status", "unknown"),
                "intent": c.get("intent", "unknown"),
                "period": f"{fmt_date(period.get('start'))} → {fmt_date(period.get('end'))}" if period else None,
                "categories": [code_text(cat) for cat in c.get("category", [])],
                "activities": [
                    {
                        "detail": code_text((act.get("detail") or {}).get("code")),
                        "status": (act.get("detail") or {}).get("status"),
                        "description": (act.get("detail") or {}).get("description"),
                    }
                    for act in c.get("activity", [])
                ],
                "notes": [n.get("text") for n in c.get("note", []) if n.get("text")],
            })

    goals = []
    if not isinstance(goal_b, Exception):
        for g in get_entries(goal_b):
            goals.append({
                "description": code_text(g.get("description")),
                "status": g.get("lifecycleStatus", "unknown"),
                "achievement_status": code_text(g.get("achievementStatus")),
                "priority": code_text(g.get("priority")),
                "start_date": fmt_date(g.get("startDate")),
                "targets": [
                    {
                        "measure": code_text(t.get("measure")),
                        "detail": f"{(t.get('detailQuantity') or {}).get('value', '')} {(t.get('detailQuantity') or {}).get('unit', '')}".strip() or None,
                        "due_date": fmt_date(t.get("dueDate")),
                    }
                    for t in g.get("target", [])
                ],
                "notes": [n.get("text") for n in g.get("note", []) if n.get("text")],
            })

    service_requests = []
    if not isinstance(sr_b, Exception):
        for s in get_entries(sr_b):
            service_requests.append({
                "code": code_text(s.get("code")),
                "status": s.get("status", "unknown"),
                "intent": s.get("intent", "unknown"),
                "priority": s.get("priority"),
                "authored_on": fmt_date(s.get("authoredOn")),
                "requester": (s.get("requester") or {}).get("display", "Unknown"),
                "performer": ((s.get("performer") or [{}])[0]).get("display"),
                "reason": code_text((s.get("reasonCode") or [None])[0]),
                "category": code_text((s.get("category") or [None])[0]),
                "occurrence_date": fmt_date(s.get("occurrenceDateTime")),
            })

    questionnaire_responses = []
    if not isinstance(qr_b, Exception):
        for q in get_entries(qr_b):
            items = q.get("item", [])
            questionnaire_responses.append({
                "questionnaire": q.get("questionnaire", "Unknown"),
                "status": q.get("status", "unknown"),
                "authored": fmt_date(q.get("authored")),
                "item_count": len(items),
                "items": [
                    {
                        "text": i.get("text"),
                        "answer": (
                            (i.get("answer") or [{}])[0].get("valueString")
                            or str((i.get("answer") or [{}])[0].get("valueBoolean", ""))
                            or str((i.get("answer") or [{}])[0].get("valueDecimal", ""))
                            or code_text((i.get("answer") or [{}])[0].get("valueCoding"))
                        ) or None,
                    }
                    for i in items[:5]
                ],
            })

    nutrition_orders = []
    if not isinstance(nutr_b, Exception):
        for n in get_entries(nutr_b):
            od = n.get("oralDiet") or {}
            nutrition_orders.append({
                "status": n.get("status", "unknown"),
                "intent": n.get("intent", "unknown"),
                "date_time": fmt_date(n.get("dateTime")),
                "orderer": (n.get("orderer") or {}).get("display", "Unknown"),
                "oral_diet": {
                    "types": [code_text(t) for t in od.get("type", [])],
                    "textures": [code_text(t.get("modifier")) for t in od.get("texture", [])],
                    "instruction": od.get("instruction"),
                } if od else None,
            })

    active_goals = [g for g in goals if g["status"] == "active"]
    active_sr = [s for s in service_requests if s["status"] == "active"]
    completed_sr = [s for s in service_requests if s["status"] != "active"]

    return json.dumps({
        "tool": "get_care_plan",
        "counts": {
            "care_plans": len(care_plans),
            "goals": len(goals),
            "active_goals": len(active_goals),
            "service_requests": len(service_requests),
            "questionnaire_responses": len(questionnaire_responses),
            "nutrition_orders": len(nutrition_orders),
        },
        "care_plans": care_plans,
        "goals": goals,
        "active_service_requests": active_sr,
        "completed_service_requests": completed_sr,
        "questionnaire_responses": questionnaire_responses,
        "nutrition_orders": nutrition_orders,
    }, indent=2)
