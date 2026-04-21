# FHIR Patient Summary MCP Server

A **SHARP-compliant** MCP server built in Python (FastAPI) that gives AI agents structured access to a patient's complete FHIR health record through **7 clinical data tools**. Built for the [Agents Assemble hackathon](https://agents-assemble.devpost.com) on Prompt Opinion.

---

## Tools

| # | Tool | FHIR Resources Queried |
|---|------|------------------------|
| 1 | `get_patient_summary` | Patient |
| 2 | `get_clinical_records` | Condition, AllergyIntolerance, Procedure, FamilyMemberHistory |
| 3 | `get_medications` | MedicationRequest, MedicationStatement, MedicationAdministration, MedicationDispense |
| 4 | `get_diagnostics` | Observation, DiagnosticReport, ImagingStudy, Immunization |
| 5 | `get_care_plan` | CarePlan, Goal, ServiceRequest, QuestionnaireResponse, NutritionOrder |
| 6 | `get_encounters` | Encounter, EpisodeOfCare, Appointment |
| 7 | `get_documents` | DocumentReference, Composition |

---

## SHARP Context

When Prompt Opinion calls a tool, it fires three HTTP headers automatically:

| Header | Description |
|--------|-------------|
| `X-FHIR-Server-URL` | Base URL of the FHIR server |
| `X-FHIR-Access-Token` | Patient-scoped bearer token |
| `X-Patient-ID` | FHIR patient resource ID |

Your server reads these and queries the FHIR server. No manual auth needed.

---

## Project Structure

```
fhir-mcp-server/
├── server.py              ← FastAPI + MCP server (entry point)
├── fhir_client.py         ← SHARP header extraction + FHIR fetch utils
├── tools/
│   ├── __init__.py
│   ├── patient_summary.py   ← Tool 1
│   ├── clinical_records.py  ← Tool 2
│   ├── medications.py       ← Tool 3
│   ├── diagnostics.py       ← Tool 4
│   ├── care_plan.py         ← Tool 5
│   ├── encounters.py        ← Tool 6
│   └── documents.py         ← Tool 7
├── public/
│   └── index.html         ← Docs landing page
├── requirements.txt
├── render.yaml
└── README.md
```

---

## Local Development

```bash
# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the server
python server.py
```

Server will start at `http://localhost:8000`

- **MCP endpoint** → `POST http://localhost:8000/mcp`
- **Health check**  → `GET  http://localhost:8000/health`
- **Docs page**     → `GET  http://localhost:8000/`

---

## Deploy to Render — Step by Step

### Step 1 — Push your code to GitHub

```bash
cd fhir-mcp-server

git init
git add .
git commit -m "Initial commit — FHIR MCP Server"
```

Go to [github.com/new](https://github.com/new), create a new **public** repository called `fhir-mcp-server`, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/fhir-mcp-server.git
git branch -M main
git push -u origin main
```

---

### Step 2 — Sign up / log in to Render

Go to [render.com](https://render.com) → **Sign up** (free tier is enough).

---

### Step 3 — Create a new Web Service

1. Click **"New +"** in the top nav → select **"Web Service"**
2. Click **"Connect a repository"** → authorize GitHub → select `fhir-mcp-server`
3. Fill in the settings:

| Field | Value |
|-------|-------|
| **Name** | `fhir-mcp-server` |
| **Region** | Any (pick closest to you) |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python server.py` |
| **Instance Type** | `Free` |

4. Scroll down to **Environment Variables** → click **Add Environment Variable**:

| Key | Value |
|-----|-------|
| `PORT` | `8000` |

5. Click **"Create Web Service"**

---

### Step 4 — Wait for deployment (~2 minutes)

Render will install dependencies and start the server. Watch the logs. You should see:

```
INFO:     Started server process
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Your public URL will be:
```
https://fhir-mcp-server.onrender.com
```

---

### Step 5 — Verify it's working

```bash
# Health check
curl https://fhir-mcp-server.onrender.com/health

# Expected response:
# {
#   "status": "ok",
#   "server": "fhir-patient-summary-mcp",
#   "version": "1.0.0",
#   "tools": ["get_patient_summary", "get_clinical_records", ...]
# }
```

Also open `https://fhir-mcp-server.onrender.com` in your browser to see the docs page.

---

### Step 6 — Register on Prompt Opinion

1. Sign up at [promptopinion.ai](https://www.promptopinion.ai)
2. Go to **Marketplace** → **Publish a Tool**
3. Enter your MCP endpoint:
   ```
   https://fhir-mcp-server.onrender.com/mcp
   ```
4. Select **Anonymous** authentication (context comes via SHARP headers)
5. Set `fhir_context_required: true`
6. Save and publish → submit to the hackathon!

---

## Testing with curl

```bash
# List all tools
curl -X POST https://fhir-mcp-server.onrender.com/mcp \
  -H "Content-Type: application/json" \
  -H "X-FHIR-Server-URL: https://hapi.fhir.org/baseR4" \
  -H "X-FHIR-Access-Token: open" \
  -H "X-Patient-ID: YOUR_PATIENT_ID" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Call a tool
curl -X POST https://fhir-mcp-server.onrender.com/mcp \
  -H "Content-Type: application/json" \
  -H "X-FHIR-Server-URL: https://hapi.fhir.org/baseR4" \
  -H "X-FHIR-Access-Token: open" \
  -H "X-Patient-ID: YOUR_PATIENT_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_patient_summary","arguments":{}}}'
```

---

## Free FHIR Test Servers

For testing without a real EHR:

| Server | Base URL |
|--------|----------|
| HAPI FHIR (public) | `https://hapi.fhir.org/baseR4` |
| Prompt Opinion test | `https://ts.fhir-mcp.promptopinion.ai` |

The Prompt Opinion platform injects real FHIR credentials automatically when deployed.
