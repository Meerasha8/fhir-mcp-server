# server.py
# FHIR Patient Summary MCP Server — SHARP-compliant
# Prompt Opinion · Agents Assemble Hackathon
#
# KEY FIX: Injects the "ai.promptopinion/fhir-context" extension into
# every MCP initialize response. Prompt Opinion checks for this during
# the handshake to confirm FHIR context is supported.
#
# Endpoints:
#   POST http://host:8000/mcp   ← MCP endpoint
#   GET  http://host:8000/health ← health check
#   GET  http://host:8000/       ← HTML docs page

from __future__ import annotations
import os
import json
from contextvars import ContextVar

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse, Response

from fhir_client import SharpContext, extract_sharp_context
from tools.patient_summary  import get_patient_summary
from tools.clinical_records import get_clinical_records
from tools.medications      import get_medications
from tools.diagnostics      import get_diagnostics
from tools.care_plan        import get_care_plan
from tools.encounters       import get_encounters
from tools.documents        import get_documents

# ── Per-request SHARP context ─────────────────────────────────────────────────
_sharp_ctx: ContextVar[SharpContext | None] = ContextVar("_sharp_ctx", default=None)

def _get_ctx() -> SharpContext:
    ctx = _sharp_ctx.get()
    if ctx is None:
        raise RuntimeError(
            "SHARP context missing — send: "
            "X-FHIR-Server-URL, X-FHIR-Access-Token, X-Patient-ID"
        )
    return ctx


# ── Prompt Opinion FHIR extension middleware ──────────────────────────────────
# Intercepts MCP initialize responses and injects the
# "ai.promptopinion/fhir-context" extension into capabilities.
# This is what makes Prompt Opinion show the FHIR trust dialog.

FHIR_EXTENSION = {
    "ai.promptopinion/fhir-context": {
        "scopes": [
            {"name": "patient/Patient.rs",          "required": True},
            {"name": "patient/Condition.rs",         "required": False},
            {"name": "patient/AllergyIntolerance.rs","required": False},
            {"name": "patient/Procedure.rs",         "required": False},
            {"name": "patient/Observation.rs",       "required": False},
            {"name": "patient/MedicationRequest.rs", "required": False},
            {"name": "patient/MedicationStatement.rs","required": False},
            {"name": "patient/DiagnosticReport.rs",  "required": False},
            {"name": "patient/Immunization.rs",      "required": False},
            {"name": "patient/Encounter.rs",         "required": False},
            {"name": "patient/DocumentReference.rs", "required": False},
            {"name": "patient/CarePlan.rs",          "required": False},
            {"name": "patient/Goal.rs",              "required": False},
        ]
    }
}


class PromptOpinionFhirMiddleware:
    """
    ASGI middleware that:
    1. Extracts SHARP headers (X-FHIR-*) into a ContextVar for tool use.
    2. For MCP initialize requests, patches the JSON response to include
       the ai.promptopinion/fhir-context extension in capabilities.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Decode headers
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

        # Inject SHARP context
        try:
            ctx = extract_sharp_context(headers)
            token = _sharp_ctx.set(ctx)
        except ValueError:
            token = _sharp_ctx.set(None)

        # Check if this is an MCP initialize request (needs extension injection)
        is_mcp_init = False
        path = scope.get("path", "")
        if path.rstrip("/") == "/mcp":
            body_chunks = []

            async def receive_with_body():
                msg = await receive()
                if msg["type"] == "http.request":
                    body_chunks.append(msg.get("body", b""))
                return msg

            # Peek at the body to detect initialize
            first_msg = await receive()
            body = first_msg.get("body", b"")
            if first_msg.get("more_body"):
                while True:
                    chunk = await receive()
                    body += chunk.get("body", b"")
                    if not chunk.get("more_body"):
                        break

            try:
                payload = json.loads(body)
                if payload.get("method") == "initialize":
                    is_mcp_init = True
            except (json.JSONDecodeError, AttributeError):
                pass

            # Replay the body back to the app
            body_sent = False

            async def replay_receive():
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            if is_mcp_init:
                # Capture the response so we can patch it
                response_started = False
                response_headers = []
                response_status = 200
                response_body = b""

                async def capture_send(message):
                    nonlocal response_started, response_headers, response_status, response_body
                    if message["type"] == "http.response.start":
                        response_started = True
                        response_status = message.get("status", 200)
                        response_headers = list(message.get("headers", []))
                    elif message["type"] == "http.response.body":
                        response_body += message.get("body", b"")

                await self.app(scope, replay_receive, capture_send)

                # Patch the response body
                patched_body = self._patch_initialize_response(response_body)

                # Update Content-Length header
                patched_headers = []
                for name, value in response_headers:
                    if name.lower() == b"content-length":
                        patched_headers.append((b"content-length", str(len(patched_body)).encode()))
                    else:
                        patched_headers.append((name, value))

                await send({
                    "type": "http.response.start",
                    "status": response_status,
                    "headers": patched_headers,
                })
                await send({
                    "type": "http.response.body",
                    "body": patched_body,
                    "more_body": False,
                })
            else:
                await self.app(scope, replay_receive, send)
        else:
            await self.app(scope, receive, send)

        _sharp_ctx.reset(token)

    def _patch_initialize_response(self, body: bytes) -> bytes:
        """Inject ai.promptopinion/fhir-context into the capabilities.extensions."""
        try:
            data = json.loads(body)
            result = data.setdefault("result", {})
            capabilities = result.setdefault("capabilities", {})
            extensions = capabilities.setdefault("extensions", {})
            extensions.update(FHIR_EXTENSION)
            return json.dumps(data).encode()
        except Exception:
            return body


# ── FastMCP server ─────────────────────────────────────────────────────────────
mcp = FastMCP(name="FHIR Patient Summary MCP Server")


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
        "fhir_extension": "ai.promptopinion/fhir-context",
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
async def docs(request: Request) -> Response:
    index = os.path.join(PUBLIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({
        "message": "FHIR Patient Summary MCP Server",
        "mcp_endpoint": "/mcp",
        "health": "/health",
    })


# ── Build ASGI app ────────────────────────────────────────────────────────────
_cors = Middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["mcp-session-id"],
)

# FastMCP ASGI app — /mcp endpoint is created automatically
_mcp_asgi = mcp.http_app(stateless_http=True, middleware=[_cors])

# Wrap with our middleware (outermost: handles SHARP + initialize patching)
app = PromptOpinionFhirMiddleware(_mcp_asgi)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
