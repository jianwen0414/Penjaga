// Penjaga 3D Home engine (PRD M1). Framework-free: the app mounts it into any element and drives it
// through the Focus command and a small API; it reports selections back through events.
// One engine instance is shared by every screen, so the house loads once and the canvas moves between screens.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { OutlinePass } from 'three/addons/postprocessing/OutlinePass.js';
import gsap from 'gsap';
import { buildHouse, setOpacity, setHighlight } from './house.js';
import { loadAssets } from './assets.js';

gsap.ticker.lagSmoothing(0); // keep camera moves on schedule even when frames are slow

// When only fan blades or highlights change, the AO from the last full frame is still exact
// (same camera, same static geometry), so we skip the G-buffer/AO/denoise passes and just re-blend it.
class CachedGTAOPass extends GTAOPass {
  // AO is soft by nature: compute it at half resolution (blended back at full res) to keep orbiting smooth.
  setSize(width, height) {
    super.setSize(Math.ceil(width / 2), Math.ceil(height / 2));
  }

  // See-through walls/glass must not cast contact shadows, or ghost outlines of faded walls stay on the floor.
  _overrideVisibility() {
    super._overrideVisibility();
    const cache = this._visibilityCache;
    this.scene.traverse((o) => {
      if (!o.isMesh || !o.visible) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      if (mats.every((m) => m.transparent && m.opacity < 0.6)) { o.visible = false; cache.push(o); }
    });
  }

  render(renderer, writeBuffer, readBuffer, ...rest) {
    if (!this.reuse) return super.render(renderer, writeBuffer, readBuffer, ...rest);
    const out = this.renderToScreen ? null : writeBuffer;
    this.copyMaterial.uniforms.tDiffuse.value = readBuffer.texture;
    this.copyMaterial.blending = THREE.NoBlending;
    this._renderPass(renderer, this.copyMaterial, out);
    this.blendMaterial.uniforms.intensity.value = this.blendIntensity;
    this.blendMaterial.uniforms.tDiffuse.value = this.pdRenderTarget.texture;
    this._renderPass(renderer, this.blendMaterial, out);
  }
}

export const SEVERITY_COLOR = { info: 0x15324a, heads_up: 0x4a3a08, act_soon: 0x5a2a06, urgent: 0x6a0d0d };
// the bright version of each, for light: the glow around a flagged part and the trace along its run
const GLOW_COLOR = { info: 0x3d8bd6, heads_up: 0xe0a21a, act_soon: 0xf0782a, urgent: 0xe5484d };
const SEV_RANK = { info: 0, heads_up: 1, act_soon: 2, urgent: 3 };
const SEVERITY_LABEL = { info: 'Info', heads_up: 'Heads-up', act_soon: 'Act soon', urgent: 'Urgent' };

// Plain description of a scene element for the app (no three.js objects leak out).
function describe(o) {
  if (!o) return null;
  const d = o.userData;
  return {
    id: d.penjaga_id, kind: d.kind, layer: d.layer, floor: d.floor, room: d.room ?? null,
    label: d.label ?? null, system: d.system ?? null, d_mm: d.d_mm ?? null, rating_a: d.rating_a ?? null,
    circuit_kind: d.circuit_kind ?? null, links: d.links ?? null, meta: d.meta ?? null,
  };
}

export async function createEngine(spec, { onProgress, lite = false } = {}) {
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const listeners = {};
  const emit = (ev, data) => (listeners[ev] || []).forEach((f) => f(data));

  // ---------- renderer / scene ----------
  const renderer = new THREE.WebGLRenderer({ antialias: lite, powerPreference: 'high-performance' });
  const DPR = Math.min(devicePixelRatio, 1.5);
  renderer.setPixelRatio(DPR);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false; // static scene: re-render shadows only when something moves
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 0.92;
  const canvas = renderer.domElement;
  canvas.style.display = 'block';
  canvas.style.touchAction = 'none';

  const labelRenderer = new CSS2DRenderer();
  Object.assign(labelRenderer.domElement.style, { position: 'absolute', inset: '0', pointerEvents: 'none' });

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xece6dd);

  const assets = await loadAssets(onProgress);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = assets.hdr ? pmrem.fromEquirectangular(assets.hdr).texture : pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = assets.hdr ? 0.7 : 0.45;

  // Tight near/far for depth precision (thin parts such as switch plates sit mm from their surfaces).
  const camera = new THREE.PerspectiveCamera(30, 1, 0.25, 220);
  camera.layers.enable(1); // layer 1 = animated parts (fan blades): drawn, but kept out of AO and shadows
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = THREE.MathUtils.degToRad(65); // never lower than 25° above the ground
  controls.minDistance = 2.5;
  controls.maxDistance = 70;
  controls.autoRotateSpeed = 0.5;

  scene.add(new THREE.HemisphereLight(0xfff6ea, 0xcabba6, 0.45));
  const sun = new THREE.DirectionalLight(0xfff1dc, 2.5);
  sun.castShadow = true;
  sun.shadow.mapSize.set(3072, 3072);
  Object.assign(sun.shadow.camera, { near: 1, far: 70 });
  sun.shadow.bias = -0.0003;
  sun.shadow.normalBias = 0.025;
  scene.add(sun, sun.target);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.MeshStandardMaterial({ color: 0xe6dfd4, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.165;
  ground.receiveShadow = true;
  scene.add(ground);

  // ---------- post: MSAA + ground-truth AO + tone mapping ----------
  const rt = new THREE.WebGLRenderTarget(16, 16, { type: THREE.HalfFloatType, samples: 4 });
  const composer = new EffectComposer(renderer, rt);
  composer.setPixelRatio(DPR);
  composer.addPass(new RenderPass(scene, camera));
  // AO sees layer 0 only, so spinning blades don't make the contact shadows under them flicker.
  const aoCamera = camera.clone();
  aoCamera.layers.set(0);
  const gtao = new CachedGTAOPass(scene, aoCamera, 16, 16);
  gtao.updateGtaoMaterial({ radius: 0.45, distanceExponent: 1.6, thickness: 1.2, scale: 1.0, samples: 16 });
  gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 16 });
  gtao.blendIntensity = 0.85;
  if (!lite) composer.addPass(gtao);
  // A pulsing glow around the parts Penjaga points at (flag()), faintly visible through the walls in front of them.
  const glow = new OutlinePass(new THREE.Vector2(16, 16), scene, camera);
  Object.assign(glow, { edgeStrength: 6, edgeGlow: 1, edgeThickness: 2.5, pulsePeriod: reduceMotion ? 0 : 1.6, enabled: false });
  if (!lite) composer.addPass(glow);
  composer.addPass(new OutputPass());

  // ---------- house ----------
  const house = buildHouse(spec);
  scene.add(house.root);
  const walls = house.walls;
  const groundPlanes = [...house.byId.values()].filter((o) => ['floor', 'slab', 'roof'].includes(o.userData.kind));
  // Things that read as walls: structural solids and full-height wardrobes.
  const wallLike = [...house.byId.values()].filter((o) => ['duct', 'column', 'ledge', 'balustrade', 'wardrobe'].includes(o.userData.kind));
  house.rooms.forEach((r) => r.label.removeFromParent()); // room pills are an app concern now

  const bounds = new THREE.Box3(); // visible house, for clamping the orbit target
  const FF_SPLIT = new THREE.Vector3(spec.meta.width + 2.4, 0, -spec.floors.FF.z); // spec coords (Z-up)
  const state = { view: 'split', furniture: true, xray: false, water: false, electrical: false, focus: null, interactive: true, drill: false };

  // render-on-demand flags
  let dirty = true, moving = false, hqDone = false, lastChange = 0, aoValid = false;
  function invalidate(shadows = false) {
    dirty = true;
    if (shadows) { renderer.shadowMap.needsUpdate = true; aoValid = false; }
  }

  function fitShadow() {
    const b = new THREE.Box3();
    for (const fg of Object.values(house.floors)) if (fg.visible) b.expandByObject(fg.userData.layers.structure);
    const c = b.getCenter(new THREE.Vector3());
    sun.target.position.copy(c);
    sun.position.copy(c).add(new THREE.Vector3(-9, 26, 14));
    bounds.copy(b).expandByScalar(1);
    const r = b.getBoundingSphere(new THREE.Sphere()).radius;
    Object.assign(sun.shadow.camera, { left: -r, right: r, top: r, bottom: -r });
    sun.shadow.camera.updateProjectionMatrix();
  }

  function applyVisibility() {
    const show = { GF: state.view !== 'FF', FF: state.view !== 'GF' };
    for (const [k, fg] of Object.entries(house.floors)) {
      fg.visible = show[k];
      fg.userData.layers.furniture.visible = state.furniture;
      fg.userData.layers.water.visible = state.water;
      fg.userData.layers.electrical.visible = state.electrical;
    }
  }

  // ---------- camera ----------
  const UP = new THREE.Vector3(0, 1, 0);
  const ELEV = THREE.MathUtils.degToRad(55);
  const D = (dur) => (reduceMotion ? 0 : dur);
  function flyTo(pos, target, duration = 1.2) {
    gsap.to(camera.position, { x: pos.x, y: pos.y, z: pos.z, duration: D(duration), ease: 'power2.inOut' });
    gsap.to(controls.target, { x: target.x, y: target.y, z: target.z, duration: D(duration), ease: 'power2.inOut' });
  }
  function frame(duration = 1.2, { tight = 0.78 } = {}) {
    const box = new THREE.Box3();
    for (const fg of Object.values(house.floors)) if (fg.visible) box.expandByObject(fg.userData.layers.structure);
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const vfov = THREE.MathUtils.degToRad(camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const dist = (sphere.radius / Math.sin(Math.min(vfov, hfov) / 2)) * tight;
    flyTo(sphere.center.clone().add(new THREE.Vector3(0, Math.sin(ELEV), Math.cos(ELEV)).multiplyScalar(dist)), sphere.center, duration);
  }
  function approach(c, dist, elevDeg, horiz) {
    const e = THREE.MathUtils.degToRad(elevDeg);
    const h = (horiz ?? camera.position.clone().sub(controls.target).setY(0)).normalize();
    if (h.lengthSq() < 1e-6) h.set(0, 0, 1);
    return c.clone().add(h.multiplyScalar(Math.cos(e)).addScaledVector(UP, Math.sin(e)).normalize().multiplyScalar(dist));
  }

  function setView(view, { reframe = true } = {}) {
    state.view = view;
    const ff = house.floors.FF.position;
    const to = view === 'split' ? FF_SPLIT : new THREE.Vector3();
    applyVisibility();
    const from = ff.clone();
    ff.copy(to);
    scene.updateMatrixWorld(true);
    fitShadow();
    if (reframe && !state.focus) frame();
    ff.copy(from);
    gsap.to(ff, { x: to.x, y: to.y, z: to.z, duration: D(0.9), ease: 'power2.inOut', onUpdate: () => invalidate(true), onComplete: () => invalidate(true) });
    emit('state', getState());
  }
  function showFloorOf(floor) {
    if (state.view === 'stack' || state.view === (floor === 'GF' ? 'FF' : 'GF')) setView(floor, { reframe: false });
  }
  function setLayer(name, on) {
    state[name] = on;
    applyVisibility();
    invalidate(true);
    emit('state', getState());
  }

  // ---------- selection + focus ----------
  let selected = null, hovered = null;
  function isVisible(o) { for (let p = o; p; p = p.parent) if (!p.visible) return false; return true; }
  function select(obj, { silent = false } = {}) {
    if (selected && selected !== obj) setHighlight(selected, null);
    selected = obj;
    if (obj) setHighlight(obj, 0x0a1f1a);
    if (!silent) emit('select', describe(obj));
    invalidate();
  }
  function focusRoom(floor, roomId, { duration = 1.4 } = {}) {
    const room = house.rooms.get(`${floor}:${roomId}`);
    if (!room) return false;
    showFloorOf(floor);
    select(null, { silent: true });
    state.focus = { room };
    const c = house.floors[floor].localToWorld(new THREE.Vector3(...room.center));
    c.y += 0.6;
    const dist = THREE.MathUtils.clamp(Math.max(...room.size) * 1.6 + 3, 5.5, 12);
    flyTo(approach(c, dist, 55, new THREE.Vector3(0, 0, 1)), c, duration);
    return true;
  }
  function focusObject(obj, { autoLayer = true, silent = false } = {}) {
    const d = obj.userData;
    if (d.kind === 'floor') return focusRoom(d.floor, d.room);
    showFloorOf(d.floor);
    if (autoLayer && (d.layer === 'water' || d.layer === 'electrical') && !state[d.layer] && !isVisible(obj)) setLayer(d.layer, true);
    scene.updateMatrixWorld(true);
    select(obj, { silent });
    state.focus = { obj };
    const b = new THREE.Box3().setFromObject(obj);
    const c = b.getCenter(new THREE.Vector3());
    const size = b.getSize(new THREE.Vector3());
    // Frame the room the object sits in (a lone fixture from above reads as an abstract blur);
    // fall back to the object's own size for things outside rooms.
    const room = d.room ? house.rooms.get(`${d.floor}:${d.room}`) : null;
    const span = room ? Math.max(...room.size, size.x, size.z) : Math.max(size.x, size.y, size.z);
    const dist = THREE.MathUtils.clamp(span * 1.8 + 6, 9, 15);
    const wall = d.kind === 'wall' ? obj : d.wall ? house.byId.get(d.wall) : null;
    let horiz;
    if (wall) {
      const [[, y1], [, y2]] = [wall.userData.spec.a, wall.userData.spec.b];
      horiz = Math.abs(y2 - y1) < 1e-6 ? new THREE.Vector3(0, 0, -1) : new THREE.Vector3(1, 0, 0);
      const side = wall.userData.outward ? wall.userData.inside : Math.sign(horiz.dot(camera.position.clone().sub(c))) || 1;
      horiz.multiplyScalar(side);
    }
    flyTo(approach(c, dist, wall ? 48 : 52, horiz ?? new THREE.Vector3(0, 0, 1)), c, 1.3);
    return true;
  }
  function focusId(id, opts) { const o = house.byId.get(id); return o ? focusObject(o, opts) : false; }
  // Several objects (e.g. "the bathrooms"): frame them all together; on both floors, show both floors.
  function focusObjects(objs) {
    const floors = [...new Set(objs.map((o) => o.userData.floor).filter(Boolean))];
    let settle = 0;
    if (floors.length > 1 && state.view !== 'split') { setView('split', { reframe: false }); settle = 0.95; }
    else if (floors.length === 1) showFloorOf(floors[0]);
    select(null, { silent: true });
    state.focus = { objs };
    const fly = () => {
      scene.updateMatrixWorld(true);
      const box = new THREE.Box3();
      objs.forEach((o) => box.expandByObject(o));
      const sphere = box.getBoundingSphere(new THREE.Sphere());
      const vfov = THREE.MathUtils.degToRad(camera.fov);
      const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
      const dist = Math.max(9, (sphere.radius / Math.sin(Math.min(vfov, hfov) / 2)) * 1.15);
      flyTo(sphere.center.clone().add(new THREE.Vector3(0, Math.sin(ELEV), Math.cos(ELEV)).multiplyScalar(dist)), sphere.center, 1.3);
    };
    // the first floor slides into place when the view changes: frame once it has arrived
    if (settle && !reduceMotion) gsap.delayedCall(settle, fly); else fly();
    return true;
  }

  // ---------- flagged objects (Focus command) ----------
  let flagged = [], flagLabels = [], revealTl = null;
  function clearFlags() {
    revealTl?.kill();
    clearTraces();
    flagged.forEach((o) => { delete o.userData.flagColor; delete o.userData.flagSev; setHighlight(o, null); });
    flagged = [];
    updateGlow();
    flagLabels.forEach((l) => l.removeFromParent());
    flagLabels = [];
    invalidate();
  }
  function clearFocus({ reframe = true } = {}) {
    clearFlags();
    clearScan();
    clearRoute();
    if (proj) { const p = proj; proj = null; p.objects.forEach((o) => o.removeFromParent()); }
    endInspect();
    state.focus = null;
    select(null);
    if (reframe) frame();
  }
  function flag(ids, severity = 'heads_up', { label, color: override } = {}) {
    const color = override ?? SEVERITY_COLOR[severity] ?? SEVERITY_COLOR.heads_up;
    const objs = ids.map((id) => house.byId.get(id)).filter(Boolean);
    // an override colour (inspection: always red) glows as the severity it stands for
    const sev = Object.keys(SEVERITY_COLOR).find((k) => SEVERITY_COLOR[k] === color) ?? severity;
    objs.forEach((o) => { o.userData.flagColor = color; o.userData.flagSev = sev; setHighlight(o, color); });
    flagged = [...new Set([...flagged, ...objs])];
    updateGlow();
    if (label && objs[0]) {
      flagLabels.forEach((l) => l.removeFromParent());
      // One place: the command's label, once. Several rooms (e.g. "the bathrooms"): each gets its own name.
      const own = (o) => o.userData.label ?? house.rooms.get(`${o.userData.floor}:${o.userData.room}`)?.name ?? label;
      const places = new Set(objs.map((o) => `${o.userData.floor}:${o.userData.room ?? o.userData.penjaga_id}`));
      const many = places.size > 1;
      flagLabels = (many ? objs : objs.slice(0, 1)).map((o, i) => {
        const el = document.createElement('div');
        el.className = `focus-label sev-${severity}`;
        el.innerHTML = `<b>${i === 0 ? SEVERITY_LABEL[severity] ?? '' : ''}</b> ${many ? own(o) : label}`;
        const tag = new CSS2DObject(el);
        const b = new THREE.Box3().setFromObject(o);
        o.worldToLocal(tag.position.set((b.min.x + b.max.x) / 2, b.max.y + 0.15, (b.min.z + b.max.z) / 2));
        o.add(tag);
        return tag;
      });
    }
    invalidate();
  }

  // One glow for everything flagged, in the colour of the most serious of them.
  function updateGlow() {
    glow.selectedObjects = flagged;
    glow.enabled = flagged.length > 0;
    if (!glow.enabled) return;
    const worst = flagged.reduce((a, o) => ((SEV_RANK[o.userData.flagSev] ?? 1) > (SEV_RANK[a] ?? 1) ? o.userData.flagSev : a), 'info');
    glow.visibleEdgeColor.setHex(GLOW_COLOR[worst] ?? GLOW_COLOR.heads_up);
    glow.hiddenEdgeColor.copy(glow.visibleEdgeColor).multiplyScalar(0.45);
  }

  // Focus command (PRD §10.4 / §11.2): { layer, object_ids, severity, label }
  //   1. glide to the first object, 2. peel the walls in the way back in two steps (finish, then structure),
  //   3. reveal the service layer, 4. pulse the affected objects in the severity colour, 5. anchor a label.
  // Interruptible, and a straight cut under prefers-reduced-motion. Unknown IDs are ignored.
  function focus({ layer, object_ids = [], severity = 'heads_up', label } = {}) {
    clearFlags();
    const objs = object_ids.map((id) => house.byId.get(id)).filter(Boolean);
    if (!objs.length) return false;
    const services = layer === 'water' || layer === 'electrical';
    if (services && !reduceMotion) setLayer(layer, false); // start from the finished house, then reveal
    if (objs.length > 1) focusObjects(objs);
    else focusObject(objs[0], { autoLayer: false, silent: true });
    state.focus.reveal = { o: 1 };
    const finish = () => {
      if (services) setLayer(layer, true);
      // a problem on a pipe or circuit: follow it from the meter or the board first, then mark it
      const lead = services && severity !== 'info' ? trace(objs, severity) : 0;
      const mark = () => {
        flag(object_ids, severity, { label: label ?? objs[0].userData.label ?? objs[0].userData.penjaga_id });
        emit('focusDone', { object_ids });
      };
      if (lead) revealTl = gsap.timeline().call(mark, null, lead); else mark();
    };
    if (reduceMotion) { state.focus.reveal.o = 0.1; finish(); return true; }
    revealTl = gsap.timeline({ onUpdate: () => invalidate() })
      .to(state.focus.reveal, { o: 0.55, duration: 0.5, ease: 'power1.out' }, 1.2) // finish layer
      .to(state.focus.reveal, { o: 0.12, duration: 0.5, ease: 'power1.out' }, 2.0) // structure
      .call(finish, null, 2.6); // services + highlight
    return true;
  }

  // ---------- trace: follow the problem along its pipe or circuit, from the source ----------
  // A glowing run grows along the object's own route (spec paths, drawn from the meter or the board outwards),
  // seen through the walls, with a bright head. Lasts until the flags are cleared.
  let traces = [];
  function clearTraces() {
    traces.forEach((t) => { t.tween?.kill(); t.mesh.removeFromParent(); t.mesh.geometry.dispose(); t.head?.removeFromParent(); });
    traces = [];
  }
  function routeOf(obj) {
    if (obj.userData.paths?.length) return obj;
    if (obj.userData.layer !== 'water') return null;
    // a fixture or valve: the pipe in its room whose run ends nearest to it
    const at = new THREE.Box3().setFromObject(obj).getCenter(new THREE.Vector3());
    let best = null, bestD = Infinity;
    for (const o of house.byId.values()) {
      if (o.userData.kind !== 'pipe' || o.userData.floor !== obj.userData.floor || !o.userData.paths?.length) continue;
      if (obj.userData.room && o.userData.room && o.userData.room !== obj.userData.room) continue;
      for (const path of o.userData.paths) {
        const end = o.localToWorld(new THREE.Vector3(...path[path.length - 1]));
        const d = end.distanceTo(at);
        if (d < bestD) { bestD = d; best = o; }
      }
    }
    return bestD < 2.5 ? best : null;
  }
  function trace(objs, severity = 'act_soon', duration = 1.6) {
    clearTraces();
    // only when the problem sits on one run: several candidate circuits are not one place yet
    const hosts = [...new Set(objs.map(routeOf).filter(Boolean))];
    if (hosts.length !== 1) return 0;
    const host = hosts[0];
    const color = GLOW_COLOR[severity] ?? GLOW_COLOR.act_soon;
    const longest = host.userData.paths.reduce((a, p) => (p.length > a.length ? p : a));
    const pts = longest.map((p) => new THREE.Vector3(...p));
    const curve = new THREE.CurvePath();
    for (let i = 0; i < pts.length - 1; i++) if (pts[i].distanceTo(pts[i + 1]) > 1e-4) curve.add(new THREE.LineCurve3(pts[i], pts[i + 1]));
    if (!curve.curves.length) return 0;
    const segs = Math.max(24, Math.round(curve.getLength() * 30));
    const geo = new THREE.TubeGeometry(curve, segs, 0.03, 8, false);
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95, depthTest: false, toneMapped: false });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.renderOrder = 10;
    mesh.layers.set(1); // drawn, but kept out of AO and shadows
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.075, 16, 12), new THREE.MeshBasicMaterial({ color: 0xfff4e0, depthTest: false, toneMapped: false }));
    head.renderOrder = 11;
    head.layers.set(1);
    host.add(mesh, head);
    const total = geo.index.count, perSeg = total / segs;
    const t = { mesh, head, tween: null };
    traces.push(t);
    const show = (k) => {
      geo.setDrawRange(0, Math.max(perSeg, Math.round((k * segs)) * perSeg));
      head.position.copy(curve.getPointAt(Math.min(1, k)));
      head.visible = k < 1;
      invalidate();
    };
    if (reduceMotion) { show(1); return 0; }
    const p = { k: 0 };
    show(0);
    t.tween = gsap.to(p, { k: 1, duration, ease: 'power1.inOut', onUpdate: () => show(p.k) });
    return duration;
  }

  // ---------- "Scan my home": a beam crosses both floors, front to back, and each room gets its result ----------
  // rooms: { 'GF:kitchen': 'urgent' | 'act_soon' | 'heads_up' | 'info', ... } for rooms with something open;
  // every other room reads as fine. Resolves when the sweep has finished.
  let scanRig = null;
  function clearScan() {
    if (!scanRig) return;
    scanRig.tween?.kill();
    scanRig.beams.forEach((b) => b.removeFromParent());
    scanRig.tags.forEach((t) => t.removeFromParent());
    const prev = scanRig.prev;
    scanRig = null;
    state.xray = prev.xray;
    setLayer('water', prev.water);
    setLayer('electrical', prev.electrical);
  }
  function scan(rooms = {}) {
    clearFlags();
    clearScan();
    select(null, { silent: true });
    state.focus = null;
    if (state.view !== 'split') setView('split', { reframe: false });
    frame(1.0);
    const prev = { xray: state.xray, water: state.water, electrical: state.electrical };
    const { width: W, depth: DEPTH } = spec.meta;
    const beams = [], tags = [];
    for (const [key, fg] of Object.entries(house.floors)) {
      const z0 = fg.userData.z;
      const beam = new THREE.Group();
      const sheet = new THREE.Mesh(
        new THREE.PlaneGeometry(W + 0.8, 3.2),
        new THREE.MeshBasicMaterial({ color: 0x2f9d7a, transparent: true, opacity: 0.24, side: THREE.DoubleSide, depthWrite: false, toneMapped: false }),
      );
      sheet.rotation.x = Math.PI / 2; // stand the sheet up across the house (spec is Z-up)
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(sheet.geometry),
        new THREE.LineBasicMaterial({ color: 0x48d6a6, transparent: true, opacity: 0.9, toneMapped: false }),
      );
      edge.rotation.x = Math.PI / 2;
      beam.add(sheet, edge);
      beam.position.set(W / 2, -0.6, z0 + 1.6);
      beam.traverse((m) => { m.layers.set(1); m.renderOrder = 9; });
      fg.userData.layers.labels.add(beam);
      beams.push(beam);
      for (const r of house.rooms.values()) {
        if (r.floor !== key) continue;
        const sev = rooms[`${key}:${r.id}`];
        const el = document.createElement('div');
        el.className = `scan-tag ${sev ? `sev-${sev}` : 'ok'}`;
        el.innerHTML = `<i aria-hidden="true">${sev ? '!' : '✓'}</i>${r.name}`;
        const tag = new CSS2DObject(el);
        tag.position.set(r.center[0], r.center[1], r.center[2] + 1.2);
        tag.visible = false;
        tag.userData.y = r.center[1];
        fg.userData.layers.labels.add(tag);
        tags.push(tag);
      }
    }
    scanRig = { beams, tags, prev, tween: null };
    // the house opens up as the beam starts: walls see-through, pipes and wiring on
    state.xray = true;
    setLayer('water', true);
    setLayer('electrical', true);
    const rig = scanRig;
    const reveal = (y) => rig.tags.forEach((t) => { if (!t.visible && t.userData.y <= y) { t.visible = true; t.element.classList.add('in'); } });
    if (reduceMotion) {
      beams.forEach((b) => (b.visible = false));
      reveal(Infinity);
      invalidate(true);
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const p = { y: -0.6 };
      rig.tween = gsap.timeline({ onUpdate: () => invalidate(), onComplete: resolve })
        .to(p, { y: DEPTH + 0.6, duration: 3.2, ease: 'none', delay: 0.9, onUpdate: () => { beams.forEach((b) => (b.position.y = p.y)); reveal(p.y); } })
        .to(beams.map((b) => b.children).flat().map((m) => m.material), { opacity: 0, duration: 0.35, ease: 'power1.out' })
        .call(() => beams.forEach((b) => (b.visible = false)));
    });
  }
  /** The room an element is in, as `${floor}:${room}`; a circuit counts as the room of what it feeds. */
  function roomOf(id, seen = new Set()) {
    const d = house.byId.get(id)?.userData;
    if (!d || seen.has(id)) return null;
    seen.add(id);
    if (d.room && house.rooms.has(`${d.floor}:${d.room}`)) return `${d.floor}:${d.room}`;
    for (const end of d.ends ?? []) { const r = roomOf(end, seen); if (r) return r; }
    return null;
  }

  // ---------- inspection: walk into the room, eye level, x-ray the wall, mark the fault in red ----------
  // The 3D model is the camera (the house has none): the view stands where a person would, at eye height,
  // looking at the problem; the wall in the way peels back like an x-ray, the part and its run turn red, and a
  // frame is captured. Phases go to `onPhase` so the app can narrate. Ends with clearFocus().
  const EYE = 1.55;
  let insp = null;
  function endInspect() {
    if (!insp) return;
    const prev = insp.prev;
    insp.tl?.kill();
    insp.cancel?.();
    stopRecording();
    insp = null;
    gsap.killTweensOf(camera);
    camera.fov = prev.fov;
    camera.updateProjectionMatrix();
    controls.maxPolarAngle = prev.maxPolar;
    controls.minDistance = prev.minDistance;
    if (state.view !== prev.view) setView(prev.view, { reframe: false });
    if (state.water !== prev.water) setLayer('water', prev.water);
    if (state.electrical !== prev.electrical) setLayer('electrical', prev.electrical);
  }
  /** Where a person would stand to look at `target`: at eye height, a couple of metres away, from the side with a
   * clear line of sight. Walls don't count as in the way (the x-ray peels them back); furniture, doors and cars do.
   * Inside the target's room where possible. Outdoor places (porch, roof) get a low side view. */
  const OUTDOOR_ROOMS = new Set(['porch', 'terrace', 'roof', 'balcony']);
  const SEE_THROUGH = new Set(['wall', 'floor', 'slab', 'roof', 'glass', 'window', 'slider', 'pipe', 'circuit', 'cable']);
  const sight = new THREE.Raycaster();
  function elementOf(o) { while (o && !o.userData.penjaga_id) o = o.parent; return o; }
  function blockers(from, to, target) {
    const dir = to.clone().sub(from);
    sight.set(from, dir.clone().normalize());
    sight.far = dir.length() - 0.25;
    const seen = new Set();
    for (const hit of sight.intersectObjects(Object.values(house.floors).filter((f) => f.visible), true)) {
      if (!isVisible(hit.object)) continue;
      const el = elementOf(hit.object);
      if (!el || el === target || seen.has(el)) continue;
      let inTarget = false;
      for (let q = hit.object; q; q = q.parent) if (q === target) { inTarget = true; break; }
      if (inTarget || SEE_THROUGH.has(el.userData.kind) || el.userData.layer === 'water' || el.userData.layer === 'electrical') continue;
      seen.add(el);
    }
    return seen.size;
  }
  function standpoint(target) {
    const d = target.userData;
    let aim = target;
    if (d.kind === 'circuit') aim = (d.ends ?? []).map((id) => house.byId.get(id)).find(Boolean) ?? target; // look at what it feeds
    const P = new THREE.Box3().setFromObject(aim).getCenter(new THREE.Vector3());
    const key = roomOf(aim.userData.penjaga_id ?? d.penjaga_id);
    const room = key && !OUTDOOR_ROOMS.has(key.split(':')[1]) ? house.rooms.get(key) : null;
    const floor = room?.floor ?? aim.userData.floor ?? d.floor;
    const fg = house.floors[floor];
    const indoor = !!room;
    const floorY = fg ? fg.localToWorld(new THREE.Vector3(0, 0, fg.userData.z)).y : P.y - 1;
    const C = room ? fg.localToWorld(new THREE.Vector3(room.center[0], room.center[1], fg.userData.z)) : null;
    // indoors, look from the room's middle; outdoors, from out in the open, away from the house
    const middle = new THREE.Box3().setFromObject(house.floors.GF).getCenter(new THREE.Vector3());
    const toCentre = C ? C.clone().sub(P).setY(0).normalize() : P.clone().sub(middle).setY(0).normalize();
    let best = null;
    for (const dist of indoor ? [1.5, 1.9, 2.4, 3.2] : [3.4, 4.4]) {
      for (let a = 0; a < 12; a++) {
        const dir = new THREE.Vector3(Math.cos((a / 12) * Math.PI * 2), 0, Math.sin((a / 12) * Math.PI * 2));
        const pos = P.clone().addScaledVector(dir, dist);
        pos.y = indoor ? floorY + EYE : P.y + 1.0;
        let score = blockers(pos, P, aim) * 4 + (indoor ? Math.abs(dist - 2.4) * 0.4 : 0) - 0.6 * dir.dot(toCentre);
        if (room) {
          const l = fg.worldToLocal(pos.clone());
          const [sx, sy] = room.size;
          const inRoom = Math.abs(l.x - room.center[0]) < sx / 2 - 0.25 && Math.abs(l.y - room.center[1]) < sy / 2 - 0.25;
          const inHouse = l.x > 0.2 && l.x < spec.meta.width - 0.2 && l.y > 0.2 && l.y < spec.meta.depth - 0.2;
          score += (inRoom ? 0 : 2.5) + (inHouse ? 0 : 3);
        }
        if (!best || score < best.score) best = { score, pos };
      }
    }
    return { pos: best.pos, look: P, floor, room: room?.name ?? null };
  }
  function inspect({ layer, object_ids = [], severity = 'urgent', label } = {}, { onPhase = () => {}, record = true } = {}) {
    clearFocus({ reframe: false });
    const objs = object_ids.map((id) => house.byId.get(id)).filter(Boolean);
    if (!objs.length) return Promise.resolve(null);
    insp = {
      prev: { fov: camera.fov, maxPolar: controls.maxPolarAngle, minDistance: controls.minDistance, view: state.view, water: state.water, electrical: state.electrical },
      tl: null,
    };
    const run = insp;
    const home = objs[0].userData.floor;
    if (home === 'GF' || home === 'FF') setView(home, { reframe: false });
    // the first floor slides into place over a moment: plan the standpoint where it will be
    const ff = house.floors.FF.position, sliding = ff.clone();
    ff.copy(state.view === 'split' ? FF_SPLIT : new THREE.Vector3());
    scene.updateMatrixWorld(true);
    const at = standpoint(objs[0]);
    ff.copy(sliding);
    scene.updateMatrixWorld(true);
    controls.maxPolarAngle = THREE.MathUtils.degToRad(100); // allow a level, eye-height view
    controls.minDistance = 0.4;
    select(null, { silent: true });
    state.focus = { obj: objs[0], reveal: { o: 1 } };
    if (record) startRecording(label);
    onPhase('walk', { room: at.room });
    const services = layer === 'water' || layer === 'electrical';
    const finish = () => {
      if (services) setLayer(layer, true);
      trace(objs, 'urgent'); // red, whatever the severity: this is the part that's wrong
      flag(object_ids, severity, { label: label ?? objs[0].userData.label ?? objs[0].userData.penjaga_id, color: SEVERITY_COLOR.urgent });
      onPhase('found', {});
    };
    return new Promise((resolve) => {
      run.cancel = () => resolve(null);
      const done = async () => {
        const image = capture();
        const video = await stopRecording();
        if (insp === run) onPhase('done', {});
        resolve({ image, video, room: at.room });
      };
      if (reduceMotion) {
        camera.fov = 50; camera.updateProjectionMatrix();
        camera.position.copy(at.pos); controls.target.copy(at.look);
        state.focus.reveal.o = 0.12;
        finish();
        invalidate(true);
        requestAnimationFrame(() => requestAnimationFrame(() => void done()));
        return;
      }
      gsap.to(camera, { fov: 50, duration: 1.6, ease: 'power2.inOut', onUpdate: () => camera.updateProjectionMatrix() });
      flyTo(at.pos, at.look, 1.7);
      run.tl = gsap.timeline({ onUpdate: () => invalidate() })
        .call(() => onPhase('look', {}), null, 1.8) // the room as it is, for a beat
        .call(() => onPhase('xray', {}), null, 2.5)
        .to(state.focus.reveal, { o: 0.5, duration: 0.45, ease: 'power1.out' }, 2.6)
        .to(state.focus.reveal, { o: 0.1, duration: 0.45, ease: 'power1.out' }, 3.1)
        .call(finish, null, 3.4)
        .call(() => void done(), null, 5.6);
    });
  }

  // ---------- route: the way from where the owner is to where they need to be (an emergency) ----------
  // A glowing path on the floors, drawn from the owner's room outwards, with light pulses running along it so the
  // direction is obvious. It goes through doorways (src/nav/route.ts plans it from the drawings), up or down the
  // stair, and ends at the thing to reach, which glows. "You" and the destination are marked. The walls go
  // see-through so the whole walk is visible. Lasts until clearRoute() or clearFocus().
  let routeRig = null;
  function clearRoute() {
    if (!routeRig) return;
    const rig = routeRig;
    routeRig = null;
    rig.tween?.kill();
    if (rig.flagged) clearFlags();
    rig.objects.forEach((o) => {
      o.removeFromParent();
      o.traverse?.((m) => { m.geometry?.dispose(); if (m.material && !m.material.userData?.shared) m.material.dispose?.(); });
    });
    state.xray = rig.prev.xray;
    invalidate(true);
    emit('state', getState());
  }
  const ROUTE_LIFT = 0.07; // above the floor finish
  function routeCurve(points) {
    // no two points in the same place (a zero-length span breaks the centripetal spline), and at least two
    const pts = points.filter((q, i) => i === 0 || q.distanceTo(points[i - 1]) > 0.01);
    if (pts.length === 1) pts.push(pts[0].clone().add(new THREE.Vector3(0.02, 0, 0)));
    return new THREE.CatmullRomCurve3(pts, false, 'centripetal', 0.5);
  }
  function routeTag(text, cls) {
    const el = document.createElement('div');
    el.className = `route-tag ${cls}`;
    el.innerHTML = `<i aria-hidden="true"></i><span></span>`;
    el.querySelector('span').textContent = text;
    return new CSS2DObject(el);
  }
  function routeMarker(color, radius) {
    const g = new THREE.Group();
    const disc = new THREE.Mesh(new THREE.CircleGeometry(radius, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.32, depthTest: false, toneMapped: false }));
    const ring = new THREE.Mesh(new THREE.RingGeometry(radius * 0.92, radius, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95, depthTest: false, toneMapped: false }));
    const wave = new THREE.Mesh(new THREE.RingGeometry(radius * 0.94, radius, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6, depthTest: false, toneMapped: false }));
    wave.userData.wave = true;
    g.add(disc, ring, wave);
    g.traverse((m) => { m.renderOrder = 12; m.layers.set(1); });
    return g;
  }
  /**
   * Show a route: { legs: [{ floor, points: [[x, y], ...] }], stair: { foot, top, up } | null,
   * from: { room }, to: { id?, room } } in spec metres; opts: { from: 'You · Kitchen', to: 'Main stop valve',
   * color }. Returns true if drawn.
   */
  function route(r, { from: fromText = 'You', to: toText = '', color = GLOW_COLOR.urgent } = {}) {
    clearFocus({ reframe: false });
    clearRoute();
    if (!r?.legs?.length) return false;
    const objects = [];
    const curves = [];
    const prev = { xray: state.xray };
    const floorsUsed = new Set(r.legs.map((l) => l.floor));
    if ([...floorsUsed].some((f) => !house.floors[f]?.visible) || floorsUsed.size > 1) setView(floorsUsed.size > 1 ? 'split' : [...floorsUsed][0], { reframe: false });
    state.xray = true; // the whole walk visible through the walls
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95, depthTest: false, toneMapped: false });
    const halo = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.18, depthTest: false, toneMapped: false });
    const addLine = (parent, curve, segs) => {
      const core = new THREE.Mesh(new THREE.TubeGeometry(curve, segs, 0.05, 10, false), mat);
      const glowTube = new THREE.Mesh(new THREE.TubeGeometry(curve, segs, 0.14, 10, false), halo);
      for (const m of [glowTube, core]) { m.renderOrder = 11; m.layers.set(1); parent.add(m); objects.push(m); }
      curves.push({ curve, core, glowTube, segs, parent });
    };
    for (const leg of r.legs) {
      const fg = house.floors[leg.floor];
      if (!fg) continue;
      const z = fg.userData.z + ROUTE_LIFT;
      const pts = leg.points.map(([x, y]) => new THREE.Vector3(x, y, z));
      // the climb is part of the ground floor's stair: add it to the ground-floor leg's end (or start, going down)
      if (r.stair && leg.floor === 'GF') {
        const [fx, fy, fz] = r.stair.foot, [tx, ty, tz] = r.stair.top;
        const foot = new THREE.Vector3(fx, fy, fz + ROUTE_LIFT + 0.12), top = new THREE.Vector3(tx, ty, tz + ROUTE_LIFT);
        if (r.stair.up) pts.push(foot, top); else pts.unshift(top, foot);
      }
      const curve = routeCurve(pts);
      addLine(fg.userData.layers.labels, curve, Math.max(32, Math.round(curve.getLength() * 26)));
    }
    // markers: where the owner is, and where they need to get to
    const start = r.legs[0], end = r.legs[r.legs.length - 1];
    const place = (leg, [x, y], obj) => { const fg = house.floors[leg.floor]; obj.position.set(x, y, fg.userData.z + ROUTE_LIFT + 0.01); fg.userData.layers.labels.add(obj); objects.push(obj); return obj; };
    const you = place(start, start.points[0], routeMarker(0x2f9d7a, 0.42));
    const goal = place(end, end.points[end.points.length - 1], routeMarker(color, 0.48));
    const youTag = routeTag(fromText, 'you'); youTag.position.set(0, 0, 0.9); you.add(youTag);
    if (toText) { const goalTag = routeTag(toText, 'goal'); goalTag.position.set(0, 0, 1.1); goal.add(goalTag); }
    // in the split view the first floor sits beside the ground floor: an arc shows the stair leads there
    let arc = null;
    if (r.stair && floorsUsed.size > 1 && state.view === 'split') {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(ARC_POINTS * 3), 3));
      arc = new THREE.Line(geo, new THREE.LineDashedMaterial({ color, dashSize: 0.25, gapSize: 0.18, transparent: true, opacity: 0.85, depthTest: false }));
      arc.renderOrder = 11; arc.layers.set(1);
      scene.add(arc);
      objects.push(arc);
    }
    if (r.to?.id) flag([r.to.id], 'urgent', { color: SEVERITY_COLOR.urgent });
    routeRig = { objects, curves, prev, arc, arcAt: null, stair: r.stair, you, goal, flagged: !!r.to?.id, tween: null, t0: performance.now(), grown: reduceMotion ? 1 : 0, pulses: [] };
    // pulses: little lights travelling the path, start to end, again and again
    if (!reduceMotion) {
      for (const c of curves) {
        const n = Math.max(2, Math.round(c.curve.getLength() / 2.2));
        for (let i = 0; i < n; i++) {
          const p = new THREE.Mesh(new THREE.SphereGeometry(0.085, 14, 10), new THREE.MeshBasicMaterial({ color: 0xfff4e0, transparent: true, depthTest: false, toneMapped: false }));
          p.renderOrder = 13; p.layers.set(1);
          c.parent.add(p);
          objects.push(p);
          routeRig.pulses.push({ mesh: p, c, offset: i / n });
        }
      }
    }
    const rig = routeRig;
    const grow = (k) => {
      for (const c of rig.curves) {
        const per = c.core.geometry.index.count / c.segs;
        const upto = Math.max(per, Math.round(k * c.segs) * per);
        c.core.geometry.setDrawRange(0, upto);
        c.glowTube.geometry.setDrawRange(0, Math.round(k * c.segs) * (c.glowTube.geometry.index.count / c.segs));
      }
      invalidate();
    };
    if (reduceMotion) grow(1);
    else {
      grow(0);
      const p = { k: 0 };
      rig.tween = gsap.to(p, { k: 1, duration: 2.2, ease: 'power2.inOut', delay: 0.6, onUpdate: () => { rig.grown = p.k; grow(p.k); } });
    }
    // frame the whole walk, both markers in view
    scene.updateMatrixWorld(true);
    const box = new THREE.Box3();
    objects.forEach((o) => { if (o !== arc) box.expandByObject(o); });
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const vfov = THREE.MathUtils.degToRad(camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const dist = Math.max(10, (sphere.radius / Math.sin(Math.min(vfov, hfov) / 2)) * 1.08);
    state.focus = { objs: [you, goal] };
    flyTo(sphere.center.clone().add(new THREE.Vector3(0, Math.sin(ELEV), Math.cos(ELEV)).multiplyScalar(dist)), sphere.center, 1.4);
    invalidate(true);
    emit('state', getState());
    return true;
  }
  const ARC_POINTS = 41;
  const _a = new THREE.Vector3(), _b2 = new THREE.Vector3(), _m = new THREE.Vector3();
  function animateRoute(now) {
    const rig = routeRig;
    if (!rig) return;
    const t = Math.max(0, (now - rig.t0) / 1000); // a frame can be stamped just before the route began
    for (const p of rig.pulses) {
      const k = Math.min(0.9999, (t * 0.22 + p.offset) % 1);
      const visible = k <= rig.grown;
      p.mesh.visible = visible;
      if (visible) p.mesh.position.copy(p.c.curve.getPointAt(k));
      p.mesh.material.opacity = Math.sin(Math.min(1, k) * Math.PI) * 0.95;
    }
    for (const m of [rig.you, rig.goal]) {
      m.children.forEach((c) => {
        if (!c.userData.wave) return;
        const k = (t * 0.7 + (m === rig.goal ? 0.5 : 0)) % 1;
        c.scale.setScalar(1 + k * 1.6);
        c.material.opacity = 0.65 * (1 - k);
      });
    }
    if (rig.arc && rig.stair) {
      // from the top of the stair on the ground floor to the landing on the first floor, through the air; rebuilt
      // only when the first floor moves (it slides when the view changes)
      const gf = house.floors.GF, ff = house.floors.FF;
      const key = `${ff.position.x.toFixed(3)},${ff.position.y.toFixed(3)},${ff.position.z.toFixed(3)}`;
      if (rig.arcAt !== key) {
        rig.arcAt = key;
        const [tx, ty, tz] = rig.stair.top;
        gf.localToWorld(_a.set(tx, ty, tz + ROUTE_LIFT));
        ff.localToWorld(_b2.set(tx, ty + 0.2, ff.userData.z + ROUTE_LIFT));
        _m.copy(_a).lerp(_b2, 0.5); _m.y += 2.2;
        const curve = new THREE.QuadraticBezierCurve3(_a.clone(), _m.clone(), _b2.clone());
        const pos = rig.arc.geometry.attributes.position;
        curve.getPoints(ARC_POINTS - 1).forEach((q, i) => pos.setXYZ(i, q.x, q.y, q.z));
        pos.needsUpdate = true;
        rig.arc.geometry.computeBoundingSphere();
        rig.arc.computeLineDistances();
      }
      rig.arc.material.dashOffset = -t * 0.6;
    }
    dirty = true;
  }

  // ---------- projection: "what if they wait?" (Lab) ----------
  // The case's part glows in the colour of where it has got to, with a line saying how far; a leak's lost water
  // stands beside the house as one cube, to scale (1 m = 1 m: 1,000 litres is a 1 m cube), next to a 1 m³ cube for
  // comparison. Called again and again as the slider moves: only what changed is redrawn.
  let proj = null;
  function clearProjection() {
    if (!proj) return;
    const p = proj;
    proj = null;
    p.objects.forEach((o) => { o.removeFromParent(); o.traverse?.((m) => { m.geometry?.dispose(); m.material?.dispose?.(); }); });
    clearFlags();
    invalidate(true);
  }
  function waterCube(fg) {
    const g = new THREE.Group();
    const water = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: 0x2b7fb8, transparent: true, opacity: 0.5, roughness: 0.08, metalness: 0, depthWrite: false }));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), new THREE.LineBasicMaterial({ color: 0x9fd3f5, transparent: true, opacity: 0.9 }));
    const surface = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ color: 0xbfe6ff, transparent: true, opacity: 0.55, depthWrite: false, side: THREE.DoubleSide }));
    surface.position.z = 0.5;
    g.add(water, edges, surface);
    g.userData.parts = { water, edges, surface };
    fg.userData.layers.labels.add(g);
    return g;
  }
  /**
   * opts: { key, layer, ids, severity, label, waterM3?, maxWaterM3? }. `key` names the case: a new key frames the
   * view (wide enough for the biggest cube the slider can reach), the same key only updates.
   */
  function projection({ key, layer, ids = [], severity = 'heads_up', label = '', waterM3 = null, maxWaterM3 = null } = {}) {
    const objs = ids.map((id) => house.byId.get(id)).filter(Boolean);
    if (!proj || proj.key !== key) {
      clearFocus({ reframe: false });
      clearProjection();
      proj = { key, sev: null, label: null, tag: null, cube: null, refCube: null, objects: [], ids };
      if (layer === 'water' || layer === 'electrical') setLayer(layer, true);
      if (state.view !== 'split') setView('split', { reframe: false });
      state.xray = true;
      const gf = house.floors.GF;
      if (maxWaterM3 != null) {
        // the cube stands on the ground to the left of the house (the first floor is on the right in this view)
        const cube = waterCube(gf);
        const ref = new THREE.Group();
        const refEdges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), new THREE.LineDashedMaterial({ color: 0x1f4d40, dashSize: 0.12, gapSize: 0.08 }));
        refEdges.computeLineDistances();
        ref.add(refEdges);
        gf.userData.layers.labels.add(ref);
        const el = document.createElement('div');
        el.className = 'proj-tag ref';
        el.textContent = '1 m³ = 1,000 L';
        const refTag = new CSS2DObject(el);
        refTag.position.set(0, 0, 0.9);
        ref.add(refTag);
        const maxSide = Math.cbrt(Math.max(1, maxWaterM3));
        cube.userData.maxSide = maxSide;
        const x = -1.6 - maxSide / 2;
        cube.position.set(x, spec.meta.depth * 0.42, 0);
        ref.position.set(x + maxSide / 2 + 0.9, spec.meta.depth * 0.42 - maxSide / 2 - 0.2, 0.5);
        const tagEl = document.createElement('div');
        tagEl.className = 'proj-tag water';
        const tag = new CSS2DObject(tagEl);
        cube.add(tag);
        proj.cube = cube; proj.refCube = ref; proj.cubeTag = tag;
        proj.objects.push(cube, ref);
      }
      // frame the part and (for water) the biggest cube the slider can reach
      scene.updateMatrixWorld(true);
      const box = new THREE.Box3();
      objs.forEach((o) => box.expandByObject(o));
      if (proj.cube) {
        const s = proj.cube.userData.maxSide, c = proj.cube.position;
        const lo = house.floors.GF.localToWorld(new THREE.Vector3(c.x - s / 2, c.y - s / 2, 0));
        const hi = house.floors.GF.localToWorld(new THREE.Vector3(c.x + s / 2, c.y + s / 2, s));
        box.expandByPoint(lo).expandByPoint(hi);
        box.expandByObject(house.floors.GF.userData.layers.structure);
      }
      if (box.isEmpty()) box.expandByObject(house.floors.GF.userData.layers.structure);
      const sphere = box.getBoundingSphere(new THREE.Sphere());
      const vfov = THREE.MathUtils.degToRad(camera.fov);
      const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
      const dist = Math.max(9, (sphere.radius / Math.sin(Math.min(vfov, hfov) / 2)) * 0.82); // the bounding sphere is generous: the corners can go
      flyTo(sphere.center.clone().add(new THREE.Vector3(0, Math.sin(ELEV), Math.cos(ELEV)).multiplyScalar(dist)), sphere.center, 1.2);
      state.focus = objs.length ? { objs } : null;
    }
    // the part's glow and its line, only when they change
    if (proj.sev !== severity && objs.length) {
      clearFlags();
      flag(ids, severity);
      proj.sev = severity;
      if (!proj.tag) {
        const el = document.createElement('div');
        el.className = 'focus-label proj';
        const tag = new CSS2DObject(el);
        const b = new THREE.Box3().setFromObject(objs[0]);
        objs[0].worldToLocal(tag.position.set((b.min.x + b.max.x) / 2, b.max.y + 0.2, (b.min.z + b.max.z) / 2));
        objs[0].add(tag);
        proj.tag = tag;
        proj.objects.push(tag);
      }
    }
    if (proj.tag) {
      proj.tag.element.className = `focus-label proj sev-${severity}`;
      if (proj.label !== label) { proj.tag.element.textContent = label; proj.label = label; }
    }
    if (proj.cube && waterM3 != null) {
      const s = Math.max(0.001, Math.cbrt(waterM3));
      proj.cube.scale.set(s, s, s);
      proj.cube.position.z = s / 2;
      proj.cube.visible = waterM3 > 0.0005;
      proj.cubeTag.position.set(0, 0, 0.5 + 0.35 / s); // just above the top face (in the cube's scaled space)
      const litres = waterM3 * 1000;
      proj.cubeTag.element.textContent = litres >= 10000 ? `${waterM3.toFixed(waterM3 < 100 ? 1 : 0)} m³ of water` : `${Math.round(litres).toLocaleString('en-MY')} litres`;
    }
    invalidate(true);
    return true;
  }

  // ---------- capture: the stage's visible part as an image, and as a short clip while inspecting ----------
  const ACCENT = '#48d6a6';
  function frameRect() {
    const w = canvas.width, h = canvas.height;
    const left = inset.left > 0 && host && host.clientWidth > 960 ? Math.round(inset.left * (w / host.clientWidth)) : 0;
    return { sx: left, sy: 0, sw: w - left, sh: h };
  }
  function overlay(ctx, W, H, { label, rec = false, t = 0 } = {}) {
    const k = W / 960, m = 18 * k, L = 34 * k;
    ctx.save();
    ctx.strokeStyle = ACCENT; ctx.lineWidth = 2.2 * k;
    for (const [x, y, dx, dy] of [[m, m, 1, 1], [W - m, m, -1, 1], [m, H - m, 1, -1], [W - m, H - m, -1, -1]]) {
      ctx.beginPath(); ctx.moveTo(x, y + dy * L); ctx.lineTo(x, y); ctx.lineTo(x + dx * L, y); ctx.stroke();
    }
    const font = getComputedStyle(document.body).fontFamily || 'sans-serif';
    ctx.font = `600 ${13 * k}px ${font}`;
    ctx.fillStyle = 'rgba(16, 22, 20, 0.62)';
    const head = `PENJAGA · 3D INSPECTION${label ? ` · ${label.toUpperCase()}` : ''}`;
    const tw = ctx.measureText(head).width;
    ctx.fillRect(m + 10 * k, m + 10 * k, tw + (rec ? 64 : 24) * k, 26 * k);
    ctx.fillStyle = '#f4efe7';
    ctx.fillText(head, m + 22 * k, m + 28 * k);
    if (rec) {
      ctx.fillStyle = Math.floor(t * 2) % 2 ? '#e5484d' : 'rgba(229,72,77,0.35)';
      ctx.beginPath(); ctx.arc(m + 34 * k + tw, m + 23 * k, 5 * k, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#f4efe7';
      ctx.fillText('REC', m + 42 * k + tw, m + 28 * k);
    }
    ctx.restore();
  }
  function capture(label = insp?.label) {
    if (lite) renderer.render(scene, camera); else composer.render();
    const { sx, sy, sw, sh } = frameRect();
    const W = Math.min(1280, sw), H = Math.round((sh * W) / sw);
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    const ctx = c.getContext('2d');
    ctx.drawImage(canvas, sx, sy, sw, sh, 0, 0, W, H);
    overlay(ctx, W, H, { label });
    invalidate();
    return c.toDataURL('image/jpeg', 0.88);
  }
  let recorder = null;
  function startRecording(label) {
    stopRecording();
    if (reduceMotion || typeof MediaRecorder === 'undefined') return;
    const { sw, sh } = frameRect();
    // video encoders need even dimensions (an odd height records nothing)
    const W = 2 * Math.round(Math.min(960, sw) / 2), H = 2 * Math.round((sh * W) / sw / 2);
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    const ctx = c.getContext('2d');
    const stream = c.captureStream(30);
    const type = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t));
    if (!type) return;
    const rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 4_000_000 });
    const chunks = [];
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    const started = performance.now();
    recorder = {
      rec, label,
      draw() {
        const r = frameRect();
        ctx.drawImage(canvas, r.sx, r.sy, r.sw, r.sh, 0, 0, W, H);
        overlay(ctx, W, H, { label, rec: true, t: (performance.now() - started) / 1000 });
      },
      done: new Promise((res) => { rec.onstop = () => res(chunks.length ? new Blob(chunks, { type }) : null); }),
    };
    if (insp) insp.label = label;
    rec.start(250);
  }
  function stopRecording() {
    if (!recorder) return Promise.resolve(null);
    const r = recorder;
    recorder = null;
    if (r.rec.state !== 'inactive') r.rec.stop();
    return r.done;
  }

  // ---------- "Can I drill here?": what runs inside a wall ----------
  function servicesInWall(wallId) {
    const w = house.byId.get(wallId)?.userData.spec;
    if (!w) return [];
    const [[x1, y1], [x2, y2]] = [w.a, w.b];
    const pad = 0.02;
    const box = {
      x: [Math.min(x1, x2) - w.t / 2 - pad, Math.max(x1, x2) + w.t / 2 + pad],
      y: [Math.min(y1, y2) - w.t / 2 - pad, Math.max(y1, y2) + w.t / 2 + pad],
      z: [w.z0, w.z0 + w.h],
    };
    const inBox = (p) => p[0] >= box.x[0] && p[0] <= box.x[1] && p[1] >= box.y[0] && p[1] <= box.y[1] && p[2] >= box.z[0] && p[2] <= box.z[1];
    const out = [];
    for (const o of house.byId.values()) {
      const paths = o.userData.paths;
      if (!paths || o.userData.floor !== w.floor) continue;
      const heights = [];
      for (const path of paths) {
        for (let i = 0; i < path.length - 1; i++) {
          const a = path[i], b = path[i + 1];
          const n = Math.max(2, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) / 0.05));
          for (let k = 0; k <= n; k++) {
            const t = k / n, p = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
            if (inBox(p)) heights.push(p[2] - w.z0);
          }
        }
      }
      if (heights.length) out.push({ ...describe(o), from_m: Math.max(0, Math.min(...heights)), to_m: Math.min(w.h, Math.max(...heights)) });
    }
    return out;
  }
  function drillCheck(wallId) {
    const wall = house.byId.get(wallId);
    if (!wall) return null;
    const found = servicesInWall(wallId);
    clearFlags();
    setLayer('water', found.some((f) => f.layer === 'water') || state.water);
    setLayer('electrical', found.some((f) => f.layer === 'electrical') || state.electrical);
    focusObject(wall, { silent: true });
    found.forEach((f) => flag([f.id], 'info'));
    return { wall: describe(wall), services: found };
  }

  // ---------- see-through walls ----------
  const ray = new THREE.Raycaster();
  const _b = new THREE.Box3(), _p = new THREE.Vector3();
  let faded = new Map();
  function focusPoints() {
    const f = state.focus;
    if (f.room) return [house.floors[f.room.floor].localToWorld(new THREE.Vector3(...f.room.center)).add(new THREE.Vector3(0, 0.6, 0))];
    if (f.objs) return f.objs.map((o) => _b.setFromObject(o).getCenter(new THREE.Vector3())); // several: each centre
    _b.setFromObject(f.obj);
    const c = _b.getCenter(new THREE.Vector3()), pts = [c];
    for (const y of [_b.max.y, Math.min(_b.min.y + 0.25, c.y)]) {
      for (const [sx, sz] of [[0.4, 0.4], [-0.4, 0.4], [0.4, -0.4], [-0.4, -0.4]]) {
        pts.push(new THREE.Vector3(c.x + (_b.max.x - _b.min.x) * sx, y, c.z + (_b.max.z - _b.min.z) * sz));
      }
    }
    return pts;
  }
  function updateWallFade() {
    const want = new Map();
    if ((state.water || state.electrical) && !insp) { // an inspection fades only the walls in the way
      // translucent enough to read the runs inside, solid enough that the rooms still read as rooms
      walls.forEach((w) => want.set(w, 0.45));
      wallLike.forEach((w) => want.set(w, 0.45));
      groundPlanes.forEach((f) => want.set(f, 0.7));
    }
    if (state.xray) [...walls, ...wallLike].forEach((w) => want.set(w, 0.22));
    else if (state.focus) {
      const visibleWalls = walls.filter((w) => house.floors[w.userData.spec.floor].visible);
      for (const pt of focusPoints()) {
        const dir = _p.copy(pt).sub(camera.position);
        ray.set(camera.position, dir.clone().normalize());
        ray.far = dir.length() - 0.15;
        for (const hit of ray.intersectObjects(visibleWalls, true)) {
          let o = hit.object;
          while (o && o.userData.kind !== 'wall') o = o.parent;
          if (o) want.set(o, state.focus.reveal?.o ?? 0.1);
        }
      }
      const o = state.focus.obj;
      const own = o && (o.userData.kind === 'wall' ? o : o.userData.wall ? house.byId.get(o.userData.wall) : null);
      if (own) want.set(own, 0.28);
    }
    for (const w of faded.keys()) if (!want.has(w)) { setOpacity(w, 1); aoValid = false; }
    for (const [w, a] of want) if (faded.get(w) !== a || (w.userData.opacity ?? 1) >= 1) { setOpacity(w, a); aoValid = false; }
    faded = want;
  }

  // ---------- pointer ----------
  const pointer = new THREE.Vector2();
  const pick = new THREE.Raycaster();
  pick.layers.enableAll();
  let downAt = null;
  function elementAt(e) {
    const r = canvas.getBoundingClientRect();
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    pick.setFromCamera(pointer, camera);
    const targets = Object.values(house.floors).filter((f) => f.visible);
    for (const hit of pick.intersectObjects(targets, true)) {
      let o = hit.object;
      if (!isVisible(o)) continue;
      while (o && !o.userData.penjaga_id) o = o.parent;
      if (!o) continue;
      const wall = o.userData.kind === 'wall' ? o : o.userData.wall ? house.byId.get(o.userData.wall) : null;
      if (state.drill) return wall ?? null; // drill mode only cares about walls
      if (wall && faded.has(wall)) continue; // see-through walls let you click what's behind them
      if (['glass', 'window', 'slider'].includes(o.userData.kind)) continue;
      return o;
    }
    return null;
  }
  canvas.addEventListener('pointermove', (e) => {
    if (!state.interactive) return;
    const o = elementAt(e);
    if (o === hovered) return;
    if (hovered && hovered !== selected && !flagged.includes(hovered)) setHighlight(hovered, null);
    hovered = o;
    if (o && o !== selected) setHighlight(o, 0x08140f);
    canvas.style.cursor = o ? 'pointer' : '';
    emit('hover', describe(o));
    invalidate();
  });
  canvas.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', (e) => {
    if (!state.interactive || !downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4) return;
    const o = elementAt(e);
    if (state.drill) { if (o) emit('drill', drillCheck(o.name)); return; }
    if (o) focusObject(o);
  });
  controls.addEventListener('start', () => gsap.killTweensOf([camera.position, controls.target]));
  controls.addEventListener('change', () => { moving = true; aoValid = false; invalidate(); });

  // ---------- mounting ----------
  let host = null;
  let inset = { left: 0 };
  const ro = new ResizeObserver(() => resize());
  function resize() {
    if (!host) return;
    const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight);
    camera.aspect = w / h;
    if (inset.left > 0 && w > 960) camera.setViewOffset(w, h, -inset.left / 2, 0, w, h);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    composer.setSize(w, h);
    labelRenderer.setSize(w, h);
    aoValid = false;
    invalidate();
  }
  function mount(el) {
    if (host === el) return;
    host?.classList.remove('has-viewer');
    host && ro.unobserve(host);
    host = el;
    el.classList.add('has-viewer');
    el.append(canvas, labelRenderer.domElement);
    ro.observe(el);
    resize();
  }
  function unmount(el) {
    if (host !== el) return;
    ro.unobserve(el);
    canvas.remove();
    labelRenderer.domElement.remove();
    host = null;
  }
  function setInteractive(on) {
    state.interactive = on;
    controls.enabled = on;
    canvas.style.cursor = '';
  }
  // Part of the stage covered by UI (e.g. the agent column): the camera re-centres on what's left.
  function setInsets(next) { inset = { left: 0, ...next }; resize(); }
  function setAutoRotate(on) { controls.autoRotate = on && !reduceMotion; invalidate(); }
  function setDrill(on) { state.drill = on; if (!on) clearFlags(); emit('state', getState()); }
  function getState() {
    return { view: state.view, water: state.water, electrical: state.electrical, xray: state.xray, furniture: state.furniture, drill: state.drill };
  }
  function setXray(on) { state.xray = on; invalidate(true); emit('state', getState()); }

  // ---------- animation ----------
  let lastSpin = 0;
  function spinFans(now) {
    if (reduceMotion || !house.spinners.length || !host) return;
    const dt = Math.min((now - (lastSpin || now)) / 1000, 0.1);
    lastSpin = now;
    for (const s of house.spinners) {
      if (!isVisible(s)) continue;
      s.rotation[s.parent?.userData.spinAxis ?? 'y'] += dt * 4.2;
      dirty = true;
    }
  }
  const _c = new THREE.Color();
  function pulseFlags(now) {
    if (reduceMotion || !flagged.length) return;
    const k = 0.55 + 0.45 * Math.sin(now / 260);
    for (const o of flagged) {
      if (o.userData.flagColor === undefined || !o.userData.ownMats) continue;
      _c.setHex(o.userData.flagColor).multiplyScalar(1.5 + 4 * k);
      o.traverse((m) => {
        if (!m.isMesh) return;
        (Array.isArray(m.material) ? m.material : [m.material]).forEach((mat) => { if (mat.emissive && !mat.userData.lamp) mat.emissive.copy(_c); });
      });
    }
    dirty = true;
  }

  renderer.setAnimationLoop((now) => {
    if (!host) return; // nothing to draw into
    controls.update();
    pulseFlags(now);
    spinFans(now);
    animateRoute(now);
    aoCamera.position.copy(camera.position);
    aoCamera.quaternion.copy(camera.quaternion);
    aoCamera.aspect = camera.aspect;
    aoCamera.projectionMatrix.copy(camera.projectionMatrix);
    aoCamera.projectionMatrixInverse.copy(camera.projectionMatrixInverse);
    if (gsap.globalTimeline.getChildren(true, true, false).some((t) => t.isActive())) { dirty = true; moving = true; aoValid = false; }
    if (recorder) dirty = true; // a clip needs every frame
    if (dirty) {
      dirty = false;
      bounds.clampPoint(controls.target, controls.target);
      updateWallFade();
      if (renderer.shadowMap.needsUpdate) moving = true;
      gtao.enabled = true;
      gtao.reuse = aoValid;
      if (lite) renderer.render(scene, camera); else composer.render();
      recorder?.draw();
      if (!moving) aoValid = true;
      labelRenderer.render(scene, camera);
      hqDone = !moving;
      if (moving) lastChange = now;
      moving = false;
    } else if (!hqDone && !lite && now - lastChange > 180) {
      gtao.reuse = false;
      composer.render();
      aoValid = true;
      hqDone = true;
    }
  });

  // ---------- start ----------
  house.floors.FF.position.copy(FF_SPLIT);
  applyVisibility();
  scene.updateMatrixWorld(true);
  fitShadow();
  camera.position.set(7, 34, 30);
  controls.target.set(7, 0, -4);
  renderer.shadowMap.needsUpdate = true;

  return {
    mount, unmount, setInteractive, setAutoRotate, setInsets,
    frame, setView, setLayer, setXray, setDrill, getState,
    focus, focusRoom, focusId, flag, clearFlags, clearFocus, drillCheck, servicesInWall, scan, inspect, capture, route, clearRoute, projection, clearProjection, trace: (ids, severity) => trace(ids.map((id) => house.byId.get(id)).filter(Boolean), severity), roomOf,
    describe: (id) => describe(house.byId.get(id)),
    has: (id) => house.byId.has(id),
    on(ev, f) { (listeners[ev] ||= []).push(f); return () => { listeners[ev] = listeners[ev].filter((x) => x !== f); }; },
    reduceMotion,
  };
}
