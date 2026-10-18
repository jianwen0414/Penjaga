# Penjaga

**Penjaga** (Malay for *guardian*) is a preventive AI companion for the home. It reads the house's meters (water flow and pressure, the electrical distribution board) and never the people. It finds problems before they cause damage, shows where they are inside the walls on a 3D model, and helps the owner act on them. It is built for a representative Sime Darby Property Elmina Green Six double-storey terrace (SDP University Hackathon 2026, PEOPLE track).

> Status: under active development. The product requirements live in [docs/PRD.md](docs/PRD.md).

## What is real and what is simulated

| Real | Simulated |
|---|---|
| Detection models, safety floor, agent, 3D, apps, management integration, evaluation | Sensor readings (calibrated on cited public data) |
| Tariffs, weather, electrical limits, water-use patterns (from cited sources, see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)) | Fault events (injected through the operator Lab) |
| Field evidence collected by the team (if any) | Vendors and utility submissions (mock) |

In this prototype, processing runs in the cloud. In production, detection is designed to run on an in-home hub so that only alerts and summaries leave the home.

## Repository layout

| Path | What | Workstream |
|---|---|---|
| `packages/contract` | **House Data Contract (M0):** JSON Schemas, the canonical house record, the stream catalogue, generated Python and TypeScript types, validators. See [docs/design/contract.md](docs/design/contract.md). | Data + ML |
| `services/stream`, `services/sim`, `services/detect`, `ml/` | Stream and house clock (M4), simulator (M2), detection (M5), evaluation (M11) | Data + ML |
| `services/api` | **Core API:** cases, cost of waiting, AI explanations and chat, actions, notices, maintenance, consent and the disclosure log. See [services/api/README.md](services/api/README.md). | Agent + Integration |
| `apps/portal` | **Management portal:** reports from homes and notices to homes. See [apps/portal/README.md](apps/portal/README.md). | Agent + Integration |
| `src/`, `public/`, `index.html`, `house.spec.json`, `design/` | **Homeowner app and 3D home** (see below) | 3D + Frontend |
| `scripts/check.py` | One-command quality gate for the Python workspace | |

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and Node.js.

```bash
uv sync                                        # creates .venv with Python 3.12 and all dev tools
uv run penjaga-contract validate               # schemas, house record, stream catalogue, reg. 36 checks
uv run python scripts/check.py                 # lint, types, import rules, contract, tests
uv run python packages/contract/tools/generate.py   # regenerate types + streams.json after a contract change
```

The whole stack with the real backend:

```bash
uv run python scripts/dev_stack.py             # stream, simulator, detection, evaluation and the Core API (:8705)
VITE_BACKEND=api npm run dev                   # the homeowner app on the real Core API
cd apps/portal && npm install && npm run dev   # the management portal (:5181)
```

Copy `.env.example` to `.env` for the AI key and to `.env.local` for the app's settings; neither is committed.

## The homeowner app and 3D home

### Run it

```bash
npm install
npm run dev          # http://localhost:5173
```

| URL | What it is |
|---|---|
| `/` | The homeowner app: Penjaga (agent) beside the 3D house |
| `/welcome` | First-run tour of the 3D home |
| `/lab` | **Penjaga Lab** — operator console to inject faults, post management notices, complete jobs (open in a second tab) |
| `/sdp` | Developer-side value view for the pitch (illustrative figures) |

Try it: open `/` and `/lab` side by side → in the Lab, inject a running toilet tank (Bathroom 2) → Penjaga notices it,
walks you through a guided check to find it, books a plumber, and verifies the fix. Then try the earth-leakage case,
a management water-disruption notice, and the living-alone check.

### How it's built

- **3D home (`src/viewer/`)** — plain three.js, generated at runtime from one contract file, `house.spec.json`
  (walls, openings, floors, furniture, pipes, circuits, devices). Every node is named by its contract ID.
  The engine consumes the PRD **Focus command** `{ layer, object_ids, severity, label }`: glide → walls fade in two
  stages → services revealed → affected parts pulse with a label. Also: layer toggles, see-through, floor switch,
  "Can I drill here?". Rendering is on demand with cached ambient occlusion so it stays smooth.
- **App (`src/screens/Agent.tsx`)** — React + TypeScript + Vite. One living screen: the conversation is rebuilt from
  case data, and every reply maps to a real action (guided check, booking, management ticket, dismissal).
- **Stand-in backend (`src/standin/`)** — lets the whole journey run end to end before the other workstreams land:
  `sim.ts` (house simulator, the only place fault ground truth lives) → `detect.ts` (rules on readings only) →
  `agent.ts` (cases, notices, answers). The app only talks to `src/api/client.ts`; swapping in the real API
  (Agent + Integration) and trained models (Data + ML) does not touch the screens.

### Real vs simulated

| Real | Simulated |
|---|---|
| 3D home, focus/reveal behaviour, homeowner app | All sensor readings (water flow, pressure, earth leakage, power) |
| Water tariff maths (Air Selangor domestic tariff, effective 1 Sep 2025) | Faults (injected in the Lab) |
| Detection logic shown is a rule baseline; confidence values are **simulated** until the trained models land | Vendors, bookings and management replies (illustrative) |
| House layout from the developer's brochure floor plan (**representative** of SDP Elmina Green Six; not endorsed by SDP) | Pipe and cable routes (typical practice, not as-built) |

In the product, detection runs on an in-home hub and only alerts leave the house. In this prototype everything runs in
the browser.

## Documentation


- [docs/PRD.md](docs/PRD.md): what we build and why (source of truth)
- [docs/DECISIONS.md](docs/DECISIONS.md): decision log
- [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md): versions, licences, official docs
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md): every number's source
- [docs/design/](docs/design/): contract and interface specifications
- `PRD.md` — product requirements · `CLAUDE.md` — project context
- `design/DECISIONS.md` — 3D and app decision log · `design/ASSETS.md` — 3D asset licences (all CC0, Poly Haven)
- `design/reference/` — the brochure floor plan and reference render the model is built from
