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

  // ---------- flagged objects (Focus command) ----------
  let flagged = [], flagLabel = null, revealTl = null;
  function clearFlags() {
    revealTl?.kill();
    flagged.forEach((o) => { delete o.userData.flagColor; setHighlight(o, null); });
    flagged = [];
    flagLabel?.removeFromParent();
    flagLabel = null;
    invalidate();
  }
  function clearFocus({ reframe = true } = {}) {
    clearFlags();
    state.focus = null;
    select(null);
    if (reframe) frame();
  }
  function flag(ids, severity = 'heads_up', { label } = {}) {
    const color = SEVERITY_COLOR[severity] ?? SEVERITY_COLOR.heads_up;
    const objs = ids.map((id) => house.byId.get(id)).filter(Boolean);
    objs.forEach((o) => { o.userData.flagColor = color; setHighlight(o, color); });
    flagged = [...new Set([...flagged, ...objs])];
    if (label && objs[0]) {
      flagLabel?.removeFromParent();
      const el = document.createElement('div');
      el.className = `focus-label sev-${severity}`;
      el.innerHTML = `<b>${SEVERITY_LABEL[severity] ?? ''}</b> ${label}`;
      flagLabel = new CSS2DObject(el);
      const b = new THREE.Box3().setFromObject(objs[0]);
      objs[0].worldToLocal(flagLabel.position.set((b.min.x + b.max.x) / 2, b.max.y + 0.15, (b.min.z + b.max.z) / 2));
      objs[0].add(flagLabel);
    }
    invalidate();
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
    focusObject(objs[0], { autoLayer: false, silent: true });
    state.focus.reveal = { o: 1 };
    const finish = () => {
      if (services) setLayer(layer, true);
      flag(object_ids, severity, { label: label ?? objs[0].userData.label ?? objs[0].userData.penjaga_id });
      emit('focusDone', { object_ids });
    };
    if (reduceMotion) { state.focus.reveal.o = 0.1; finish(); return true; }
    revealTl = gsap.timeline({ onUpdate: () => invalidate() })
      .to(state.focus.reveal, { o: 0.55, duration: 0.5, ease: 'power1.out' }, 1.2) // finish layer
      .to(state.focus.reveal, { o: 0.12, duration: 0.5, ease: 'power1.out' }, 2.0) // structure
      .call(finish, null, 2.6); // services + highlight
    return true;
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
    if (state.water || state.electrical) {
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
      _c.setHex(o.userData.flagColor).multiplyScalar(1 + 2.2 * k);
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
    aoCamera.position.copy(camera.position);
    aoCamera.quaternion.copy(camera.quaternion);
    aoCamera.aspect = camera.aspect;
    aoCamera.projectionMatrix.copy(camera.projectionMatrix);
    aoCamera.projectionMatrixInverse.copy(camera.projectionMatrixInverse);
    if (gsap.globalTimeline.getChildren(true, true, false).some((t) => t.isActive())) { dirty = true; moving = true; aoValid = false; }
    if (dirty) {
      dirty = false;
      bounds.clampPoint(controls.target, controls.target);
      updateWallFade();
      if (renderer.shadowMap.needsUpdate) moving = true;
      gtao.enabled = true;
      gtao.reuse = aoValid;
      if (lite) renderer.render(scene, camera); else composer.render();
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
    focus, focusRoom, focusId, flag, clearFlags, clearFocus, drillCheck, servicesInWall,
    describe: (id) => describe(house.byId.get(id)),
    has: (id) => house.byId.has(id),
    on(ev, f) { (listeners[ev] ||= []).push(f); return () => { listeners[ev] = listeners[ev].filter((x) => x !== f); }; },
    reduceMotion,
  };
}
