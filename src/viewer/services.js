// Water + electrical services (PRD §11.1): pipes, cable runs, fixtures, appliances.
// Concealed parts (pipes, cables, roof tank) live in per-floor 'water' / 'electrical' layer groups that can be
// toggled; visible devices (sockets, aircons, heaters, fans, taps, DB) live in an always-on 'devices' group.
// Every node is named by its contract ID.
import * as THREE from 'three';
import { materials, box, rbox, cyl, mergeByMaterial } from './kit.js';
import { placeModel } from './assets.js';

const FACE_ROT = { '-Y': 0, '+X': 90, '+Y': 180, '-X': -90 };
const FACE_VEC = { '-Y': [0, -1], '+X': [1, 0], '+Y': [0, 1], '-X': [-1, 0] };

// Service colours carry meaning (water vs electrical, hot vs cold), as in BIM/MEP viewers.
export const SERVICE_COLORS = {
  main: 0x1f5fa8, cold: 0x2d9cdb, hot: 0xe0524a, overflow: 0x7fc8e8, waste: 0x8a847c,
  lighting: 0xf2c94c, sockets: 0xf2994a, dedicated: 0xb36bd9, riser: 0x9a8f86,
};
const svcMat = {};
function serviceMaterial(key) {
  if (!svcMat[key]) {
    const c = new THREE.Color(SERVICE_COLORS[key]);
    svcMat[key] = new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.18, roughness: 0.45, metalness: 0.05 });
  }
  return svcMat[key];
}

// Polyline → cylinders + joint spheres (sharp corners, like real pipework). Display radius has a floor so
// 15 mm pipes stay visible from across the house; the true diameter stays in the contract.
function polyline(points, radius, mat) {
  const g = new THREE.Group();
  const a = new THREE.Vector3(), b = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
  for (let i = 0; i < points.length - 1; i++) {
    a.set(...points[i]); b.set(...points[i + 1]);
    const len = a.distanceTo(b);
    if (len < 1e-4) continue;
    const m = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, len, 10, 1, true), mat);
    m.position.copy(a).add(b).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(up, b.clone().sub(a).normalize());
    g.add(m);
  }
  for (const p of points) {
    const j = new THREE.Mesh(new THREE.SphereGeometry(radius * 1.15, 10, 8), mat);
    j.position.set(...p);
    g.add(j);
  }
  return g;
}

function element(id, props) {
  const g = new THREE.Group();
  g.name = id;
  g.userData = { penjaga_id: id, ...props };
  return g;
}

// ---------- device models (local: Z-up, back on the wall at y = 0, front towards -Y) ----------
const DEVICES = {
  socket13A() {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(0.146, 0.01, 0.086, 0.004, M.lacquer, 0, -0.005, -0.043));
    for (const sx of [-1, 1]) {
      g.add(box([sx * 0.035 - 0.02, sx * 0.035 + 0.02, -0.012, -0.009, -0.025, 0.012], M.matteWhite));
      g.add(box([sx * 0.035 - 0.008, sx * 0.035 + 0.008, -0.013, -0.011, 0.018, 0.032], M.blackMetal));
    }
    return g;
  },
  valve() {
    const M = materials(), g = new THREE.Group();
    const body = cyl(0.012, 0.012, 0.06, M.steel, 0, 0, 0, 12);
    body.rotation.x = Math.PI / 2; body.position.set(0, -0.03, 0);
    g.add(body);
    g.add(box([-0.02, 0.02, -0.05, -0.04, -0.006, 0.006], M.steel));
    return g;
  },
  tap() {
    const M = materials(), g = new THREE.Group();
    g.add(cyl(0.025, 0.025, 0.012, M.steel, 0, 0, -0.025, 16).rotateX(Math.PI / 2));
    const body = cyl(0.012, 0.012, 0.09, M.steel, 0, 0, 0, 12);
    body.rotation.x = Math.PI / 2; body.position.set(0, -0.045, 0);
    g.add(body);
    g.add(cyl(0.01, 0.01, 0.05, M.steel, 0, -0.085, -0.05, 10));
    g.add(box([-0.035, 0.035, -0.06, -0.05, 0.015, 0.025], M.steel));
    return g;
  },
  mixer() {
    const M = materials(), g = new THREE.Group();
    const plate = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.01, 32), M.blackMetal);
    plate.position.y = -0.005;
    g.add(plate);
    g.add(box([-0.008, 0.008, -0.06, -0.01, -0.008, 0.008], M.blackMetal));
    g.add(box([-0.008, 0.008, -0.07, -0.055, -0.008, 0.07], M.blackMetal));
    return g;
  },
  waterHeater() {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(0.22, 0.09, 0.34, 0.02, M.lacquer, 0, -0.045, -0.17));
    g.add(box([-0.06, 0.06, -0.092, -0.088, 0.04, 0.09], M.blackGlass));
    g.add(cyl(0.012, 0.012, 0.06, M.steel, -0.05, -0.04, -0.23, 10));
    g.add(cyl(0.012, 0.012, 0.06, M.steel, 0.05, -0.04, -0.23, 10));
    return g;
  },
  acIndoor() {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(0.9, 0.22, 0.29, 0.04, M.lacquer, 0, -0.11, -0.145));
    g.add(box([-0.4, 0.4, -0.225, -0.205, -0.13, -0.11], M.screen));
    g.add(box([0.33, 0.37, -0.226, -0.222, 0.05, 0.07], M.blackGlass));
    return g;
  },
  distributionBoard() {
    const M = materials(), g = new THREE.Group();
    const w = 0.42, h = 0.5, d = 0.11;
    g.add(rbox(w, d, h, 0.008, new THREE.MeshStandardMaterial({ color: 0xdedcd6, roughness: 0.5, metalness: 0.2 }), 0, -d / 2, 0));
    g.add(box([-w / 2 + 0.02, w / 2 - 0.02, -d - 0.004, -d + 0.002, 0.2, 0.38], M.blackGlass));
    for (let i = 0; i < 12; i++) {
      const x = -w / 2 + 0.05 + i * 0.027;
      g.add(box([x - 0.009, x + 0.009, -d - 0.012, -d - 0.004, 0.25, 0.33], i === 0 ? M.blackMetal : M.matteWhite));
    }
    return g;
  },
  rcd() {
    const g = new THREE.Group();
    const m = new THREE.MeshStandardMaterial({ color: 0x2a6fb0, roughness: 0.5 });
    g.add(box([-0.018, 0.018, -0.01, 0, -0.045, 0.045], m));
    g.add(box([-0.006, 0.006, -0.016, -0.01, 0.02, 0.032], new THREE.MeshStandardMaterial({ color: 0xd8c64a })));
    return g;
  },
  ceilingFan(item) {
    const M = materials(), g = new THREE.Group();
    const real = placeModel('ceiling_fan', 0.52);
    if (real) {
      real.position.z = -0.52;
      g.add(real);
      real.traverse((o) => { if (o.name === 'ceiling_fan_blades') g.userData.spinner = o; });
      g.userData.spinner?.traverse((o) => o.layers.set(1));
      return g;
    }
    g.add(cyl(0.012, 0.012, 0.3, M.blackMetal, 0, 0, -0.3, 8));
    g.add(cyl(0.09, 0.08, 0.12, M.matteWhite, 0, 0, -0.42, 20));
    const blades = new THREE.Group();
    for (let i = 0; i < 3; i++) {
      const b = box([0.08, 0.66, -0.06, 0.06, -0.01, 0.0], M.matteWhite);
      const arm = new THREE.Group(); arm.rotation.z = (i / 3) * Math.PI * 2; arm.add(b);
      blades.add(arm);
    }
    blades.position.z = -0.38;
    blades.traverse((o) => o.layers.set(1));
    g.add(blades);
    g.userData.spinner = blades;
    g.userData.spinAxis = 'z';
    return g;
  },
  downlight() {
    const g = new THREE.Group();
    const trim = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.012, 28).rotateX(Math.PI / 2), materials().matteWhite);
    trim.position.z = -0.006;
    const lens = new THREE.Mesh(new THREE.CircleGeometry(0.055, 28), new THREE.MeshStandardMaterial({ color: 0xfff3dc, emissive: 0xffe2b0, emissiveIntensity: 1.2, side: THREE.DoubleSide }));
    lens.material.userData.lamp = true;
    lens.position.z = -0.0125;
    g.add(trim, lens);
    return g;
  },
  inspectionChamber() {
    const g = new THREE.Group();
    g.add(box([-0.3, 0.3, -0.3, 0.3, -0.01, 0.012], new THREE.MeshStandardMaterial({ color: 0x6d6a66, roughness: 0.7, metalness: 0.3 })));
    g.add(box([-0.26, 0.26, -0.26, 0.26, 0.012, 0.016], new THREE.MeshStandardMaterial({ color: 0x55524f, roughness: 0.6, metalness: 0.5 })));
    return g;
  },
  waterMeter() {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(0.36, 0.3, 0.26, 0.01, M.wall, 0, 0, 0));
    g.add(box([-0.16, 0.16, -0.14, 0.14, 0.26, 0.275], M.blackMetal));
    g.add(cyl(0.05, 0.05, 0.02, new THREE.MeshStandardMaterial({ color: 0xb08d57, metalness: 0.8, roughness: 0.35 }), 0, 0, 0.275, 20));
    return g;
  },
  roofTank() {
    const g = new THREE.Group();
    const m = new THREE.MeshStandardMaterial({ color: 0x8f9aa3, roughness: 0.6 });
    g.add(rbox(1.2, 0.9, 0.9, 0.08, m, 0, 0, 0));
    g.add(rbox(0.5, 0.5, 0.05, 0.02, new THREE.MeshStandardMaterial({ color: 0x6f7a83, roughness: 0.6 }), 0, 0, 0.9));
    return g;
  },
  floatValve() {
    const g = new THREE.Group();
    const brass = new THREE.MeshStandardMaterial({ color: 0xb08d57, metalness: 0.85, roughness: 0.3 });
    g.add(cyl(0.022, 0.022, 0.08, brass, 0, 0, -0.04, 12));
    g.add(box([-0.005, 0.005, -0.3, 0, -0.01, 0.0], brass));
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.07, 16, 12), new THREE.MeshStandardMaterial({ color: 0xe8e3d9, roughness: 0.4 }));
    ball.position.set(0, -0.34, -0.02);
    g.add(ball);
    return g;
  },
  acCondensers() {
    const g = new THREE.Group();
    // one twin outdoor unit on the flat porch roof (multi-split: serves the indoor units)
    for (const [x, y] of [[0, 0]]) {
      const real = placeModel('exterior_aircon_unit', 0.93);
      if (real) { real.position.set(x, y, 0); g.add(real); continue; }
      const M = materials();
      for (const sx of [-0.45, 0.45]) g.add(rbox(0.8, 0.3, 0.6, 0.02, M.matteWhite, x + sx, y, 0));
    }
    return g;
  },
};

function buildDevice(dv) {
  const make = DEVICES[dv.type];
  if (!make) throw new Error(`no device builder for ${dv.type}`);
  const model = make(dv);
  if (dv.face && FACE_ROT[dv.face] !== undefined) model.rotation.z = THREE.MathUtils.degToRad(FACE_ROT[dv.face]);
  model.position.set(...dv.at);
  return model;
}

// Cable route: up from the source to the ceiling void, along X, then along Y, then down inside the wall
// to the device. Representative of how cables are typically run; not an as-built record.
function cableRoute(from, to, ceil) {
  return [from, [from[0], from[1], ceil], [to[0], from[1], ceil], [to[0], to[1], ceil], to];
}

export function buildServices(spec, floors, register) {
  const S = spec.services;
  if (!S) return { spinners: [] };
  const spinners = [];
  const devById = new Map(S.devices.map((d) => [d.id, d]));
  const layer = (floor, name) => floors[floor].userData.layers[name];

  for (const dv of S.devices) {
    const concealed = dv.meta?.concealed;
    const g = element(dv.id, { floor: dv.floor, room: dv.room, layer: dv.layer, kind: dv.type, links: dv.links, meta: dv.meta });
    const model = buildDevice(dv);
    g.add(model);
    if (model.userData.spinner) spinners.push(model.userData.spinner);
    else mergeByMaterial(model);
    layer(dv.floor, concealed ? dv.layer : 'devices').add(g);
    register(g);
  }

  for (const p of S.pipes) {
    const g = element(p.id, { floor: p.floor, room: p.room, layer: 'water', kind: 'pipe', system: p.system, d_mm: Math.round(p.d * 1000), meta: p.meta });
    const r = THREE.MathUtils.clamp(p.d / 2, 0.016, 0.035);
    for (const path of p.paths) g.add(polyline(path, r, serviceMaterial(p.system)));
    g.userData.paths = p.paths; // spec coords, used by "Can I drill here?"
    mergeByMaterial(g);
    layer(p.floor, 'water').add(g);
    register(g);
  }

  // Cable runs, one node per circuit, generated from the device positions.
  const dbTop = (() => { const d = devById.get('db_main'); return [d.at[0] - 0.06, d.at[1], d.at[2] + 0.5]; })();
  const riser = element('cable_riser_db_ff', { floor: 'GF', room: 'foyer', layer: 'electrical', kind: 'cable' });
  riser.add(polyline([dbTop, [S.riser_db_ff[0], dbTop[1], dbTop[2]], S.riser_db_ff], 0.014, serviceMaterial('riser')));
  mergeByMaterial(riser);
  layer('GF', 'electrical').add(riser);
  register(riser);

  const lane = { GF: 0, FF: 0 };
  for (const c of S.circuits) {
    const g = element(c.id, { floor: c.floor, room: null, layer: 'electrical', kind: 'circuit', circuit_kind: c.kind, rating_a: c.rating_a, label: c.label, ends: c.ends });
    const from = c.from === 'db_main' ? dbTop : S.riser_db_ff;
    const ceil = S.ceiling[c.floor] - 0.012 * lane[c.floor]++;
    g.userData.paths = [];
    for (const end of c.ends) {
      const dv = devById.get(end);
      const [fx, fy] = FACE_VEC[dv.face] ?? [0, 0];
      const to = dv.face === 'down' ? dv.at : [dv.at[0] - fx * 0.07, dv.at[1] - fy * 0.07, dv.at[2]];
      const route = cableRoute(from, to, ceil);
      g.add(polyline(route, 0.009, serviceMaterial(c.kind)));
      g.userData.paths.push(route);
    }
    mergeByMaterial(g);
    layer(c.floor, 'electrical').add(g);
    register(g);
  }
  return { spinners };
}
