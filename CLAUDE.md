# Penjaga — project context (SDP University Hackathon 2026, PEOPLE track)

## The pitch
**Penjaga ("guardian"): the house that warns you first.** "Alexa listens to your family. Penjaga listens to your house."
A preventive AI home agent that serves the RESIDENT (not management). It knows the house's blueprint (every pipe and wire, by name),
reads meters/sensors, and when something is wrong it: shows the house in 3D → flies the camera into the room → turns the wall
see-through → highlights the exact pipe/circuit → explains the cost of waiting → offers the fix (licensed tradesperson, management ticket, DIY step).

### Scope for the submission (keep it narrow)
- HERO layer: **Electrical fire prevention** (~60% of Malaysian house fires are electrical — NST, Aug 2025).
  Note: one clamp at the DB only sees whole-home load (appliance health/overload). Pinpointing a circuit needs per-circuit sensors → pitch as upgrade.
- SECOND layer: **Night-flow leak hunter** (water flow 1am–5am = hidden leak). Needs inline flow sensor (install during construction).
- One slide: appliance health + maintenance schedule. Roadmap only: street early warning, perimeter sensors.
- Small add: "no water/electricity use by 10am → alert family" for seniors living alone (18.8%, NHMS 2025 via NST Apr 2026).
- Angle for SDP: built into NEW SDP homes at construction (cheap, walls open), 3D model generated from SDP floor plans at handover → Home Passport (verified home health history, helps resale).
- Privacy: senses the building, not people. No cameras/mics. Owner owns the data.

### Key dates
Registration closes 21 Oct · Proposal (deck ≤10 slides + 5-min video + prototype link) due 23 Oct 11:59pm · Top 10 on 28 Oct.

## Tech decision
- **Three.js = the product.** Generate the house procedurally at runtime from ONE JSON spec (`house.spec.json`): walls, rooms, openings, later pipes/circuits.
- **Blender = pitch visuals only** (bird's-eye hero render, fly-through video). Blender MCP is configured in the CLI (official Blender Lab extension).
- AI agent may only return parts that exist in the spec: `{room, layer, object_id, severity, fix}`.

## Naming rules (must match between spec, Three.js and Blender)
- Walls: `wall_<GF|FF>_<room>_<N|S|E|W>` (N = rear, S = front). One object per wall segment. Never merge walls.
- Floors: `floor_<GF|FF>_<room>`. Openings: `door_/window_/glass_<...>`.
- MEP (to add): `pipe_<floor>_<room>_<hot|cold|waste>_<n>`, `circuit_<room>_<13A|20A|lighting>`, `db_main`.
- Every object carries custom props: penjaga_id, floor, room, layer (structure|furniture|plumbing|electrical), kind.

## House: SDP double-storey terrace, type B (from the brochure 2D plan)
Units metres, Z up. Origin = LEFT end of GF FRONT wall line. +X left→right (from street), +Y front→rear.
Width X 0–6.1, depth Y 0–13.56. Floor-to-floor 3.3. Walls: external/party 0.20, internal 0.12. GF walls 3.15 high, FF 3.0.
Coordinates are wall CENTRELINES (walls extended by t/2 at each end so corners close).

### Ground floor (Z=0)
- Car porch X0–6.1, Y −5.5..−0.85 (depth assumed). Terrace Y −0.85..0.
- Front wall Y0: living glass doors X0.5–4.0 (h 2.4); foyer door X4.4–5.3.
- Foyer X4.1–6.1 Y0–1.6 (wall at Y1.6). Living X0–4.1 Y0–4.4. Dining X0–4.1 Y4.4–7.3 (open plan).
- Stair X4.45–6.0, 18 risers, Y1.75 → 7.0, rising to the rear (adjusted from plan so it lands inside the FF void).
- Kitchen X0–3.0 Y7.3–12.1; counter on left wall Y7.7–11.3; rear wall Y12.1 with window X0.2–0.95, door X1.15–1.9 to yard.
- Guest room X3.0–6.1 Y7.3–10.8 + rear strip X4.8–6.1 Y10.8–12.2; door in W wall (X3.0) at Y7.45–8.25; rear window X5.0–5.9.
- Guest bath X2.1–4.8 Y10.8–13.56; doors: S wall X3.6–4.4 (to guest room), W wall Y11.2–11.95 (to kitchen); rear window X3.0–3.9 high.
- Yard X0–2.1 Y12.1–13.56 (boundary walls 2.0 high).

### First floor (Z=3.3)
- Balcony X0.6–3.1 Y−1.0..0 (glass balustrade). Flat roof over porch X0–6.1 Y−5.5..−1.0 with parapet.
- Master X0–4.0 Y0–4.5: front glass door X0.6–3.1; door to corridor in N wall X3.25–3.95.
- Master bath X4.0–6.1 Y0–2.9: door in W wall Y0.35–1.1; front window X4.7–5.6 high.
- Stair void X4.0–6.1 Y2.95–7.0; timber balustrade along X4.0 Y4.5–7.0; landing Y7.0–7.4.
- Family area X0–3.1 Y4.5–7.4. Corridor X3.1–4.0 Y4.5–7.4.
- Bedroom 1 X0–3.05 Y7.4–11.0 + rear strip X0–1.35 to Y12.2; door in S wall X2.2–2.95; rear window X0.15–1.2.
- Bedroom 2 X3.05–6.1 Y7.4–11.0 + rear strip X4.65–6.1 to Y12.2; door X3.15–3.9; rear window X4.85–5.95.
- Bath 1 X1.35–2.8 Y11.0–13.56 (door X1.6–2.4). Bath 2 X3.2–4.65 (door X3.45–4.25). Service duct X2.86–3.14 Y12.6–13.2 (`duct_FF_baths` — vertical pipes run here).

## Current state
- Blender (unsaved scene, save as `house_typeB.blend` here): GF + FF structure built by script — all named walls with openings,
  floors, slabs, stair, balcony, porch roof, void balustrade, glass panes. Helper library stored in Blender text block `penjaga_lib.py`.
  NOT done in Blender: furniture, bird's-eye render, GLB export.
- Three.js app: not started.

## Next steps
1. Write `house.spec.json` from the coordinates above (single source of truth).
2. Vite + three (0.186) app: build walls/floors from the spec, orbit view, room labels.
3. Add MEP layer to the spec: kitchen circuit `circuit_kitchen_13A` from `db_main`; bath pipes incl. `pipe_FF_bath1_cold_1` joint.
4. Alert flow: camera fly-to (GSAP), wall fade, pulse highlighted object, side panel with AI JSON + cost + actions.
5. Later: Blender furniture + bird's-eye render for the deck; demo hardware (~RM150: ESP32, YF-S201, SCT-013/PZEM-004T, BME280).

## Team to-dos (human)
- Register team before 21 Oct. Survey SDP homeowners (tripping breakers, hidden leaks, do you know where pipes run?).
- Verify "RM1.6b fire losses 2023" (Malay Mail reports RM1.57b for 2025). Confirm porch depth and stair direction from the original plan.
