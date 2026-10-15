# Penjaga — the house that warns you first

> *"Alexa listens to your family. Penjaga listens to your house."*

Penjaga (Malay for *guardian*) is a preventive AI companion for the home, built for the SDP University Hackathon 2026
(People track). It knows the house's blueprint, listens to its water meter and electrical board, notices problems
early, **shows exactly where they are inside the walls on a 3D model of the home**, and helps the owner act.

This branch (`3d-modeling-v1`) contains the **3D home** and the **homeowner app** (3D + Frontend workstream).

## Run it

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

## How it's built

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

## Real vs simulated

| Real | Simulated |
|---|---|
| 3D home, focus/reveal behaviour, homeowner app | All sensor readings (water flow, pressure, earth leakage, power) |
| Water tariff maths (Air Selangor domestic tariff, effective 1 Sep 2025) | Faults (injected in the Lab) |
| Detection logic shown is a rule baseline; confidence values are **simulated** until the trained models land | Vendors, bookings and management replies (illustrative) |
| House layout from the developer's brochure floor plan (**representative** of SDP Elmina Green Six; not endorsed by SDP) | Pipe and cable routes (typical practice, not as-built) |

In the product, detection runs on an in-home hub and only alerts leave the house. In this prototype everything runs in
the browser.

## Docs

- `PRD.md` — product requirements · `CLAUDE.md` — project context
- `docs/DECISIONS.md` — decision log · `docs/ASSETS.md` — 3D asset licences (all CC0, Poly Haven)
- `docs/reference/` — the brochure floor plan and reference render the model is built from
