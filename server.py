# server.py
# FHIR Patient Summary MCP Server — SHARP-compliant
# Prompt Opinion · Agents Assemble Hackathon
#
# Uses fastmcp (pip install fastmcp>=2.9.0)
#
# HOW IT WORKS:
#   mcp.run(transport="http") starts uvicorn internally.
#   FastMCP automatically creates the MCP endpoint at /mcp
#   Custom routes /health and / are added via @mcp.custom_route()
#   SHARP headers are read per-request via ASGI middleware → ContextVar
#
# Endpoints:
#   POST http://host:8000/mcp   ← MCP endpoint (Prompt Opinion connects here)
#   GET  http://host:8000/health ← health check
#   GET  http://host:8000/       ← HTML docs page

from __future__ import annotations
import os
from contextvars import ContextVar

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse

from fhir_client import SharpContext, extract_sharp_context
from tools.patient_summary  import get_patient_summary
from tools.clinical_records import get_clinical_records
from tools.medications      import get_medications
from tools.diagnostics      import get_diagnostics
from tools.care_plan        import get_care_plan
from tools.encounters       import get_encounters
from tools.documents        import get_documents

# ── Per-request SHARP context ─────────────────────────────────────────────────
# MCP tool functions cannot receive HTTP request objects.
# We thread the SHARP headers through a ContextVar so tools can call _get_ctx().
_sharp_ctx: ContextVar[SharpContext | None] = ContextVar("_sharp_ctx", default=None)


def _get_ctx() -> SharpContext:
    ctx = _sharp_ctx.get()
    if ctx is None:
        raise RuntimeError(
            "SHARP context missing — send these headers: "
            "X-FHIR-Server-URL, X-FHIR-Access-Token, X-Patient-ID"
        )
    return ctx


# ── ASGI middleware: extract SHARP headers per request ────────────────────────
class SharpContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {
                k.decode("latin-1"): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            try:
                ctx = extract_sharp_context(headers)
                token = _sharp_ctx.set(ctx)
            except ValueError:
                token = _sharp_ctx.set(None)
            try:
                await self.app(scope, receive, send)
            finally:
                _sharp_ctx.reset(token)
        else:
            await self.app(scope, receive, send)


# ── FastMCP server ─────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="FHIR Patient Summary MCP Server",
    # Middleware list passed to http_app internally by mcp.run()
)


# ── 7 Tools ───────────────────────────────────────────────────────────────────

@mcp.tool(description=(
    "Returns a structured summary of the patient's demographic and identity information: "
    "full name, date of birth, gender, age, address, phone, preferred language, race, "
    "ethnicity, marital status, and medical record number (MRN)."
))
async def get_patient_summary_tool() -> str:
    return await get_patient_summary(_get_ctx())


@mcp.tool(description=(
    "Returns the patient's clinical history: active and resolved conditions/diagnoses "
    "(ICD codes), allergies and intolerances with reaction details and severity, "
    "procedures performed, and hereditary family member history."
))
async def get_clinical_records_tool() -> str:
    return await get_clinical_records(_get_ctx())


@mcp.tool(description=(
    "Returns the complete medication picture: active prescriptions, historical "
    "prescriptions, patient-reported medications, clinical administrations, and "
    "pharmacy dispense records."
))
async def get_medications_tool() -> str:
    return await get_medications(_get_ctx())


@mcp.tool(description=(
    "Returns diagnostic data: recent vital signs, lab results with automatic abnormal "
    "value flags, diagnostic reports (radiology, pathology), imaging studies (DICOM "
    "references), and the complete immunization history."
))
async def get_diagnostics_tool() -> str:
    return await get_diagnostics(_get_ctx())


@mcp.tool(description=(
    "Returns forward-looking care data: structured care plans, clinical goals with "
    "measurable targets and due dates, active referrals and service orders, "
    "questionnaire responses (e.g. PHQ-9, GAD-7), and nutrition orders."
))
async def get_care_plan_tool() -> str:
    return await get_care_plan(_get_ctx())


@mcp.tool(description=(
    "Returns the patient's full visit history: inpatient stays, outpatient visits, "
    "emergency encounters, episodes of care, and upcoming or past appointments."
))
async def get_encounters_tool() -> str:
    return await get_encounters(_get_ctx())


@mcp.tool(description=(
    "Returns the patient's clinical document library: discharge summaries, clinical "
    "notes, referral letters, and structured compositions with section-level text "
    "previews (HTML stripped to plain text)."
))
async def get_documents_tool() -> str:
    return await get_documents(_get_ctx())


# ── Custom routes ─────────────────────────────────────────────────────────────
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "server": "fhir-patient-summary-mcp",
        "version": "1.0.0",
        "mcp_endpoint": "/mcp",
        "sharp_headers_required": [
            "X-FHIR-Server-URL",
            "X-FHIR-Access-Token",
            "X-Patient-ID",
        ],
        "tools": [
            "get_patient_summary_tool",
            "get_clinical_records_tool",
            "get_medications_tool",
            "get_diagnostics_tool",
            "get_care_plan_tool",
            "get_encounters_tool",
            "get_documents_tool",
        ],
    })


@mcp.custom_route("/", methods=["GET"])
async def docs(request: Request) -> FileResponse | JSONResponse:
    index = os.path.join(PUBLIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({
        "message": "FHIR Patient Summary MCP Server",
        "mcp_endpoint": "/mcp",
        "health": "/health",
    })


# ── ASGI app (used by uvicorn on Render) ─────────────────────────────────────
# Build the ASGI app with CORS + SHARP middleware.
# Render calls `uvicorn server:app` so we expose `app` at module level.
_cors = Middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["mcp-session-id"],
)

# http_app() creates the ASGI app; mcp endpoint is at /mcp automatically
app = mcp.http_app(
    stateless_http=True,
    middleware=[_cors],
)

# Wrap with our SHARP context middleware (outermost layer)
app = SharpContextMiddleware(app)


# ── Entry point (local dev) ───────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
