# Decision log

**2026-10-15 — 3D Home: plain three.js + Vite, house generated from `house.spec.json` (deviates from the PRD's Blender → GLB + react-three-fiber default).**
The house (walls, openings, floors, furniture, services) is built at runtime from one JSON spec whose IDs are the contract IDs,
so the 3D model cannot drift from the contract and a new SDP house type is a new spec, not a new 3D file. Blender is kept for
pitch renders only. Alternatives: Blender-authored GLB (two sources of truth to keep in sync), react-three-fiber (can wrap this
later; not needed for the viewer itself).

**2026-10-15 — Photo-scanned CC0 textures + selective CC0 models; furniture stays procedural.**
Poly Haven's furniture models are mostly classical/vintage and clash with the modern interior, and high-poly assets everywhere
would break the mobile performance requirement (PRD §11.4). Real PBR textures, an HDRI, plants, vases, ceiling fans and aircon
condensers come from Poly Haven (CC0, see `docs/ASSETS.md`); everything else is parametric. Floor tiles / paving / porch roof
stayed procedural at the team's request (scanned tile colours didn't suit the house).

**2026-10-15 — Services layer: representative routes, true sizes in the contract, exaggerated display size.**
Pipe and cable routes follow typical Malaysian terrace practice (main → kitchen sink direct, rising main → roof tank, gravity
down-feed through the bathroom duct; DB in the foyer, dedicated circuits for aircons and water heaters). They are labelled as
representative, not as-built. Pipes store their true diameter; rendering uses a minimum display radius so they stay visible.
Circuit MCB ratings are placeholders to be verified against Suruhanjaya Tenaga guidance (PRD §15.3).

**2026-10-15 — Render on demand.** The viewer only draws when something changes; camera motion uses fast frames without AO,
a full-quality frame follows when it stops. Spinning fans keep full quality at ≤30 fps.

**2026-10-15 — Animated parts don't touch AO; AO is cached between animation frames.**
Fan blades render on layer 1, which the AO camera ignores (spinning blades made the screen-space AO under them flicker).
When only fans or highlights change, the last AO buffer is still exact, so `CachedGTAOPass` re-blends it instead of
recomputing (idle-with-fans went from ~32 to ~51 fps in testing). Camera near/far tightened to 0.25/220 m for depth precision.

**2026-10-15 — Focus sequence (PRD §11.2).** `penjaga.focus({ layer, object_ids, severity, label })`: glide → occluding walls
fade in two stages (finish 55 %, structure 12 %) → service layer revealed → affected objects pulse in the severity colour →
anchored label. Interruptible; a straight cut under `prefers-reduced-motion`. Deep link: `?alert=<id>,<id>&layer=water&severity=act_soon`.
