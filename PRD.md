# Penjaga — Product Requirements Document

> **Penjaga** (Malay for *guardian*) is a preventive AI companion for the home. It knows the house's blueprint, reads its meters, finds problems before they cause damage, shows exactly where they are inside the walls on a 3D model of the home, and helps the owner act on them.
>
> **One line:** *"Alexa listens to your family. Penjaga listens to your house."*

---

## 0. How to read this document (for the coding agent)

This PRD tells you **what** we are building, **why**, and **what must never be compromised**. It deliberately does **not** dictate every **how**. You are expected to think, research and "cook".

### 0.1 Three levels of instruction

Every requirement in this document belongs to one of three levels:

| Level | Marker | Meaning |
|---|---|---|
| **Invariant** | 🔒 | Must hold. Do not change without the team's explicit approval. These protect the product's integrity, privacy story, safety and credibility. |
| **Default** | 🧭 | Our current best plan. Follow it unless you find a better approach. If you deviate, record why (see 0.3). |
| **Open** | 🎨 | Your call. Explore, experiment, and pick what produces the best result. |

If a requirement has no marker, treat it as a 🧭 Default.

### 0.2 Intent beats instruction

When a detail in this PRD conflicts with the **intent** behind it, follow the intent. Every major section explains its purpose so you can make good calls when this document is silent or wrong.

### 0.3 Cook dynamically — and look for better signals

While building, actively watch for signals that a better approach exists: a library that solves the problem more cleanly, a model that evaluates better, a UX pattern that tests better, a data source that is more realistic, a simpler architecture with fewer failure points.

When you see such a signal:

1. **Dig in.** Research it properly (official docs, papers, benchmarks, small spikes/prototypes).
2. **Measure if you can.** Prefer evidence (an evaluation run, a benchmark, a working spike) over opinion.
3. **Decide.** If it improves the product without breaking an 🔒 Invariant, adopt it.
4. **Record it.** Add a short entry to `docs/DECISIONS.md` (date, decision, what changed, why, evidence, alternatives considered). Keep it brief — one paragraph is fine.

Deviating from a 🧭 Default with good reason is **encouraged**, not tolerated.

### 0.4 Third-party technology rule 🔒

For **every** third-party library, framework, model, API or service:

- **Always consult the latest official, validated source** (official documentation, official GitHub repository README/changelog, official model card) **before** writing code against it. Do not rely on memory of APIs — syntax and methods change.
- Check the **current stable version**, its **breaking changes/migration notes**, and **deprecations**.
- **Pin versions** in lockfiles/requirements and note the version you validated against in `docs/DEPENDENCIES.md` (name, version, official doc URL, date checked, licence).
- If official docs and this PRD disagree on syntax or method, **the official docs win** for *how*; this PRD wins for *what/why*.
- Check **licences** for every dataset and model you use and record them. Prefer permissive licences. Flag anything restrictive to the team.

### 0.5 Quality over speed 🔒

There is no timeline in this document on purpose. Do not cut corners, fake functionality, or lower quality to "finish faster". A smaller thing done excellently beats a larger thing done poorly. If scope must shrink, shrink breadth, never quality.

---

## 1. Context

### 1.1 The hackathon

- **Event:** Sime Darby Property (SDP) University Hackathon 2026 — *"Township of Tomorrow"*.
- **Organiser:** Sime Darby Property, one of Malaysia's largest township developers (28 active townships and integrated developments; ~11,400 acres of landbank; malls such as KL East Mall, Elmina Lakeside Mall, KLGCC Mall).
- **Our track:** **PEOPLE** — *"How might we enable every resident to live healthier, safer, more connected and more fulfilling lives?"*
- **People subthemes we address:** Safety & Security (primary), Digital Living (primary), Community Wellbeing, Neighbourhood Connection (via management integration).
- **Team:** 3 university students.

### 1.2 How we will be judged

| Criterion | Weight | What judges ask | What it means for the build |
|---|---|---|---|
| Impact & Sustainability Value | 25% | Can it create meaningful, measurable outcomes? | Every alert quantifies impact (RM, litres, risk). Evaluation metrics are real. |
| Innovation & Differentiation | 20% | Genuine originality vs repackaging? | Preventive + blueprint-aware + 3D "see inside the wall" + privacy-first. Not "another smart assistant". |
| Feasibility & Deliverability | 20% | Can it realistically be piloted in a township? | Uses off-the-shelf sensor types and data SDP already owns. Working end-to-end system. |
| Strategic Fit & Theme Relevance | 15% | Clearly a People challenge and an SDP priority? | Built around a real SDP house type and SDP's handover moment. |
| Commercial & Value Multiplication | 10% | Value for SDP, businesses, residents; can it scale? | SDP bundles it with homes; vetted-vendor commission; construction-quality insight. |
| Team, Evidence & Pitch Quality | 10% | Researched, validated, credible, clearly communicated? | Honest "real vs simulated" framing; evaluation report; model cards. |

### 1.3 What must be submitted (context only)

The submission requires a pitch deck, a ~5-minute demo video, and a **link to the prototype / codebase (GitHub)**. Therefore the repository itself is judged: it must be clean, well-documented and runnable.

> ⚠️ **IP note:** The hackathon terms state that submissions become the intellectual property of the organiser. Do not add third-party code or assets whose licence forbids this, and keep secrets out of the repo.

---

## 2. Problem

Every home is a black box. Owners discover problems only **after** damage is done — a stained ceiling, a burnt socket, a dead aircon, a shocking bill.

- **~60% of house fires in Malaysia are linked to electrical issues** — old or faulty wiring, overloaded sockets from high-power appliances (aircon, water heaters, freezers), bypassed safety devices (Fire and Rescue Department via NST, Aug 2025). Fire losses were estimated at **RM1.6 billion in 2023**.
- **Hidden water leaks** (running cisterns, dripping taps, faulty roof-tank float valves, leaking pipes inside walls) run silently for months, wasting water and money and damaging ceilings and walls.
- **Appliances fail without warning.** An aircon with a clogged filter quietly draws more power for weeks before it breaks.
- **Maintenance is forgotten.** Gas hoses, water heaters, safety-switch tests, filters — nobody tracks install dates.
- **Owners don't know their own house.** They don't know where pipes and wires run inside the walls, which makes every repair slower, costlier and riskier.
- **Information from management doesn't reach the house.** A scheduled water disruption can look exactly like a plumbing problem.

**Opening question for the product story:** *"You know your car's full service history. Do you know which wall in your house has a water pipe inside it?"*

---

## 3. Product vision

### 3.1 What Penjaga is

A **single, seamless AI companion for the home** — one agent the owner talks to and hears from — that:

1. **Knows the house** — its blueprint, rooms, pipes, circuits, appliances and their install dates (from SDP's handover record).
2. **Listens to the house** — water meter, water pressure, electrical distribution board — never to the people.
3. **Notices problems early** — using specialist AI models plus hard safety limits.
4. **Shows the problem** — the 3D home zooms from the whole house to the room, the wall fades away layer by layer, and the faulty pipe or circuit is highlighted.
5. **Explains it in plain English** — what is happening, how sure it is, what it costs to wait.
6. **Helps act on it** — book a vetted tradesperson, notify management, follow a guided check — always with the owner's confirmation.
7. **Is connected to the township's management** — it receives notices (e.g. scheduled water disruption) and uses them to avoid false alarms and keep the owner informed; it sends tickets to management when needed.

### 3.2 Architectural truth behind the seamless experience 🔒

To the user, Penjaga is **one companion**. Internally:

> **The agent is the brain. Specialist models are its senses. Safety limits are its reflexes.**

- **Specialist models** read the sensor streams and detect patterns (language models are poor at reading thousands of raw numbers; specialist time-series models are built for it).
- **The agent** (a language model with tool calling) orchestrates the models, **connects clues across signals**, reasons over the house record and management notices, explains, decides what to show in 3D, and takes actions.
- **Safety limits** (hard electrical thresholds) are **always on** and **never overridable** by any AI component.

### 3.3 What Penjaga is not 🔒

- Not a voice assistant for music, timers or general trivia (those are commodities; out of scope).
- Not a camera or microphone surveillance system. The core senses the **building**, not the **people**.
- Not a hackathon "demo toy". The user app must be what a real homeowner would use.

---

## 4. Users

| Persona | Description | What they need from Penjaga |
|---|---|---|
| **Primary: The homeowner household** — e.g. *Aiman & Mei Ling*, a working couple in their 30s; first home in Elmina; both out ~8am–7pm; one young child; Aiman's mother lives in the ground-floor suite. | Busy, not technical, cares about safety and money. Has never seen the house's wiring or plumbing drawings. | Calm by default; tells them only what matters; specific, trustworthy alerts; one-tap actions; understandable costs. |
| **Secondary: Township / building management staff** | Office team handling residents' tickets and posting notices (water disruptions, planned outages, works). | A simple portal to post notices and receive clear, pre-filled tickets with evidence and location. |
| **Tertiary: Vetted tradesperson (tukang/plumber/electrician)** | Receives bookings. *(Mocked in this build.)* | Precise problem description and location → fewer wasted visits. |
| **Strategic: Sime Darby Property** | Developer; owns handover data and the brand promise. | A product that makes SDP homes safer and better-maintained; future insight into recurring construction defects. |

---

## 5. Scope

### 5.1 The house 🔒 (subject to floor-plan confirmation)

We model **one real SDP house type**: a **double-storey intermediate terrace in Elmina Green Six, City of Elmina, Shah Alam** (Sime Darby Property).

Publicly listed characteristics (verify against SDP brochure/floor plan when available):
- Land ~22 ft × 80 ft (~1,760 sq ft); built-up ~2,170 sq ft; freehold; completed ~2024.
- 4 bedrooms, 4 bathrooms; layouts may include a **ground-floor "Flexi-Suite"** with wheelchair-friendly access.

> 🧭 The team will try to obtain the official floor plan (SDP brochure / sales gallery / listings). Until then, model a **representative layout** consistent with the above (see §9.2). The UI and docs must describe it as *"Representative of SDP Elmina Green Six (double-storey terrace)"* until verified. **Do not use SDP's logo or imitate SDP's official branding** — Penjaga is our product designed for SDP homes, not an official SDP app.

### 5.2 Demo scale 🔒

**One house.** All five layers operate on this one house. (Neighbourhood-wide pooling is a roadmap item, not in this build.)

### 5.3 The five preventive layers (in scope)

| # | Layer | Signal | What it catches early | Action |
|---|---|---|---|---|
| **L1** | **Night-Flow Leak Hunter** | Water meter (flow) | Flow during the quiet night window → hidden leak, running cistern, dripping tap, roof-tank float-valve fault, pipe leak in wall | Guided check to narrow down location → water lost and RM/month → book plumber |
| **L2** | **Electrical Fire Prevention** | Distribution-board monitor (main + per-circuit current, residual current, protective-device trips) | Overloaded circuits, rising earth leakage, repeated safety-switch trips | Show the risky circuit in 3D → safety guidance → book electrician |
| **L3** | **Appliance Health Check** | Same electrical data + outdoor weather | Aircon drawing more than expected for the weather (e.g. +20%), fridge running nearly nonstop | "Service now: ~RM90 vs replace later: ~RM1,800" → book servicing |
| **L4** | **Maintenance Schedule** | Install dates and documents from the handover record + manuals | Ageing water heater, gas hose due for replacement, safety-switch test due, aircon service due, filter due | Reminder before failure → schedule or book |
| **L5** | **Management Integration** | Notices from the township/building management portal | Scheduled water disruptions, planned power outages, works affecting the house | Auto-inform the owner; **correlate with sensor data to suppress false alarms** ("pressure dropped — matches scheduled works, not a leak"); send tickets to management |

### 5.4 Out of scope (this build)

- Camera/CCTV features, voice-assistant commodity features (music, timers, trivia).
- Real hardware installation. *(All sensor data is simulated — see §8.)*
- Multiple houses / neighbourhood pooling.
- Real payments, real vendor dispatch, real utility-company integrations (all mocked, clearly).
- Languages other than English.

### 5.5 Nice-to-have (only if core quality is excellent)

- **Voice** (push-to-talk speech in/out for "Ask Penjaga").
- **Appliance disaggregation model (NILM)** for richer L2/L3 insights.
- **Handover onboarding flow** (claim your home with a handover code; walkthrough of your 3D home; choose privacy layers).
- **Virtual device firmware** in Wokwi (ESP32) publishing simulated readings, to show the pilot path.

---

## 6. Product principles

1. **Calm by default.** Silence means the house is fine. Penjaga speaks only when it matters, and its tone matches the severity.
2. **Specific or silent.** Never "something might be wrong". Always *what, where, how sure, what it costs, what to do*.
3. **Show, don't tell.** The 3D home is not decoration — it answers *"where is it?"* instantly.
4. **Evidence on request.** Plain-English explanation first; the numbers behind it are one tap away.
5. **Owner in control.** Penjaga proposes; the owner confirms. No external action without confirmation.
6. **Honest about uncertainty and limits.** Confidence levels are real. Penjaga says when it is not sure.
7. **Privacy is a feature, not a footnote.** (See §7.)
8. **Local and relatable.** Ringgit, litres, Malaysian tariffs, Malaysian homes. Translate numbers into meaning ("~180 litres a day — about a full bathtub").

---

## 7. Privacy & trust 🔒

1. **Senses the building, not the people.** The core uses only meters and building-system sensors: water flow/pressure and electrical measurements. **No cameras. No always-on microphones.** (Optional voice is strictly push-to-talk.)
2. **In-home processing (production design).** In production, detection runs on an in-home hub; only alerts and summaries leave the home. **In this prototype, processing runs in the cloud** — the product, README and pitch must state this openly. Architect the code so detection *could* run on an edge device (no hard dependency on cloud-only services inside the detection path).
3. **Layer-by-layer consent.** Each layer can be switched on/off by the owner in Settings, and switching off actually stops processing for that layer.
4. **Owner owns the data.** Nothing goes to management, vendors or utilities unless the owner confirms the action.
5. **Data minimisation.** Send the language model only what it needs (derived features, detection results, house-record excerpts) — **never raw high-frequency sensor streams, never personal identifiers beyond what's needed**. Prefer OpenRouter provider settings that avoid data retention/training where available (verify current options in official docs).
6. **Malaysia PDPA mindset.** Design consistent with Malaysia's Personal Data Protection Act principles (consent, purpose limitation, security, retention, access). No real personal data in this build; seed data uses fictional people.

---
## 8. Data reality: simulated, but never fake 🔒

We have **no physical sensors**. All sensor readings are **simulated**. This is acceptable and will be stated openly. What is **not** acceptable is anything that *looks* intelligent but is actually scripted.

### 8.1 The core integrity rule 🔒

```
[House Simulator] ──readings──▶ [Detection: specialist models + safety floor] ──findings──▶ [Agent] ──▶ [User App + 3D]
        ▲
        │ injects faults (ground truth stays here)
[Penjaga Lab: fault injection + clock]
```

- The **simulator** produces realistic readings from a model of the household and the house.
- **Faults** are injected into the simulator via the **Penjaga Lab** (operator tool).
- The **detection layer only sees the readings** — exactly what real meters would provide. It has **no access** to fault labels, injection events, or simulator internals at inference time. Enforce this structurally (separate services/modules, no shared state, labels written only to training/evaluation stores).
- **Nothing in the agent, detection or UI may be hardcoded to a specific scenario.** No "if demo then show bathroom 2". Every alert, explanation, cost and 3D focus must be derived from data at runtime.
- Randomness (timing, magnitude, noise, household behaviour) is on by default; a **seed** option exists for reproducibility.

### 8.2 Grounding the simulation in reality 🧭

Every simulated number should be traceable to a real source. Record sources in `docs/DATA_SOURCES.md`.

| Domain | Calibrate against (verify availability, format, licence) |
|---|---|
| Household water end-uses (toilet, shower, taps, washing machine) | **STREaM** (Stochastic Residential water End-use Model, open source); **AMPds2** (real home dataset incl. water); Malaysian per-capita consumption published by SPAN / Air Selangor |
| Appliance electrical behaviour | **UK-DALE**, **REFIT** (appliance-level household electricity datasets); **PLAID** (appliance signatures); manufacturer spec sheets for Malaysian-market aircons (1.0/1.5/2.0 hp), instant water heaters, fridges |
| Weather (drives aircon load) | **Open-Meteo** historical weather API for Shah Alam / Elmina coordinates |
| Electricity cost | **TNB** official domestic tariff (incl. Time-of-Use option, effective July 2025 structure) — fetch current official figures, don't trust memory |
| Water cost | **Air Selangor** official domestic tariff — fetch current official figures |
| Electrical limits | Malaysian practice: 230 V single phase, BS 1363 13 A sockets, MCB ratings per circuit, RCD/RCCB residual-current ratings — verify against Suruhanjaya Tenaga (Energy Commission) guidance / MS IEC 60364 references |
| Management notices | Real **past** public notices from Air Selangor (scheduled water disruptions) and TNB (planned outages), adapted to our fictional street |

Malaysian context the simulator must respect (these also stress-test detection):
- **Aircon runs overnight** in bedrooms — high night-time electricity is normal.
- **Roof water tanks** with float valves are common in landed homes: meter flow often occurs as the tank **refills after** fixture use, smoothing the signal; a faulty float valve causes continuous overflow.
- **Early-morning household routines** (e.g. pre-dawn ablutions/prayers, early school runs) create legitimate water use around 5–6am. The night-flow detector must learn the household's own quiet window rather than assume a fixed one.
- Washing machines on delay timers, guests staying over, filling buckets at night — **legitimate night use must not become false alarms**.

### 8.3 Real data the team may add (optional, high value)

- **Manual night-flow tests:** households photograph their water meter dial before bed and after waking (no water used). Results can be logged as real-world evidence in `docs/FIELD_EVIDENCE.md`.
- **Anonymised myTNB usage history** from team households to sanity-check simulated electricity profiles.

---

## 9. System architecture

### 9.1 Overview 🧭

```
┌────────────────────────────────────────────────────────────────────────────┐
│                               PENJAGA APP (web)                            │
│  Home · Cases · Ask Penjaga · My Home (3D + record) · Notices · Settings   │
└───────────────▲───────────────────────────────▲───────────────────────────┘
                │ REST + realtime (WS/SSE)       │ agent chat (streaming)
┌───────────────┴───────────────────────────────┴───────────────────────────┐
│                               CORE API (FastAPI)                           │
│  house record · cases · actions · notices · auth · realtime fan-out        │
│                                                                            │
│   ┌─────────────────────┐   tools   ┌──────────────────────────────────┐   │
│   │   PENJAGA AGENT     │◀─────────▶│  Detection services              │   │
│   │ (LLM via OpenRouter)│           │  • L1 leak model                 │   │
│   │ orchestrate · link  │           │  • L2 safety floor (+ risk model)│   │
│   │ clues · explain ·   │           │  • L3 health forecaster          │   │
│   │ focus 3D · act      │           │  • (NILM, optional)              │   │
│   └─────────┬───────────┘           └───────────────▲──────────────────┘   │
│             │ knowledge (house record, manuals,      │ readings             │
│             │ notices, tariffs, vendor directory)    │                      │
└─────────────┼────────────────────────────────────────┼──────────────────────┘
              │                                        │
   ┌──────────┴──────────┐                  ┌──────────┴──────────┐
   │ MANAGEMENT PORTAL   │                  │ HOUSE SIMULATOR     │
   │ (separate web app)  │                  │ + sim clock         │
   │ notices · tickets   │                  └──────────▲──────────┘
   └─────────────────────┘                             │
                                            ┌──────────┴──────────┐
                                            │ PENJAGA LAB         │
                                            │ fault injection,    │
                                            │ clock, evaluation   │
                                            └─────────────────────┘
```

### 9.2 Modules

| ID | Module | Purpose |
|---|---|---|
| **M0** | House Data Contract | Single source of truth for house structure, IDs, event and message schemas. |
| **M1** | 3D Home | Model of the house with named parts; focus/zoom/reveal behaviour. |
| **M2** | House Simulator | Realistic readings for the house and household. |
| **M3** | Penjaga Lab | Operator tool: fault injection, simulation clock, scenario control, evaluation view. |
| **M4** | Data Stream & Clock | Moves readings to detection/storage/UI in near real time; owns "house time". |
| **M5** | Detection Services | L1 leak model, L2 safety floor (+ optional risk model), L3 health forecaster, optional NILM. |
| **M6** | Knowledge Base | House record, handover documents, appliance manuals, tariffs, vendor directory — searchable by the agent. |
| **M7** | Management Portal | Separate web app for management staff: post notices, receive and update tickets. |
| **M8** | Penjaga Agent | Orchestrator brain (LLM + tools): cases, explanations, 3D focus, chat, actions. |
| **M9** | Action Layer | Vendor booking (mock), management tickets (real within system), utility reports (mock). |
| **M10** | Penjaga App | The homeowner's web app (desktop + mobile). |
| **M11** | Evaluation Harness | Measures detection quality against baselines; produces reports and model cards. |

---

## 10. M0 — House Data Contract 🔒 (structure) / 🎨 (exact contents)

**Purpose:** every module speaks the same language. The 3D model, simulator, detectors, agent and UI all refer to the same IDs. If this drifts, everything breaks.

### 10.1 Requirements

- 🔒 One canonical definition in the repo (recommended: **JSON Schema** files + a canonical `house.json` instance), with **generated types** for TypeScript and Python (e.g. Pydantic). Do not hand-maintain parallel definitions.
- 🔒 Every physical element that can be referenced (room, wall, pipe segment, fixture, valve, meter, circuit, socket group, appliance, protective device, tank) has a **stable, unique, human-readable ID**.
- 🔒 The 3D model's node/mesh names **match these IDs exactly**. A validation script checks that every contract ID that should be visible exists in the 3D file and vice versa (warn on extras).
- 🔒 The agent may only reference IDs that exist in the contract; the backend **validates** every ID the agent emits.
- 🎨 Exact naming convention, granularity and extra metadata are yours to refine.

### 10.2 Suggested ID convention 🧭

`<kind>_<location>_<descriptor>` — lowercase, snake_case. Examples:
`room_bath_master`, `fix_bath2_wc_cistern`, `pipe_bath2_cold_supply`, `valve_roof_tank_float`, `tank_roof_main`, `meter_water_main`, `db_main`, `circ_ac_master`, `circ_sockets_kitchen`, `rcd_main`, `app_ac_master`, `app_fridge_kitchen`, `app_wh_bath_master`, `app_lpg_hose_kitchen`.

### 10.3 Representative house model (until official floor plan is obtained) 🎨

**Ground floor:** car porch & front garden (outdoor tap, outdoor socket); living; dining; dry kitchen; wet kitchen; yard/utility (washing machine point, laundry tap); **Flexi-Suite** (bedroom 4, wheelchair-friendly) with en-suite bathroom; staircase; distribution board location (commonly near the entrance or kitchen — choose and document).

**First floor:** master bedroom + master bathroom; bedroom 2; bedroom 3; family hall; bathroom 2 and bathroom 3 (layout per floor plan).

**Roof/ceiling space:** roof water tank with float valve and overflow pipe; rising main and down-feed pipes.

**Water system (typical Malaysian landed home — verify and refine):** water main → `meter_water_main` (at front boundary) → kitchen sink often fed directly from main (drinking water) → rising main to `tank_roof_main` (float valve) → gravity down-feed to bathrooms and other fixtures. Each bathroom: WC cistern, basin tap, shower, instant water heater. Kitchen and wet kitchen sinks; washing machine tap; outdoor taps.

**Electrical system (verify and refine):** single-phase 230 V supply → `db_main` with main switch, residual-current protection, and MCB-protected circuits, e.g.: lighting (GF, 1F), socket circuits (living/dining, kitchen, wet kitchen/yard, 1F), dedicated circuits for each aircon, each water heater, and any high-load kitchen appliance. Each circuit has a rating (A) in the contract.

**Appliances (starting assumptions — calibrate from spec sheets):** aircon per bedroom + living (1.0–2.0 hp), instant water heater per bathroom (~3.6 kW class), fridge, possibly chest freezer, washing machine, rice cooker, kettle, microwave, iron, TV, router, LPG cylinder + hose for gas hob. Each with **install date** (from handover ≈ 2024) and manual/spec reference where available.

### 10.4 Core schemas (indicative — refine freely, keep the intent) 🧭

```jsonc
// Reading — one measurement from one stream
{ "ts": "2026-10-16T03:12:40+08:00",   // house time (see M4)
  "house_id": "elmina_g6_demo",
  "stream": "water.main.flow_lpm",       // see stream catalogue below
  "value": 0.42, "unit": "L/min", "quality": "ok" }

// Finding — output of a detection service (no prose, no LLM)
{ "id": "...", "layer": "L1", "type": "running_cistern_suspected",
  "severity": "act_soon", "confidence": 0.91,
  "object_ids": ["fix_bath2_wc_cistern"], "candidates": [{ "object_id": "...", "p": 0.91 }, ...],
  "evidence": { "night_min_flow_lpm": 0.42, "nights_observed": 4, "baseline_lpm": 0.0, "pattern": "periodic_refill" },
  "first_seen": "...", "last_seen": "...", "source": "leak_model@v3" }

// Case — what the user sees; created/updated by the agent from findings + context
{ "id": "...", "status": "open|monitoring|action_pending|resolved|dismissed",
  "title": "...", "summary": "...", "severity": "...", "confidence": 0.91,
  "layers": ["L1"], "finding_ids": ["..."], "object_ids": ["..."],
  "impact": { "litres_per_day": 180, "rm_per_month": 11.2, "risk_note": "..." },
  "recommended_actions": [ ... ], "timeline": [ ... ], "focus": { ... } }

// Focus command — tells ANY 3D implementation what to show
{ "room_id": "room_bath_2", "layer": "water|electrical|structure",
  "object_ids": ["fix_bath2_wc_cistern", "pipe_bath2_cold_supply"],
  "severity": "act_soon", "camera_hint": "optional" }
```

**Severity scale 🧭:** `info` → `heads_up` → `act_soon` → `urgent` (urgent is reserved for safety, e.g. electrical hazard, burst pipe).

**Stream catalogue 🧭 (extend as needed):**
- Water: `water.main.flow_lpm`, `water.main.pressure_bar` (supply pressure at inlet), optional `water.tank.level_pct`.
- Electrical: `elec.main.power_w`, `elec.main.voltage_v`, `elec.circuit.<circ_id>.current_a`, `elec.rcd.residual_ma`, event stream `elec.protection.trip` (device, time).
- Context: `weather.outdoor.temp_c`, `weather.outdoor.rh_pct` (from Open-Meteo historical data aligned to house time).

Sampling rates are 🎨: choose rates that are realistic for commercial meters/monitors and sufficient for the models (e.g. seconds-level for electrical, ~10 s–1 min for water).

---

## 11. M1 — 3D Home

**Purpose:** answer *"where is the problem?"* instantly and memorably, and let owners explore their own home's hidden systems.

### 11.1 Requirements

- 🔒 **Framework-agnostic contract.** The 3D layer consumes the **Focus command** (§10.4) and the **contract IDs**. Any implementation that honours this contract is acceptable.
- 🧭 **Recommended pipeline:** Blender → glTF 2.0 binary (`.glb`) → web renderer (react-three-fiber + drei on three.js; GSAP or the renderer's animation tools for camera/opacity transitions). 🎨 The 3D/frontend owner may choose another tool or renderer (e.g. Spline, Babylon.js, plain three.js) if it serves the experience better — document the choice.
- 🔒 Model contains at least these **layers** that can be shown/hidden independently: **structure** (walls, floors, roof, doors/windows), **water** (pipes, fixtures, valves, tank, meter), **electrical** (DB, circuits/cable runs, sockets, appliances on dedicated circuits).
- 🔒 Node names equal contract IDs (validated by script).

### 11.2 Focus sequence (the signature moment) 🧭

When a case is opened (or the agent decides to show something):
1. Start from the **whole house** (exterior/cutaway).
2. **Glide** to the relevant **room**.
3. **Fade** the relevant wall/floor/ceiling **layer by layer** (finish → structure → reveal services inside).
4. **Highlight** the affected object(s) with a clear, severity-appropriate treatment; secondary candidates shown more subtly.
5. Show a concise **label** anchored to the object (screen-space, readable).

Must be **smooth, interruptible, reversible** (user can orbit, step back, switch layers), and **respect `prefers-reduced-motion`** (cross-fade/cut instead of flying cameras).

### 11.3 Exploration mode 🎨

Owners can explore their home at any time: tap a room, toggle layers ("show me the pipes in the kitchen"), tap an object to see its record (install date, last service, circuit rating). The agent can drive the same view from chat ("where does the water heater in the master bath get its power?").

### 11.4 Performance & resilience 🔒

- Must run smoothly on a mid-range phone and a laptop. Optimise the asset (mesh simplification, texture budget, compression such as Draco/Meshopt/KTX2 where supported — verify current loader support in official docs).
- Lazy-load the 3D; show a meaningful lightweight state while loading.
- **Fallback:** if WebGL is unavailable or fails, show a 2D floor-plan view with the same highlight semantics.

---

## 12. M2 — House Simulator

**Purpose:** generate readings realistic enough that the detection models face real-world difficulty, and that experts would find credible.

### 12.1 Requirements 🧭

- Models the **household** (occupants, weekday/weekend routines, the ground-floor elder's routine, the working couple's absence during the day, guests occasionally), **fixtures and appliances** (from M0), the **roof-tank dynamics**, and **weather-driven aircon load** (Open-Meteo).
- Produces all streams in the catalogue (§10.4) with realistic noise, sensor quantisation and occasional missing data.
- Supports **faults** (§12.2) injected at runtime via M3, with configurable start time, magnitude, progression (constant, intermittent, worsening), and location.
- Supports **non-fault anomalies** (§12.3) to test false-alarm behaviour.
- **Ground truth** (fault labels, start/end times, true location) is written **only** to an evaluation/training store — never visible to detection services at inference time.
- Runs in **real time** or **accelerated time** (driven by M4's clock) and can also **batch-generate** long histories (months) fast for training and evaluation.
- Deterministic with a seed; stochastic without.
- 🎨 Implementation approach is open (physics-inspired, stochastic event models, data-driven replay/augmentation of real datasets, or a mix). Prefer whatever gives the most realistic and defensible data.

### 12.2 Fault catalogue (initial — extend if research suggests better) 🧭

| ID | Layer | Fault | Signature hints (calibrate from sources) |
|---|---|---|---|
| W1 | L1 | Dripping tap | Very low continuous flow (order of 0.02–0.1 L/min) |
| W2 | L1 | Running / leaking WC cistern | Continuous low flow, or **periodic refills** ("phantom flush") every few minutes |
| W3 | L1 | Roof-tank float valve fault (overflow) | Continuous flow with tank-full behaviour; independent of fixture use |
| W4 | L1 | Concealed pipe leak in wall | Continuous flow that may **worsen over days** |
| W5 | L1 | Burst pipe | Sudden large continuous flow → **urgent** |
| E1 | L2 | Sustained circuit overload | Circuit current near/above rating for extended periods (e.g. multiple high-load appliances on one socket circuit) |
| E2 | L2 | Rising earth leakage | Residual current trending upward (e.g. water heater element or outdoor socket degradation; may correlate with wet weather) |
| E3 | L2 | Repeated protective-device trips | Trip events with a pattern (time, circuit, conditions) |
| A1 | L3 | Aircon filter clogging | Power for the same cooling demand drifts upward (~1–3%/week) |
| A2 | L3 | Aircon low refrigerant | Longer runtimes and higher energy for weather-adjusted demand |
| A3 | L3 | Fridge door-seal failure | Compressor duty cycle rises markedly |
| A4 | L3 | Fridge compressor degradation | Abnormal start-up current / cycling behaviour |
| U1 | L5 | Scheduled water disruption (with management notice) | Supply pressure drops to ~0 then recovers; tank level falls |
| U2 | L5 | Unannounced supply drop (no notice) | Same signature, no matching notice |
| U3 | L5 | Planned power outage (with notice) | Mains loss in the notice window |

### 12.3 Legitimate anomalies (must not trigger false alarms) 🔒

Night-time bucket filling, early-morning routines, washing machine on delay timer, guests, heatwave-driven aircon peaks, holiday absence (near-zero use for days), festive cooking surges.

---

## 13. M3 — Penjaga Lab (operator tool)

**Purpose:** let the team (and judges, during demos) control the world the house lives in — without polluting the homeowner product.

🔒 **The Lab is a separate operator surface** (separate app or a clearly separated, access-controlled route). **No Lab controls ever appear in the homeowner app.**

Capabilities 🧭:
- **Fault injection:** pick fault type, location (from contract IDs valid for that fault), magnitude, progression, start (now/at time); randomise option ("surprise me").
- **Clock control:** real-time, accelerated (e.g. 1 s = 10 min), jump-to-time, pause.
- **Scenario presets** (combinations of household behaviour, weather period, faults) — still generated, never scripted outcomes.
- **Live view:** raw streams and detector outputs (for transparency/debugging) — this is the *only* place raw internals are shown in full.
- **Evaluation view:** run/inspect M11 evaluations; show results.
- 🎨 Design: a clean, functional operator console. Tablet-friendly so a judge can tap "inject" themselves.

---

## 14. M4 — Data Stream & House Time

- 🔒 A single **house-time clock** owned by the backend. All services (simulator, detection, agent, app, portal) use house time. In real mode it equals wall time; in accelerated mode the Lab controls it. The homeowner app simply displays house time as "now" (as a real product would).
- 🧭 Realtime transport to the app: WebSockets (FastAPI) or Server-Sent Events — choose based on needs. MQTT is acceptable for the device→backend leg if you want protocol realism (and Wokwi compatibility).
- 🧭 Persist readings in a time-series-friendly store (Postgres via Supabase is the default; TimescaleDB or similar only if justified).
- 🔒 Backpressure/aggregation: at accelerated speeds, aggregate intelligently so the UI and models stay responsive. Never drop safety-relevant events (trips, urgent thresholds).
- Target: a reading that triggers a finding should reach the user's screen in **≤ ~1–2 seconds** of house-time processing (excluding the model's required evidence window).

---

## 15. M5 — Detection Services

**Purpose:** turn raw readings into **findings** (§10.4). Detection is AI-driven, measured, explainable — with a non-negotiable safety floor.

### 15.1 Shared requirements

- 🔒 Detection consumes **only readings and the house contract/record** (plus weather context and, for L5 correlation, notices — via the agent, not inside the detectors).
- 🔒 Every finding includes **confidence**, **evidence** (the key numbers that justify it), **object_ids / candidates**, and **source model + version**.
- 🔒 Each trained model ships with a **model card** in `docs/models/` (purpose, data, training, metrics vs baseline, limitations, licence).
- 🧭 Models are served behind a stable internal API so they can be swapped/upgraded without touching the agent.
- 🔒 **Decision by evaluation, not by hype:** the deployed model for each layer is the one that wins on M11's evaluation against a simple baseline. Keep the baseline in the codebase for comparison.

### 15.2 L1 — Leak model 🧭

- **Default candidate:** **MOMENT** (CMU Auton Lab; MIT licence; Hugging Face `AutonLab/MOMENT-1-small|base|large`; `pip install momentfm`) **fine-tuned** on simulated, labelled water data for leak detection and leak-type classification.
- **Why fine-tuning matters:** an ICLR 2026 study found that several time-series foundation models used *zero-shot* for anomaly detection performed no better than trivial baselines (moving-window variance, squared differences). Off-the-shelf is not enough; train on our domain and **prove** improvement.
- **Outputs:** leak present? type (W1–W5), confidence, likely source candidates (fixture/pipe IDs), estimated flow (L/min), onset time.
- **Source attribution:** combine flow signature (e.g. periodic refill → cistern; tank-full continuous → float valve; worsening → concealed pipe) with the house record (fixture ages, which fixtures feed from tank vs main). 🎨 This can be a second-stage model, feature logic, or agent reasoning over model outputs — choose what evaluates best and is explainable.
- **Personalisation:** learn the household's own quiet window and baseline.
- 🎨 If another approach (e.g. Chronos-2/TimesFM residuals, a purpose-built classifier, gradient boosting on engineered features, a small 1D-CNN/transformer) evaluates better, use it and record the decision.

### 15.3 L2 — Electrical safety floor + risk model

- 🔒 **Safety floor (deterministic, always on, cannot be overridden by any AI):**
  - Circuit current above rating (sustained) → finding (severity scaled by margin and duration).
  - Residual current above warning threshold, or trending toward the protective device's rating → finding.
  - Protective-device trip events → finding; repeated trips → escalate.
  - Thresholds come from the contract (circuit ratings, device ratings) and documented standards — **verify** against official Malaysian guidance; do not invent.
- 🧭 **Risk model (AI):** learns patterns that precede hazards (e.g. overload duration profiles, leakage trends correlated with weather, trip patterns) to warn **earlier** than the floor. It can raise severity, never lower the floor's severity.
- 🔒 Any `urgent` electrical finding includes **safety guidance** (e.g. do not repeatedly reset a tripping device; contact a qualified electrician) — wording reviewed for safety and clarity.

### 15.4 L3 — Appliance health forecaster 🧭

- **Approach:** forecast **expected** consumption of each major appliance (or its dedicated circuit) given context (outdoor temperature/humidity, time of day, occupancy pattern), compare to **actual**, and track the **efficiency gap** over time. A persistent, growing gap = degradation.
- **Default candidates:** **Chronos-2** (Amazon; `amazon/chronos-2` on Hugging Face) or **TimesFM 2.5** (Google Research). Prefer one that supports covariates (weather) — **verify current capabilities in official docs/model cards**.
- Aircons and water heaters are on dedicated circuits (direct measurement). Fridge and other plug-in appliances need **disaggregation** (see 15.5) or a dedicated monitored outlet assumption — document the choice.
- Outputs: appliance ID, efficiency gap (%), trend (%/week), projected time to failure/threshold (with uncertainty), estimated extra cost per month.

### 15.5 Optional — NILM (appliance disaggregation)

- Estimate per-appliance consumption from the main/circuit signal. Default tooling: **torch-nilm** (PyTorch NILM toolkit) or NILMTK-family models trained on **UK-DALE/REFIT**; adapt to Malaysian appliance mixes (aircon-heavy) with synthetic augmentation.
- Use only if it measurably improves L2/L3 or user insight.
- (Research watch: a Sep 2026 arXiv paper proposes a prompt-programmable NILM foundation model — evaluate only if weights/code are actually available and licence permits.)

### 15.6 Training & compute 🧭

- Train on simulator-generated, labelled histories (many simulated months, varied household profiles/weather), plus public datasets where relevant.
- Hold out test sets the models never see; avoid leakage between train/test (split by simulated household/period, not random rows).
- Compute: Kaggle notebooks / Google Colab GPUs or local GPU. Inference must run acceptably on CPU for the deployed demo (verify model sizes/latency).
- Store weights outside git if large (e.g. Hugging Face Hub private repo or release artifacts) with a download script.

---
## 16. M6 — Knowledge Base

**Purpose:** give the agent grounded facts so it never has to guess.

Contents 🧭:
- **House record** (from M0 + handover): rooms, systems, appliances, install dates, warranties, service history, documents.
- **Appliance manuals / spec sheets** (public PDFs for representative Malaysian-market models; record sources and licences).
- **Maintenance rules** (L4): intervals and triggers per item (e.g. aircon servicing, water-heater inspection, LPG hose replacement, residual-current device test-button check, water filter cartridges, roof-tank cleaning). 🔒 **Every interval must cite a source** (manufacturer, Energy Commission, Fire and Rescue Department, gas supplier, water utility). Where sources disagree or none exists, say so and use a conservative, clearly-labelled default.
- **Tariffs:** current official TNB and Air Selangor domestic tariffs, stored as versioned data with source URL and date checked. Used by a deterministic cost calculator (the LLM must not do tariff arithmetic itself).
- **Vendor directory (mock):** categories (plumber, electrician, aircon servicing, pest control, roofer), fictional vendors, ratings, service areas, **price benchmarks** (RM ranges) — clearly labelled as illustrative and grounded in publicly observable market ranges where possible.
- **Management notices** (from M7).

🧭 Retrieval: Postgres + pgvector (Supabase) or equivalent for document search; structured queries for the house record and tariffs. 🎨 Choose chunking/embedding approach after checking current best practice.

---

## 17. M7 — Management Portal

**Purpose:** prove that Penjaga plugs into the township's existing operations, and power L5.

🔒 A **separate web app** (or separately routed, separately authenticated surface) for **management staff**.

Capabilities 🧭:
- **Post notices** in free text and/or structured form (type, affected area/streets, start/end time, description). Free-text notices are parsed by the agent (LLM) into structured events (`water_disruption`, `planned_power_outage`, `works`, `general`) with area and time window — validated before use.
- **Ticket inbox:** receive tickets created from Penjaga (with case summary, location in plain words + object IDs, evidence, photos if provided, owner's contact preference); update status (received → scheduled → in progress → resolved); reply. Status updates flow back to the owner's case timeline in real time.
- Seed with realistic notices adapted from real past Air Selangor/TNB announcements.
- 🎨 Design: professional, efficient back-office UI. Same design language family as Penjaga, but optimised for staff productivity.

---

## 18. M8 — Penjaga Agent

**Purpose:** the single intelligent companion. It turns findings and context into **cases**, explanations, 3D focus, conversation and actions.

### 18.1 Model access 🔒/🧭

- 🔒 LLM access via **OpenRouter API**. API key server-side only.
- 🔒 Model ID is **configuration**, not code (env/config), with a **fallback list**. Only use models that support **tool/function calling** (verify per model on OpenRouter).
- 🧭 Use **structured outputs** (JSON schema) where the chosen model/provider supports it (verify in OpenRouter docs); otherwise validate and repair.
- 🧭 Stream responses to the UI for chat.
- 🧭 Evaluate 2–3 candidate models early for tool-calling reliability, latency, cost and tone; record the choice in `docs/DECISIONS.md`.
- 🔒 Set spending limits on the key; handle rate limits/timeouts gracefully.

### 18.2 Responsibilities

1. **Case management (proactive):** when findings arrive, decide whether to open a new case, update an existing one, merge related findings, or hold (monitoring) until confidence is sufficient. Avoid alert fatigue.
2. **Clue-linking (the core value):** correlate across layers, house record, weather and notices. Examples the system must handle (generated from data, never scripted):
   - Pressure drop + matching management notice → *"Matches scheduled works on your street; not a leak. Your tank has roughly N hours of water."* (Tank estimate computed, not guessed.)
   - Pressure drop + **no** notice → *"Supply pressure dropped. No notice from management — this may be a utility issue. Want me to ask management?"*
   - Rising earth leakage + recent rain + outdoor socket circuit → likely moisture ingress; advise not to reset repeatedly; recommend electrician.
   - Night flow + periodic-refill pattern + 9-year-old cistern in record → *"Likely the Bathroom 2 cistern flush valve — usually a small part, not a pipe repair."*
   - Aircon efficiency gap + heatwave forecast + rising bill → recommend servicing before the heatwave, with cost of waiting.
3. **Explanation:** for every case: what's happening, where, how sure, why it matters, **cost of waiting** (from the deterministic calculator), what to do now, what Penjaga will keep watching.
4. **3D focus:** emit a valid Focus command (IDs validated by the backend).
5. **Conversation ("Ask Penjaga"):** answer questions about the house, its systems, cases, maintenance, costs. Can drive the 3D view and open cases. Politely declines out-of-scope requests (music, trivia) with a brief redirect.
6. **Actions:** propose and, after explicit owner confirmation, execute via M9 (book vendor, create management ticket, schedule a reminder, mark resolved, run a guided check).
7. **Guided checks:** interactive steps to narrow down a problem (e.g. *"Turn off the valve under the Bathroom 2 cistern — tell me when done"* → Penjaga checks whether flow stops in the readings → updates confidence and location).

### 18.3 Tools (indicative — design the final set) 🧭

`get_house_record`, `query_readings` (aggregated/summarised, never raw dumps), `get_findings`, `run_detector` (on demand), `get_weather_context`, `get_notices`, `estimate_cost` (deterministic tariff calculator), `search_knowledge` (manuals/maintenance rules), `focus_3d`, `create_or_update_case`, `propose_action`, `execute_action` (only after confirmation), `create_management_ticket`, `book_vendor` (mock), `schedule_reminder`, `start_guided_check` / `evaluate_guided_check`.

### 18.4 Guardrails 🔒

- Never fabricate numbers. All quantities (flow, cost, litres, percentages, dates) come from tools.
- Never invent object IDs or house features; only use the contract/record.
- Never override or downplay a safety-floor finding.
- Always state uncertainty honestly; distinguish *detected* from *suspected* from *possible*.
- Never perform external actions without explicit confirmation.
- Never give electrical/gas instructions that require a qualified person; for hazards, give safety-first guidance and recommend professionals.
- If the LLM is unavailable, the system still creates findings and shows a clear, templated case (graceful degradation); chat shows a polite unavailable state.

### 18.5 Voice & personality 🧭

Calm, competent, warm, concise. Plain English (Malaysian context — ringgit, local utilities; English only). No hype, no emojis in system messages, no "As an AI…". Matches tone to severity: relaxed for `info`, direct and clear for `urgent`. 🎨 Refine through testing with real people.

### 18.6 Memory 🧭

Persist conversation history, cases, actions, and owner preferences (e.g. quiet hours, preferred vendors, notification preferences). Summarise long histories rather than replaying them.

---

## 19. M9 — Action Layer

| Action | Behaviour in this build |
|---|---|
| **Create management ticket** | 🔒 Real within the system — appears in M7 portal; status syncs back. |
| **Book vendor** | Mock but complete flow: choose vendor (filtered by category, ratings, price benchmark), pick slot, confirm; vendor "acknowledges"; case timeline updates. Clearly labelled as illustrative vendors. |
| **Report to utility** (TNB / Air Selangor) | Mock: generate a pre-filled report summary the owner can copy/send via official channels; never claim it was submitted to the real utility. |
| **Schedule reminder / maintenance** | Real within the system (notifications at the right house time). |
| **Guided check** | Real (uses live readings to verify). |
| **Resolve / dismiss case** | Real; records outcome to improve future behaviour (feedback loop). |

🔒 Every action is logged on the case timeline with time, actor (owner/Penjaga/management), and result.

---

## 20. M10 — Penjaga App (homeowner web app)

### 20.1 Your role for this section

For the frontend, act as an **experienced, award-winning product designer and UI/UX engineer** who has shipped beloved consumer apps in the smart-home, energy and home-services space. **The frontend owner has full creative freedom — this is their playground.** Everything in this section is guidance unless marked 🔒.

### 20.2 Non-negotiables 🔒

- **It is a real product, not a demo.** Every screen must be what an actual homeowner would see and use daily. No demo buttons, no fake controls, no debug panels, no "hackathon" framing in the homeowner app. (Operator controls live in the Lab.)
- **Responsive web app** that is excellent on **phones and desktops** (mobile-first thinking; desktop is a first-class layout, not a stretched phone).
- **Accessible:** target WCAG 2.2 AA (contrast, focus states, keyboard navigation, screen-reader labels, touch targets, reduced motion).
- **Honest data:** every number shown is computed from real system data or a cited source. No placeholder numbers in shipped screens.
- **English only.**
- **Environment indicator:** a subtle, tasteful indicator that the home is running on simulated sensor data (configurable), so the product is honest without looking like a demo.

### 20.3 Visual direction — "not AI-generated" 🎨 (with strong guidance)

**Research first.** Before designing, study real, highly regarded products and design galleries for inspiration — e.g. premium smart-home and energy apps (such as Apple Home, Google Home, Octopus Energy, Tesla's app), calm/editorial interfaces, and curated galleries (Awwwards, Godly, Mobbin, Land-book, Refero, etc.). Extract principles; **do not copy**. Penjaga must have its **own identity**.

**Feel:** calm, trustworthy, domestic, quietly premium. A guardian, not an alarm system. Warmth over tech-coldness.

**Avoid the "AI-generated" look**, including:
- Purple/blue neon gradients, glowing orbs, "sparkle ✨ AI" icons, glassmorphism on everything.
- Generic dashboards of identical cards with identical shadows and random KPI tiles.
- Emoji as icons; stock 3D blobs; meaningless illustrations.
- Centred hero text like "Welcome to your smart home" with a gradient button.
- Default component-library look with no typographic or spatial character.
- Overuse of badges, pills, and colour for decoration rather than meaning.
- Chatbot-first layouts where everything is a chat bubble.

**Do instead:** a deliberate typographic system, generous and intentional spacing, a restrained palette where colour carries **meaning** (e.g. severity, water vs electrical), purposeful motion, real hierarchy, thoughtful empty states, and micro-interactions that make the house feel alive but calm.

### 20.4 Information architecture (suggested — redesign freely) 🎨

| Area | Purpose |
|---|---|
| **Home** | At-a-glance state of the house. Calm "all quiet" state is the default and must feel good. Active cases surface clearly by severity. Today's summary (e.g. water and electricity today vs typical). |
| **Cases** | Each issue is a **case**, not a raw alert: timeline from detection → explanation → actions → resolution. Filter by status/layer. |
| **Case detail** | The signature screen: 3D focus sequence, plain-English summary, confidence, cost of waiting, recommended actions (one primary), "why Penjaga thinks this" (expandable evidence with charts), guided check, timeline, related management notices/tickets. |
| **Ask Penjaga** | Conversational access from anywhere (e.g. persistent entry point/sheet), able to open cases and drive the 3D view. Not the primary navigation model. |
| **My Home** | Explorable 3D home with layer toggles; rooms; systems; appliances with install dates, warranties, service history; maintenance schedule (L4); documents from handover. |
| **Notices** | Management notices relevant to the house; tickets and their status. |
| **Settings & Privacy** | Layer-by-layer on/off with clear explanations of what each layer reads; notification preferences and quiet hours; household members; data controls; connected meters/devices status. |

Possible extra (nice-to-have): **Handover onboarding** — "Welcome to your new home" flow: claim with handover code, a short guided tour of your 3D home, choose layers, set notification preferences.

### 20.5 Key UX behaviours 🧭

- **Severity design:** `info` (quiet, no push), `heads_up` (in-app), `act_soon` (notification), `urgent` (prominent, safety guidance first, primary action obvious).
- **Numbers with meaning:** pair metrics with relatable equivalents and ringgit.
- **Progressive disclosure:** summary → reasoning → raw evidence.
- **Confirmation for actions:** clear, reversible where possible, with what will happen next.
- **Realtime without anxiety:** live updates animate subtly; no flickering numbers.
- **Empty and loading states** are designed, not default spinners.
- **Charts** (water flow, circuit load, efficiency trend) must be legible, honest (sensible axes, units, baselines/thresholds marked), and readable on mobile.

### 20.6 Frontend tech (recommended — owner may change) 🧭

Next.js (React) + TypeScript + Tailwind CSS; react-three-fiber + drei (three.js) for 3D; GSAP or equivalent for motion; Recharts or ECharts (or a custom D3 layer) for charts. Component libraries are allowed only as unstyled primitives or heavily customised — the result must not look like a default kit.

---

## 21. M11 — Evaluation Harness

**Purpose:** prove the AI works and is needed. This directly feeds the *Impact*, *Innovation* and *Evidence* judging criteria.

Requirements 🔒:
- Generates large numbers of evaluation episodes from the simulator (varied households, weather periods, fault types, magnitudes, progressions, and **legitimate anomalies**) with ground truth kept separately.
- Runs the deployed detectors **and** simple baselines (e.g. minimum-night-flow threshold for L1; static thresholds for L3) on the same episodes.
- Reports per layer and per fault type: **detection rate (recall), precision, false alarms per house-month, time-to-detect (median/p90), localisation accuracy** (correct object/room), and **where it fails** (e.g. drips below a certain flow).
- Produces a machine-readable report (JSON) and a readable summary (`docs/EVALUATION.md`), plus charts suitable for the pitch deck.
- Reproducible via seeds and a single command.
- 🔒 Report results honestly, including weaknesses. Never tune on the test set.

---

## 22. Tech stack (defaults) 🧭

| Area | Default | Notes |
|---|---|---|
| Frontend | Next.js + React + TypeScript + Tailwind CSS | Owner may change (§20.6) |
| 3D | Blender → glTF/.glb; react-three-fiber + drei (three.js); GSAP | Framework-agnostic via Focus contract (§11) |
| Charts | Recharts / ECharts / D3 | |
| Backend | Python + FastAPI | Keep ML and API in one language |
| Realtime | WebSockets or SSE (FastAPI); optional MQTT for device leg | |
| Database | Supabase (Postgres + Auth + pgvector) | SQLite acceptable for local dev |
| Simulator | Python (numpy, pandas); Open-Meteo historical API | |
| ML | PyTorch; `momentfm`; Chronos-2 / TimesFM; optional torch-nilm; scikit-learn for baselines | Verify versions/APIs in official repos |
| Training compute | Kaggle / Colab GPU | |
| Agent | OpenRouter API (tool calling); orchestration via native tool-calling loop or LangGraph | Choose the simplest robust option |
| Contract | JSON Schema → generated TS + Pydantic types | |
| Voice (optional) | Browser speech APIs or Whisper-class STT + TTS | Push-to-talk only |
| Virtual device (optional) | Wokwi ESP32 simulator publishing readings | Shows pilot path |
| Hosting | Vercel (web apps); Render / Railway / Hugging Face Spaces / Fly (API + models) | Must also run fully locally with one command |
| Repo | GitHub monorepo | |

---

## 23. Interfaces (indicative) 🧭

Design the final API; these are the capabilities it must cover.

- **House:** get contract/record; get 3D asset URL; get maintenance schedule.
- **Readings:** query aggregated history per stream; realtime subscription.
- **Cases:** list, get, update status, get timeline; realtime updates.
- **Agent:** chat (streaming), with tool results that can trigger UI actions (open case, focus 3D).
- **Actions:** propose/confirm/execute; vendor directory and booking (mock); reminders.
- **Management:** notices CRUD (portal); tickets (create from app, update from portal); realtime sync.
- **Lab:** inject/clear faults, clock control, scenario presets, evaluation runs/results (operator-auth only).
- **Auth:** homeowner account, management account, operator account (simple but real; seed users are fictional).

---

## 24. Non-functional requirements

| Area | Requirement |
|---|---|
| **Reliability** 🔒 | The full flow must work end-to-end repeatedly without manual fixes. Graceful degradation if OpenRouter or a model service is down. |
| **Performance** | App interactive quickly on mid-range phones; 3D smooth; realtime updates within ~1–2 s. |
| **Security** 🔒 | No secrets in the repo or client bundle; `.env.example` provided; role-based access (homeowner / management / operator); input validation on all endpoints; LLM outputs validated before use. |
| **Privacy** 🔒 | As §7. |
| **Observability** | Structured logs; trace a reading → finding → case → action; agent tool calls logged (without sensitive content) for debugging. |
| **Testing** | Unit tests for contract validation, safety floor thresholds, cost calculator, ID validation; integration test for the full fault → case → action flow; evaluation harness as the model test. |
| **Portability** | One command to run everything locally (e.g. Docker Compose or a task runner); documented cloud deployment. Detection path must not depend on cloud-only services (edge-ready). |
| **Documentation** 🔒 | See §25. |

---

## 25. Repository & documentation 🧭 (structure is open; documents are 🔒)

Suggested layout:
```
/apps/web            homeowner app
/apps/portal         management portal
/apps/lab            operator lab
/services/api        FastAPI core (cases, agent, actions, realtime)
/services/sim        house simulator + clock
/services/detect     detection services (L1–L3, safety floor)
/ml                  training, evaluation harness, notebooks, model cards
/packages/contract   JSON Schemas, house.json, generated types, validators
/assets/3d           source .blend and exported .glb
/docs                documentation (below)
```

Required documents 🔒:
- `README.md` — what Penjaga is, architecture diagram, how to run locally, **what is real vs simulated**, screenshots.
- `docs/DECISIONS.md` — decision log (deviations, discoveries, choices).
- `docs/DEPENDENCIES.md` — dependencies with versions, official doc links, licences, date verified.
- `docs/DATA_SOURCES.md` — every dataset/statistic/tariff used, with source, licence, date.
- `docs/EVALUATION.md` — evaluation method and results.
- `docs/models/*.md` — model cards.
- `docs/PRIVACY.md` — privacy design (production vs prototype).

### 25.1 "Real vs simulated" statement (keep accurate as the build evolves) 🔒

| Real | Simulated |
|---|---|
| Detection models, safety floor, agent, 3D, apps, management integration, evaluation | Sensor readings (calibrated on public data) |
| Tariffs, weather, electrical limits, water-use patterns (from cited sources) | Fault events (injected) |
| Field evidence collected by the team (if any) | Vendors and utility submissions (mock) |

---

## 26. Definition of done (product-level)

The build is "done" when, without any scripted shortcuts:

1. A fault injected in the Lab (any type in §12.2, random location/magnitude) is **detected by the models/safety floor from readings alone**, becomes a **case** with correct-or-honestly-uncertain location, accurate **cost of waiting**, and a **3D focus** on the right part of the house.
2. Legitimate anomalies (§12.3) do **not** produce cases (or produce appropriately low-severity, well-explained ones), within the false-alarm rates reported by evaluation.
3. A management notice posted in the portal is understood by Penjaga and **prevents a false leak alarm** during a matching pressure drop; an unannounced drop is handled differently.
4. The owner can **ask Penjaga** about the house and cases, have the 3D view respond, run a **guided check** that uses live readings, **confirm an action**, and see the ticket appear in the **management portal** and its status sync back.
5. L4 maintenance reminders appear at the right house time, each citing its source.
6. The evaluation report shows the deployed models' performance **against baselines**, honestly.
7. The homeowner app looks and feels like a **real, beautifully designed product** on both phone and desktop.
8. Every 🔒 Invariant in this document holds.
9. Documentation in §25 is complete and accurate.

---

## 27. Invariants checklist 🔒 (quick reference)

- [ ] Detection never sees fault labels or simulator internals; nothing is scripted to a scenario.
- [ ] Agent uses only contract IDs; backend validates them.
- [ ] All numbers come from tools/data/cited sources — none invented by the LLM.
- [ ] Safety floor always on; AI can raise but never lower it.
- [ ] No cameras or always-on microphones; layer-level consent works.
- [ ] LLM receives minimised, derived data — never raw streams or unnecessary personal data.
- [ ] No external action without owner confirmation.
- [ ] Homeowner app contains no Lab/demo controls; it is a real product.
- [ ] OpenRouter model is configurable with fallbacks; secrets server-side only.
- [ ] Latest official docs consulted for every third-party dependency; versions pinned and recorded.
- [ ] Prototype-vs-production processing location stated openly.
- [ ] No SDP logo/official branding imitation; house described as representative until verified.
- [ ] Licences recorded for all datasets, models and assets.

---

## 28. Open items (to be resolved by the team; agent should flag, not block)

- Official Elmina Green Six floor plan and as-built services layout (until then: representative model).
- Final choice of OpenRouter model(s) after evaluation.
- Whether voice is included in the first submission.
- Whether NILM is included (based on evaluation value).

---

## 29. Business model (context for product decisions)

- **SDP bundles Penjaga with new homes** as a differentiator ("SDP homes warn you before they break"), funded by the developer as part of the handover experience.
- **Vetted-vendor commission:** Penjaga routes precise, pre-diagnosed jobs to vetted tradespeople; vendors pay a commission per completed booking.
- **Future:** anonymised, aggregated defect patterns help SDP improve construction quality across townships (requires owner consent; roadmap).

---

## 30. Glossary

| Term | Meaning |
|---|---|
| **SDP** | Sime Darby Property, the hackathon organiser and township developer |
| **Elmina Green Six** | An SDP residential phase in City of Elmina, Shah Alam |
| **TNB** | Tenaga Nasional Berhad, Peninsular Malaysia's electricity utility |
| **ToU** | Time-of-Use electricity tariff option |
| **Air Selangor** | Water utility for Selangor |
| **SPAN** | Suruhanjaya Perkhidmatan Air Negara (National Water Services Commission) |
| **Suruhanjaya Tenaga / ST** | Energy Commission of Malaysia |
| **Bomba / JBPM** | Fire and Rescue Department of Malaysia |
| **PDPA** | Personal Data Protection Act (Malaysia) |
| **DB** | Distribution board (fuse box) |
| **MCB** | Miniature circuit breaker (per-circuit protection) |
| **RCD / RCCB / ELCB** | Residual-current protection devices ("safety switch") that trip on earth leakage |
| **NILM** | Non-intrusive load monitoring — estimating appliance usage from a single meter |
| **Tukang** | Malay for tradesperson/handyman |
| **Flexi-Suite** | SDP's ground-floor flexible bedroom suite, wheelchair-friendly |
| **House time** | The system clock used by all services; equals real time unless the Lab accelerates it |
| **Finding / Case** | Finding = raw detection output; Case = user-facing issue managed by the agent |

---

## 31. References (starting points — always verify latest official versions)

**Context & statistics**
- SDP University Hackathon: https://www.simedarbyproperty.com/universityhackathon/
- NST — 60% of house fires caused by electrical issues (Aug 2025): https://www.nst.com.my/amp/news/nation/2025/08/1254638/60pct-house-fires-caused-electrical-issues
- RinggitPlus — TNB Time-of-Use tariff explainer: https://ringgitplus.com/en/blog/personal-finance-news/how-to-lower-your-electricity-bill-with-tnbs-time-of-use-tou-tariff.html (verify against TNB official)
- Elmina Green Six listing (house characteristics): https://www.propertyguru.com.my/property-listing/elmina-green-six-for-sale-by-ka-ee-42286616

**Research**
- ICLR 2026 — "When Foundation Models are One-Liners: Limitations and Future Directions for Time Series Anomaly Detection": https://mlanthology.org/iclr/2026/zhu2026iclr-foundation/
- Strathclyde — Can NILM identify an appliance's anomalous behaviour?: https://strathprints.strath.ac.uk/66609/
- NILM review: https://arxiv.org/pdf/1610.01191

**Official docs / repos to consult before coding** (find the current official pages)
- OpenRouter: https://openrouter.ai/docs
- MOMENT: https://github.com/moment-timeseries-foundation-model/moment
- Chronos (Amazon): https://github.com/amazon-science/chronos-forecasting
- TimesFM (Google Research): https://github.com/google-research/timesfm
- torch-nilm: https://intelligence.csd.auth.gr/publication/torch-nilm-an-effective-deep-learning-toolkit-for-non-intrusive-load-monitoring-in-pytorch/
- Open-Meteo historical weather API: https://open-meteo.com/en/docs/historical-weather-api
- three.js: https://threejs.org/docs/ · react-three-fiber / drei (pmndrs official docs) · GSAP: https://gsap.com/docs/
- Next.js: https://nextjs.org/docs · FastAPI: https://fastapi.tiangolo.com · Supabase: https://supabase.com/docs
- Blender glTF exporter (Blender manual) · Khronos glTF 2.0 specification
- Wokwi: https://docs.wokwi.com
- Datasets: UK-DALE, REFIT, AMPds2, PLAID, STREaM — locate official pages, verify licences.
- Tariffs: TNB official tariff pages; Air Selangor official tariff pages.
- Electrical safety guidance: Suruhanjaya Tenaga official publications.

---

*End of PRD. Build something a homeowner would genuinely trust with their family's safety.*
