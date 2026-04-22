# server.py
# FHIR Patient Summary MCP Server — Prompt Opinion FHIR Extension
#
# Uses mcp.server.lowlevel.Server so we own the initialize handler
# and can inject "ai.promptopinion/fhir-context" into capabilities
# NATIVELY — not via middleware body-patching.
#
# Pattern from official SDK example:
#   examples/servers/simple-streamablehttp-stateless/
#
# Endpoints:
#   POST /mcp  — MCP (Prompt Opinion connects here)
#   GET  /health
#   GET  /

from __future__ import annotations
import os, json, logging, contextlib
from contextvars import ContextVar
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
import mcp.types as types

from fhir_client import SharpContext, extract_sharp_context
from tools.patient_summary  import get_patient_summary
from tools.clinical_records import get_clinical_records
from tools.medications      import get_medications
from tools.diagnostics      import get_diagnostics
from tools.care_plan        import get_care_plan
from tools.encounters       import get_encounters
from tools.documents        import get_documents

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── SHARP context per-request ──────────────────────────────────────────────────
_sharp_ctx: ContextVar[SharpContext | None] = ContextVar("_sharp_ctx", default=None)


# ── Prompt Opinion FHIR extension payload ──────────────────────────────────────
# Injected into every initialize response.
# Prompt Opinion reads "capabilities.experimental.extensions"
# and looks for the key "ai.promptopinion/fhir-context".
PO_FHIR_EXTENSION = {
    "ai.promptopinion/fhir-context": {
        "scopes": [
            {"name": "patient/Patient.rs",            "required": True},
            {"name": "patient/Condition.rs",          "required": False},
            {"name": "patient/AllergyIntolerance.rs", "required": False},
            {"name": "patient/Procedure.rs",          "required": False},
            {"name": "patient/Observation.rs",        "required": False},
            {"name": "patient/MedicationRequest.rs",  "required": False},
            {"name": "patient/DiagnosticReport.rs",   "required": False},
            {"name": "patient/Immunization.rs",       "required": False},
            {"name": "patient/Encounter.rs",          "required": False},
            {"name": "patient/DocumentReference.rs",  "required": False},
            {"name": "patient/CarePlan.rs",           "required": False},
            {"name": "patient/Goal.rs",               "required": False},
        ]
    }
}

# ── Tool list ──────────────────────────────────────────────────────────────────
TOOLS = [
    types.Tool(
        name="get_patient_summary",
        description=(
            "Returns the patient's demographic and identity information: full name, "
            "DOB, gender, age, address, phone, language, race, ethnicity, and MRN."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_clinical_records",
        description=(
            "Returns the patient's clinical history: active/resolved diagnoses (ICD), "
            "allergies with reactions, procedures, and family history."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_medications",
        description=(
            "Returns all medication data: active prescriptions, historical prescriptions, "
            "reported medications, clinical administrations, and pharmacy dispenses."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_diagnostics",
        description=(
            "Returns diagnostics: recent vitals, lab results with abnormal flags, "
            "diagnostic reports (radiology/pathology), imaging studies, and immunizations."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_care_plan",
        description=(
            "Returns care planning data: care plans, clinical goals with targets, "
            "active referrals, questionnaire responses, and nutrition orders."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_encounters",
        description=(
            "Returns visit history: inpatient stays, outpatient visits, emergency "
            "encounters, episodes of care, and appointments."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_documents",
        description=(
            "Returns the clinical document library: discharge summaries, clinical notes, "
            "referral letters, and compositions with section previews."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]

TOOL_FN = {
    "get_patient_summary":  get_patient_summary,
    "get_clinical_records": get_clinical_records,
    "get_medications":      get_medications,
    "get_diagnostics":      get_diagnostics,
    "get_care_plan":        get_care_plan,
    "get_encounters":       get_encounters,
    "get_documents":        get_documents,
}

# ── Build low-level MCP Server ─────────────────────────────────────────────────
mcp_server = Server("fhir-patient-summary-mcp")


@mcp_server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@mcp_server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    fn = TOOL_FN.get(name)
    if not fn:
        raise ValueError(f"Unknown tool: {name}")

    ctx = _sharp_ctx.get()
    if ctx is None:
        return [types.TextContent(
            type="text",
            text=json.dumps({"error": (
                "SHARP context missing. Prompt Opinion must send "
                "X-FHIR-Server-URL, X-FHIR-Access-Token, X-Patient-ID headers."
            )})
        )]

    try:
        result = await fn(ctx)
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return [types.TextContent(
            type="text", text=json.dumps({"error": str(e)})
        )]


# ── StreamableHTTP session manager ─────────────────────────────────────────────
session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    event_store=None,
    json_response=True,
    stateless=True,
)


# ── ASGI middleware: inject SHARP + extension into initialize ──────────────────
class PromptOpinionMiddleware:
    """
    Two jobs:
    1. Extract SHARP headers (X-FHIR-*) per request → ContextVar
    2. For MCP initialize, patch the JSON response to inject
       capabilities.experimental.extensions["ai.promptopinion/fhir-context"]
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

        # Set SHARP context
        try:
            ctx = extract_sharp_context(headers)
            token = _sharp_ctx.set(ctx)
        except ValueError:
            token = _sharp_ctx.set(None)

        # Detect initialize by buffering the request body
        path = scope.get("path", "").rstrip("/")
        is_init = False

        if path == "/mcp":
            body_chunks: list[bytes] = []
            more = True
            while more:
                msg = await receive()
                body_chunks.append(msg.get("body", b""))
                more = msg.get("more_body", False)
            body = b"".join(body_chunks)

            try:
                payload = json.loads(body)
                # Handle both single request and batch
                if isinstance(payload, list):
                    is_init = any(p.get("method") == "initialize" for p in payload)
                else:
                    is_init = payload.get("method") == "initialize"
            except Exception:
                pass

            replayed = False

            async def replay_receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                # Park — never called again in stateless mode
                import anyio
                await anyio.sleep_forever()

            if is_init:
                # Capture response body
                resp_status = [200]
                resp_headers: list = []
                resp_body = bytearray()

                async def capture(message):
                    if message["type"] == "http.response.start":
                        resp_status[0] = message.get("status", 200)
                        resp_headers.extend(message.get("headers", []))
                    elif message["type"] == "http.response.body":
                        resp_body.extend(message.get("body", b""))

                await self.app(scope, replay_receive, capture)

                # Patch capabilities.experimental.extensions
                patched = self._inject_extension(bytes(resp_body))

                # Rebuild headers with correct content-length
                new_headers = [
                    (k, v) for k, v in resp_headers
                    if k.lower() not in (b"content-length",)
                ]
                new_headers.append((b"content-length", str(len(patched)).encode()))

                await send({
                    "type": "http.response.start",
                    "status": resp_status[0],
                    "headers": new_headers,
                })
                await send({
                    "type": "http.response.body",
                    "body": patched,
                    "more_body": False,
                })
            else:
                await self.app(scope, replay_receive, send)
        else:
            await self.app(scope, receive, send)

        _sharp_ctx.reset(token)

    @staticmethod
    def _inject_extension(body: bytes) -> bytes:
        """Inject PO FHIR extension into initialize response."""
        try:
            data = json.loads(body)
        except Exception:
            return body

        def patch(obj: dict) -> None:
            result = obj.setdefault("result", {})
            caps = result.setdefault("capabilities", {})
            exp = caps.setdefault("experimental", {})
            extensions = exp.setdefault("extensions", {})
            extensions.update(PO_FHIR_EXTENSION)

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "result" in item:
                    patch(item)
        elif isinstance(data, dict) and "result" in data:
            patch(data)

        return json.dumps(data).encode()


# ── Extra HTTP routes ──────────────────────────────────────────────────────────
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")


async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "server": "fhir-patient-summary-mcp",
        "version": "1.0.0",
        "mcp_endpoint": "/mcp",
        "po_fhir_extension": "ai.promptopinion/fhir-context ✓",
        "tools": [t.name for t in TOOLS],
    })


async def docs_page(request: Request) -> Response:
    index = os.path.join(PUBLIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"message": "FHIR MCP Server", "mcp": "/mcp"})


async def handle_mcp(request: Request) -> None:
    """Delegate to StreamableHTTPSessionManager."""
    await session_manager.handle_request(
        request.scope, request._receive, request._send
    )


# ── Starlette app ──────────────────────────────────────────────────────────────
@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    async with session_manager.run():
        logger.info("FHIR MCP Server ready — MCP endpoint at /mcp")
        yield


routes = [
    Route("/health", endpoint=health, methods=["GET"]),
    Route("/",       endpoint=docs_page, methods=["GET"]),
    Route("/mcp",    endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
]
if os.path.isdir(PUBLIC_DIR):
    routes.append(Mount("/public", StaticFiles(directory=PUBLIC_DIR)))

_starlette = Starlette(routes=routes, lifespan=lifespan)
_starlette.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["mcp-session-id"],
)

# Wrap with Prompt Opinion middleware (SHARP + extension injection)
app = PromptOpinionMiddleware(_starlette)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
