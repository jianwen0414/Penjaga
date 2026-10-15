// Builds the house procedurally from house.spec.json.
// Spec is Z-up metres; the returned root is rotated so the scene is Y-up.
// Every named spec element becomes a THREE.Group named by its penjaga_id.
import * as THREE from 'three';
import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { materials, box, mergeByMaterial } from './kit.js';
import { buildFurniture } from './furniture.js';
import { buildServices } from './services.js';

const OUTDOOR = new Set(['porch', 'terrace', 'yard']);
const SOLID_MAT = { slab: 'wall', roof: 'roof', column: 'wall', duct: 'wall', ledge: 'bathtile', balustrade: 'oak' };

function element(id, props) {
  const g = new THREE.Group();
  g.name = id;
  g.userData = { penjaga_id: id, ...props };
  return g;
}

function clear(g) {
  for (const c of [...g.children]) {
    c.traverse((o) => o.geometry?.dispose());
    g.remove(c);
  }
}

// Gathered curtain panel: a sine-folded sheet hung parallel to the wall.
function curtain(horiz, c, n, uA, uB, z1, z2, mat) {
  const segs = 28, pos = [], idx = [];
  for (let i = 0; i <= segs; i++) {
    const u = uA + ((uB - uA) * i) / segs;
    const off = n + Math.sin(i * 1.9) * 0.028;
    for (const z of [z1, z2]) pos.push(...(horiz ? [u, c + off, z] : [c + off, u, z]));
  }
  for (let i = 0; i < segs; i++) { const a = i * 2; idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3); }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, mat);
  m.castShadow = true;
  return m;
}

// ---------- walls ----------
// A wall is rebuilt whenever its cutaway height changes, so everything is generated from (spec, cut).
function fillWall(wallObj, w, cut) {
  const M = materials();
  const { body, openings } = wallObj.userData.parts;
  clear(body);
  openings.forEach((g) => clear(g));
  const [[x1, y1], [x2, y2]] = [w.a, w.b];
  const horiz = Math.abs(y2 - y1) < 1e-6;
  const c = horiz ? y1 : x1;
  const t = w.t, z0 = w.z0, top = Math.min(w.h, cut);
  const inside = wallObj.userData.inside; // +1/-1: which side of the wall faces the rooms
  const lo = (horiz ? Math.min(x1, x2) : Math.min(y1, y2)) - t / 2;
  const hi = (horiz ? Math.max(x1, x2) : Math.max(y1, y2)) + t / 2;
  const wallMats = [M.wall, M.wall, M.wall, M.wall, M.cap, M.wall];

  // u = along the wall, n = offset across it (from centreline), z = height above this floor
  const B = (u1, u2, n1, n2, za, zb) => {
    zb = Math.min(zb, top);
    if (u2 - u1 < 1e-4 || zb - za < 1e-4 || n2 - n1 < 1e-5) return null;
    return horiz ? [u1, u2, c + n1, c + n2, z0 + za, z0 + zb] : [c + n1, c + n2, u1, u2, z0 + za, z0 + zb];
  };
  const add = (parent, b, mat, opts) => { if (b) parent.add(box(b, mat, opts)); };
  const piece = (u1, u2, za, zb) => {
    const b = B(u1, u2, -t / 2, t / 2, za, zb);
    if (!b) return;
    add(body, b, Math.min(zb, top) >= top - 1e-4 ? wallMats : M.wall);
    if (za === 0 && !OUTDOOR.has(w.room)) add(body, B(u1 + 0.001, u2 - 0.001, -t / 2 - 0.012, t / 2 + 0.012, 0, 0.08), M.lacquer);
  };

  let cur = lo;
  const ops = [...w.openings].sort((a, b) => a.from - b.from);
  ops.forEach((o, i) => {
    piece(cur, o.from, 0, w.h);
    piece(o.from, o.to, 0, o.sill);
    piece(o.from, o.to, o.top, w.h);
    cur = o.to;
    const g = openings[i];
    const { from: u1, to: u2, sill, top: oTop } = o;
    if (o.kind === 'window' || o.kind === 'slider') {
      const f = 0.05, fd = 0.035;
      add(g, B(u1, u1 + f, -fd, fd, sill, oTop), M.frame);
      add(g, B(u2 - f, u2, -fd, fd, sill, oTop), M.frame);
      add(g, B(u1, u2, -fd, fd, oTop - f, oTop), M.frame);
      add(g, B(u1, u2, -fd, fd, sill, sill + f * (o.kind === 'slider' ? 0.6 : 1)), M.frame);
      const panels = o.kind === 'slider' ? 4 : (u2 - u1 > 1.1 ? 2 : 1);
      for (let k = 1; k < panels; k++) {
        const u = u1 + ((u2 - u1) * k) / panels;
        add(g, B(u - 0.025, u + 0.025, -fd, fd, sill, oTop), M.frame);
      }
      add(g, B(u1 + f, u2 - f, -0.004, 0.004, sill + f, oTop - f), M.glass);
      if (o.kind === 'window') {
        // internal sill board + external drip sill
        const ni = inside > 0 ? [t / 2 - 0.02, t / 2 + 0.03] : [-t / 2 - 0.03, -t / 2 + 0.02];
        const no = inside > 0 ? [-t / 2 - 0.04, -t / 2 + 0.01] : [t / 2 - 0.01, t / 2 + 0.04];
        add(g, B(u1 - 0.04, u2 + 0.04, ni[0], ni[1], sill - 0.025, sill), M.quartz);
        add(g, B(u1 - 0.03, u2 + 0.03, no[0], no[1], sill - 0.04, sill), M.cap);
      }
      if (o.curtain) {
        const n = inside * (t / 2 + 0.09), zTop = Math.min(oTop + 0.2, w.h - 0.05);
        const zr = Math.min(zTop, top);
        if (zr > 0.1) {
          g.add(curtain(horiz, c, n, u1 - 0.3, u1 + 0.08, 0.02, zr, M.curtain));
          g.add(curtain(horiz, c, n, u2 - 0.08, u2 + 0.3, 0.02, zr, M.curtain));
        }
        add(g, B(u1 - 0.32, u2 + 0.32, n * inside < 0 ? n - 0.01 : n - 0.01, n + 0.01, zTop, zTop + 0.02), M.blackMetal);
      }
    } else if (o.kind === 'door') {
      const lin = 0.02, arch = 0.06, at = 0.012;
      // linings + architraves on both faces
      add(g, B(u1, u1 + lin, -t / 2 - 0.002, t / 2 + 0.002, 0, oTop), M.lacquer);
      add(g, B(u2 - lin, u2, -t / 2 - 0.002, t / 2 + 0.002, 0, oTop), M.lacquer);
      add(g, B(u1, u2, -t / 2 - 0.002, t / 2 + 0.002, oTop - lin, oTop), M.lacquer);
      for (const s of [-1, 1]) {
        const n = s > 0 ? [t / 2, t / 2 + at] : [-t / 2 - at, -t / 2];
        add(g, B(u1 - arch, u1, n[0], n[1], 0, oTop + arch), M.lacquer);
        add(g, B(u2, u2 + arch, n[0], n[1], 0, oTop + arch), M.lacquer);
        add(g, B(u1 - arch, u2 + arch, n[0], n[1], oTop, oTop + arch), M.lacquer);
      }
      // leaf, opened 90° about the hinge into the swing side (as drawn on the plan)
      const L = u2 - u1 - 2 * lin, th = 0.04, swing = o.swing ?? inside;
      const hu = o.hinge === 'from' ? u1 + lin : u2 - lin, dir = o.hinge === 'from' ? 1 : -1;
      const ua = Math.min(hu, hu + dir * th), ub = Math.max(hu, hu + dir * th);
      const na = swing > 0 ? t / 2 : -t / 2 - L, nb = swing > 0 ? t / 2 + L : -t / 2;
      const leafMat = o.main ? M.walnut : M.oak;
      add(g, B(ua, ub, na, nb, 0.01, oTop - lin - 0.005), leafMat);
      // three butt hinges on the hinge edge
      const nf = swing > 0 ? t / 2 : -t / 2;
      for (const zh of [0.2, (oTop - lin) / 2, oTop - lin - 0.3]) add(g, B(hu - 0.009, hu + 0.009, nf - 0.009, nf + 0.009, zh, zh + 0.1), M.steel);
      const nh = swing > 0 ? t / 2 + L - 0.07 : -t / 2 - L + 0.07;
      for (const s of [-1, 1]) {
        const uh = s > 0 ? [ub, ub + 0.05] : [ua - 0.05, ua];
        add(g, B(uh[0], uh[1], nh - 0.06, nh + 0.06, 1.0, 1.02), M.blackMetal);
      }
    }
  });
  piece(cur, hi, 0, w.h);
  mergeByMaterial(body);
  openings.forEach((g) => mergeByMaterial(g));
}

function makeWall(w, isInside) {
  const g = element(w.id, { floor: w.floor, room: w.room, layer: 'structure', kind: 'wall' });
  const body = new THREE.Group();
  body.name = `${w.id}__body`;
  g.add(body);
  const openings = w.openings.map((o) => {
    const og = element(o.id, { floor: w.floor, room: w.room, layer: 'structure', kind: o.kind, wall: w.id });
    g.add(og);
    return og;
  });
  // Which side faces the rooms? Probe 0.3 m either side of the wall's midpoint against the indoor floor rects.
  const [[x1, y1], [x2, y2]] = [w.a, w.b];
  const horiz = Math.abs(y2 - y1) < 1e-6;
  const mu = horiz ? (x1 + x2) / 2 : (y1 + y2) / 2, c = horiz ? y1 : x1, d = w.t / 2 + 0.3;
  const probe = (s) => isInside(horiz ? [mu, c + s * d] : [c + s * d, mu]);
  const pIn = probe(1), nIn = probe(-1);
  const inside = pIn && !nIn ? 1 : !pIn && nIn ? -1 : 0;
  g.userData.parts = { body, openings };
  g.userData.inside = inside || 1;
  g.userData.exterior = inside !== 0 || (!pIn && !nIn);
  // outward normal in spec space (for the cutaway)
  g.userData.outward = inside === 0 ? null : horiz ? [0, -inside, 0] : [-inside, 0, 0];
  g.userData.spec = w;
  g.userData.cut = w.h;
  g.userData.rebuild = (cut) => {
    g.userData.cut = cut;
    fillWall(g, w, cut);
    g.userData.ownMats = false;
    g.userData.onRebuild?.(g);
  };
  fillWall(g, w, w.h);
  return g;
}

// ---------- stair: solid white blocks with oak treads, metal balusters + oak handrail on the open side ----------
function makeStair(s) {
  const M = materials();
  const { x1, x2, y1, y2, z1, z2, risers, open } = s.stair;
  const g = element(s.id, { floor: s.floor, room: s.room, layer: 'structure', kind: 'stair' });
  const rise = (z2 - z1) / risers, going = (y2 - y1) / risers;
  for (let i = 0; i < risers; i++) {
    const ya = y1 + i * going, zt = z1 + (i + 1) * rise;
    g.add(box([x1, x2, ya, ya + going, z1, zt - 0.03], M.matteWhite));
    g.add(box([x1 - 0.01, x2, ya - 0.025, ya + going, zt - 0.03, zt], M.oak));
  }
  const xr = open === 'W' ? x1 + 0.03 : x2 - 0.03;
  for (let i = 0; i < risers; i++) {
    const yb = y1 + (i + 0.5) * going, zt = z1 + (i + 1) * rise;
    g.add(box([xr - 0.008, xr + 0.008, yb - 0.008, yb + 0.008, zt, zt + 0.9], M.blackMetal));
  }
  const len = Math.hypot(y2 - y1, z2 - z1);
  const rail = box([-0.025, 0.025, -len / 2, len / 2, -0.02, 0.02], M.oak);
  rail.position.set(xr, (y1 + y2) / 2, (z1 + z2) / 2 + 0.92 + rise / 2);
  rail.rotation.x = Math.atan2(z2 - z1, y2 - y1);
  g.add(rail);
  return mergeByMaterial(g);
}

export function buildHouse(spec) {
  const M = materials();
  const root = new THREE.Group();
  root.name = 'house';
  root.rotation.x = -Math.PI / 2; // spec Z-up → three Y-up (front of house faces +Z)

  const floors = {};
  const byId = new Map();
  const rooms = new Map();
  const walls = [];
  for (const [key, f] of Object.entries(spec.floors)) {
    const fg = new THREE.Group();
    fg.name = `floor_${key}`;
    fg.userData = { floor: key, z: f.z, layers: {} };
    for (const l of ['structure', 'furniture', 'devices', 'water', 'electrical', 'labels']) {
      const lg = new THREE.Group();
      lg.name = `${key}_${l}`;
      fg.userData.layers[l] = lg;
      fg.add(lg);
    }
    floors[key] = fg;
    root.add(fg);
  }
  const add = (obj, floor, layer) => {
    floors[floor].userData.layers[layer].add(obj);
    obj.traverse((o) => { if (o.userData?.penjaga_id) byId.set(o.userData.penjaga_id, o); });
  };

  const indoor = {};
  for (const f of spec.floorFinishes) if (!OUTDOOR.has(f.room)) (indoor[f.floor] ||= []).push(f.rect);
  const isInside = (floor) => ([x, y]) => indoor[floor].some(([a, b, c, d]) => x > a && x < b && y > c && y < d);

  for (const w of spec.walls) {
    const g = makeWall(w, isInside(w.floor));
    walls.push(g);
    add(g, w.floor, 'structure');
  }
  for (const f of spec.floorFinishes) {
    const g = element(f.id, { floor: f.floor, room: f.room, layer: 'structure', kind: 'floor' });
    const [x1, x2, y1, y2] = f.rect;
    const mesh = box([x1, x2, y1, y2, f.z, f.z + 0.012], M[f.finish]);
    mesh.castShadow = false;
    g.add(mesh);
    add(g, f.floor, 'structure');
  }
  for (const s of spec.solids) {
    if (s.kind === 'stair') { add(makeStair(s), s.floor, 'structure'); continue; }
    const g = element(s.id, { floor: s.floor, room: s.room, layer: 'structure', kind: s.kind });
    s.boxes.forEach((b) => g.add(box(b, M[SOLID_MAT[s.kind] ?? s.mat])));
    mergeByMaterial(g);
    add(g, s.floor, 'structure');
  }
  for (const f of spec.furniture) {
    const g = element(f.id, { floor: f.floor, room: f.room, layer: 'furniture', kind: f.type });
    const model = buildFurniture(f, spec.floors[f.floor].z);
    g.add(mergeByMaterial(model));
    add(g, f.floor, 'furniture');
  }
  const { spinners } = buildServices(spec, floors, (g) => byId.set(g.name, g));

  for (const r of spec.rooms) {
    const el = document.createElement('div');
    el.className = 'room-label';
    el.textContent = r.name;
    const label = new CSS2DObject(el);
    label.position.set(r.center[0], r.center[1], r.center[2] + 0.3);
    floors[r.floor].userData.layers.labels.add(label);
    const rects = spec.floorFinishes.filter((f) => f.floor === r.floor && f.room === r.id).map((f) => f.rect);
    const size = [Math.max(...rects.map((q) => q[1])) - Math.min(...rects.map((q) => q[0])),
                  Math.max(...rects.map((q) => q[3])) - Math.min(...rects.map((q) => q[2]))];
    rooms.set(`${r.floor}:${r.id}`, { ...r, size, label, el });
  }

  return { root, floors, byId, rooms, walls, spinners, materials: M };
}

// Per-object material overrides (fade / highlight) without touching shared materials.
export function ownMaterials(obj) {
  if (obj.userData.ownMats) return;
  obj.traverse((m) => {
    if (!m.isMesh) return;
    m.userData.baseMaterial = m.material;
    m.material = Array.isArray(m.material) ? m.material.map((x) => x.clone()) : m.material.clone();
  });
  obj.userData.ownMats = true;
}

export function setOpacity(obj, opacity) {
  obj.userData.opacity = opacity;
  if (opacity >= 1 && !obj.userData.ownMats) return;
  ownMaterials(obj);
  obj.traverse((m) => {
    if (!m.isMesh) return;
    const base = m.userData.baseMaterial;
    const list = Array.isArray(m.material) ? m.material : [m.material];
    const baseList = Array.isArray(base) ? base : [base];
    list.forEach((mat, i) => {
      const b = baseList[i];
      mat.opacity = Math.min(b.opacity, opacity);
      mat.transparent = b.transparent || opacity < 1;
      mat.depthWrite = b.depthWrite && opacity >= 1;
    });
    m.castShadow = opacity >= 1 && !baseList[0].transparent;
  });
}

export function setHighlight(obj, color) {
  ownMaterials(obj);
  obj.traverse((m) => {
    if (!m.isMesh) return;
    (Array.isArray(m.material) ? m.material : [m.material]).forEach((mat) => {
      if (mat.emissive && !mat.userData.lamp) mat.emissive.set(color ?? obj.userData.flagColor ?? 0x000000);
    });
  });
}
