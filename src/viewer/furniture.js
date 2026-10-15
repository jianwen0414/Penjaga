// Parametric furniture. Local space: Z-up metres, footprint centred on the origin,
// base at z = 0, FRONT faces -Y (back against +Y). The spec's rot turns it into place.
import * as THREE from 'three';
import { materials, box, rbox, cyl, rng } from './kit.js';
import * as T from './textures.js';
import { placeModel } from './assets.js';

const legs4 = (g, w, d, inset, h, r, mat, round = true) => {
  for (const sx of [-1, 1]) for (const sy of [-1, 1]) {
    g.add(round ? cyl(r, r * 0.8, h, mat, sx * (w / 2 - inset), sy * (d / 2 - inset), 0, 12)
                : box([sx * (w / 2 - inset) - r, sx * (w / 2 - inset) + r, sy * (d / 2 - inset) - r, sy * (d / 2 - inset) + r, 0, h], mat));
  }
};

function vase(g, x, y, z, s = 1) {
  const real = placeModel(s > 0.95 ? 'ceramic_vase_01' : 'ceramic_vase_03', 0.24 * s);
  if (real) {
    real.position.set(x, y, z);
    real.rotation.z = x * 7;
    g.add(real);
    return;
  }
  const M = materials();
  const pts = [[0, 0], [0.05, 0], [0.065, 0.06], [0.05, 0.16], [0.028, 0.2], [0.032, 0.24]].map(([a, b]) => new THREE.Vector2(a * s, b * s));
  const v = new THREE.Mesh(new THREE.LatheGeometry(pts, 24).rotateX(Math.PI / 2), M.ceramic);
  v.position.set(x, y, z);
  v.castShadow = true;
  g.add(v);
  // a few stems
  for (let i = 0; i < 3; i++) {
    const st = cyl(0.003, 0.003, 0.25 * s, M.leafDark, x + (i - 1) * 0.015, y, z + 0.2 * s, 6);
    st.rotation.y = (i - 1) * 0.25;
    g.add(st);
  }
}

function books(g, x1, x2, y, z, depth = 0.2, seed = 1) {
  const M = materials(), rand = rng(seed);
  let x = x1;
  while (x < x2 - 0.03) {
    const w = 0.02 + rand() * 0.025, h = 0.18 + rand() * 0.1;
    if (x + w > x2) break;
    g.add(box([x, x + w, y - depth / 2, y + depth / 2, z, z + h], M.books[Math.floor(rand() * M.books.length)]));
    x += w + 0.002;
  }
}

function lamp(g, x, y, z, h = 0.45, withLight = false) {
  const M = materials();
  g.add(cyl(0.06, 0.07, h * 0.5, M.ceramic, x, y, z));
  g.add(cyl(0.11, 0.14, h * 0.45, M.lampShade, x, y, z + h * 0.48, 28));
  if (withLight) g.userData.lights = (g.userData.lights || []).concat([[x, y, z + h * 0.7]]);
}

export const BUILDERS = {
  sofa({ size: [w, d, h] = [2.2, 0.9, 0.8] }) {
    const M = materials(), g = new THREE.Group();
    const arm = 0.16, back = 0.2, seatZ = 0.42;
    legs4(g, w - 0.1, d - 0.1, 0.04, 0.08, 0.018, M.blackMetal);
    g.add(rbox(w, d, seatZ - 0.08 - 0.12, 0.03, M.fabric, 0, 0, 0.08));
    g.add(rbox(arm, d, 0.6 - 0.08, 0.05, M.fabric, -w / 2 + arm / 2, 0, 0.08));
    g.add(rbox(arm, d, 0.6 - 0.08, 0.05, M.fabric, w / 2 - arm / 2, 0, 0.08));
    g.add(rbox(w - 2 * arm, back, h - 0.08, 0.05, M.fabric, 0, d / 2 - back / 2, 0.08));
    const n = w > 2.3 ? 3 : 2, cw = (w - 2 * arm) / n;
    for (let i = 0; i < n; i++) {
      const cx = -w / 2 + arm + cw * (i + 0.5);
      g.add(rbox(cw - 0.012, d - back - 0.02, 0.14, 0.05, M.fabric, cx, -back / 2 + 0.01, seatZ - 0.12));
      const bc = rbox(cw - 0.03, 0.17, 0.42, 0.07, M.fabric, cx, d / 2 - back - 0.07, seatZ + 0.01);
      bc.rotation.x = -0.12;
      g.add(bc);
    }
    for (const [sx, m] of [[-1, M.fabricSage], [1, M.fabricTaupe]]) {
      const p = rbox(0.42, 0.13, 0.42, 0.07, m, sx * (w / 2 - arm - 0.28), d / 2 - back - 0.2, seatZ + 0.02);
      p.rotation.set(-0.25, 0, sx * 0.12);
      g.add(p);
    }
    return g;
  },

  coffeeTable({ size: [w, d, h] = [0.8, 1.4, 0.4] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, 0.035, 0.012, M.oak, 0, 0, h - 0.035));
    g.add(rbox(w - 0.16, d - 0.16, h - 0.035, 0.01, M.lacquer, 0, 0, 0));
    vase(g, 0.12, -d * 0.18, h, 0.9);
    books(g, -0.2, 0.05, d * 0.15, h, 0.24, 4);
    return g;
  },

  tvConsole({ size: [w, d, h] = [2.2, 0.42, 0.45] }) {
    const M = materials(), g = new THREE.Group();
    const z0 = 0.18;
    g.add(rbox(w, d, h, 0.008, M.oak, 0, 0, z0));
    const n = Math.max(2, Math.round(w / 0.6));
    for (let i = 1; i < n; i++) g.add(box([-w / 2 + (w * i) / n - 0.002, -w / 2 + (w * i) / n + 0.002, -d / 2 - 0.001, -d / 2 + 0.01, z0 + 0.01, z0 + h - 0.01], M.blackMetal));
    // wall-mounted TV
    const tw = Math.min(1.45, w * 0.75), th = tw * 0.5625;
    g.add(rbox(tw, 0.035, th, 0.006, M.screen, 0, d / 2 - 0.02, 1.0));
    vase(g, -w / 2 + 0.2, 0, z0 + h);
    books(g, w / 2 - 0.4, w / 2 - 0.12, 0, z0 + h, 0.2, 9);
    return g;
  },

  floorLamp() {
    const M = materials(), g = new THREE.Group();
    g.add(cyl(0.16, 0.17, 0.025, M.blackMetal, 0, 0, 0));
    g.add(cyl(0.012, 0.012, 1.35, M.blackMetal, 0, 0, 0.02));
    g.add(cyl(0.17, 0.21, 0.3, M.lampShade, 0, 0, 1.3, 32));
    g.userData.lights = [[0, 0, 1.4]];
    return g;
  },

  plant({ opts: { scale = 1 } = {}, id, room }) {
    // Outdoor / large → tall potted plant; indoor → broad-leaf plant. Height follows the spec scale.
    const big = scale >= 1.15 || ['terrace', 'yard', 'porch'].includes(room);
    const real = placeModel(big ? 'potted_plant_01' : 'potted_plant_02', big ? 1.1 * scale : 0.85 * scale);
    if (real) {
      const g = new THREE.Group();
      real.rotation.z = [...id].reduce((a, c) => a + c.charCodeAt(0), 0) % 6.28;
      g.add(real);
      return g;
    }
    const M = materials(), g = new THREE.Group();
    const rand = rng([...id].reduce((a, c) => a + c.charCodeAt(0), 0));
    const s = scale;
    g.add(cyl(0.17 * s, 0.13 * s, 0.38 * s, M.ceramic, 0, 0, 0, 28));
    g.add(cyl(0.155 * s, 0.155 * s, 0.01, M.soil, 0, 0, 0.36 * s));
    const leafG = new THREE.SphereGeometry(1, 10, 6);
    for (let i = 0; i < 16; i++) {
      const a = rand() * Math.PI * 2, r = 0.05 + rand() * 0.12, z = 0.45 + rand() * 0.6;
      const leaf = new THREE.Mesh(leafG, rand() > 0.5 ? M.leaf : M.leafDark);
      leaf.scale.set(0.11 * s, 0.035 * s, 0.24 * s);
      leaf.position.set(Math.cos(a) * r * s, Math.sin(a) * r * s, z * s);
      leaf.rotation.set(rand() * 0.8 - 0.4, rand() * 0.9 - 0.45, a);
      leaf.castShadow = true;
      g.add(leaf);
      const stem = cyl(0.004, 0.006, z * s - 0.36 * s, M.leafDark, Math.cos(a) * r * s * 0.5, Math.sin(a) * r * s * 0.5, 0.36 * s, 5);
      g.add(stem);
    }
    return g;
  },

  diningTable({ size: [w, d, h] = [2.0, 0.95, 0.75] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, 0.045, 0.015, M.oak, 0, 0, h - 0.045));
    for (const sx of [-1, 1]) {
      g.add(rbox(0.07, d - 0.2, 0.06, 0.01, M.oak, sx * (w / 2 - 0.28), 0, h - 0.1));
      for (const sy of [-1, 1]) {
        const leg = rbox(0.06, 0.06, h - 0.045, 0.01, M.oak, sx * (w / 2 - 0.28), sy * (d / 2 - 0.12), 0);
        g.add(leg);
      }
    }
    g.add(box([-w / 2 + 0.28, w / 2 - 0.28, -0.02, 0.02, h - 0.12, h - 0.06], M.oak));
    g.add(rbox(0.5, 0.25, 0.02, 0.01, M.walnut, 0, 0, h));
    vase(g, -0.08, 0, h + 0.02, 1);
    vase(g, 0.12, 0.02, h + 0.02, 0.7);
    return g;
  },

  chair() {
    const M = materials(), g = new THREE.Group();
    const w = 0.46, d = 0.48;
    legs4(g, w, d, 0.04, 0.44, 0.017, M.oak);
    g.add(rbox(w, d, 0.04, 0.01, M.oak, 0, 0, 0.42));
    g.add(rbox(w - 0.04, d - 0.06, 0.05, 0.02, M.fabric, 0, -0.01, 0.455));
    for (const sx of [-1, 1]) g.add(cyl(0.015, 0.015, 0.42, M.oak, sx * (w / 2 - 0.04), d / 2 - 0.04, 0.44, 10));
    const back = rbox(w, 0.035, 0.2, 0.012, M.oak, 0, d / 2 - 0.03, 0.66);
    back.rotation.x = 0.08;
    g.add(back);
    return g;
  },

  kitchen({ size: [w, d, h] = [4, 0.6, 0.9], opts: { sinkAt = 1, hobAt = -0.2 } = {} }) {
    const M = materials(), g = new THREE.Group();
    const plinth = 0.1, top = 0.04;
    g.add(box([-w / 2, w / 2, -d / 2 + 0.06, d / 2, 0, plinth], M.blackMetal));
    const n = Math.round(w / 0.6), dw = w / n;
    const body = h - plinth - top, drawer = 0.17;
    g.add(box([-w / 2, w / 2, -d / 2 + 0.03, d / 2, plinth, h - top], M.matteWhite)); // carcass
    for (let i = 0; i < n; i++) {
      const x = -w / 2 + dw * (i + 0.5);
      const isOven = Math.abs(x - hobAt) < dw / 2;
      // fronts: 3 mm gaps, 18 mm thick
      g.add(rbox(dw - 0.006, 0.018, drawer - 0.006, 0.003, M.oak, x, -d / 2 + 0.02, h - top - drawer));
      g.add(box([x - 0.12, x + 0.12, -d / 2 - 0.012, -d / 2 + 0.011, h - top - 0.06, h - top - 0.05], M.steel));
      if (isOven) {
        const oh = body - drawer - 0.02;
        g.add(rbox(dw - 0.02, 0.02, oh, 0.004, M.blackGlass, x, -d / 2 + 0.02, plinth + 0.01));
        g.add(box([x - dw / 2 + 0.04, x + dw / 2 - 0.04, -d / 2 + 0.008, -d / 2 + 0.012, plinth + oh - 0.08, plinth + oh - 0.03], M.steel));
        g.add(box([x - 0.2, x + 0.2, -d / 2 - 0.03, -d / 2 + 0.01, plinth + oh - 0.06, plinth + oh - 0.045], M.steel));
      } else {
        g.add(rbox(dw - 0.006, 0.018, body - drawer - 0.006, 0.003, M.oak, x, -d / 2 + 0.02, plinth));
        g.add(box([x - 0.12, x + 0.12, -d / 2 - 0.012, -d / 2 + 0.011, plinth + body - drawer - 0.06, plinth + body - drawer - 0.05], M.steel));
      }
    }
    g.add(rbox(w + 0.02, d + 0.03, top, 0.004, M.quartz, 0, -0.015, h - top));
    // backsplash
    g.add(box([-w / 2, w / 2, d / 2 - 0.012, d / 2, h, 1.45], M.quartz));
    // sink (inset basin + tap)
    g.add(box([sinkAt - 0.38, sinkAt + 0.38, -0.2, 0.2, h - 0.001, h + 0.004], M.steel));
    g.add(box([sinkAt - 0.34, sinkAt + 0.34, -0.17, 0.17, h + 0.004, h + 0.008], new THREE.MeshStandardMaterial({ color: 0x77797b, metalness: 1, roughness: 0.4 })));
    g.add(cyl(0.018, 0.022, 0.32, M.steel, sinkAt, 0.24, h));
    const spout = new THREE.Mesh(new THREE.TorusGeometry(0.09, 0.012, 10, 24, Math.PI), M.steel);
    spout.rotation.set(Math.PI / 2, 0, Math.PI / 2);
    spout.position.set(sinkAt, 0.15, h + 0.32);
    g.add(spout);
    // induction hob
    g.add(box([hobAt - 0.3, hobAt + 0.3, -0.24, 0.24, h, h + 0.006], M.blackGlass));
    for (const [ox, oy, r] of [[-0.14, -0.1, 0.09], [0.14, -0.1, 0.07], [-0.14, 0.11, 0.07], [0.14, 0.11, 0.09]]) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(r, 0.003, 6, 40), M.steel);
      ring.position.set(hobAt + ox, oy, h + 0.007);
      g.add(ring);
    }
    // hood
    g.add(rbox(0.75, 0.48, 0.12, 0.01, M.steel, hobAt, d / 2 - 0.26, 1.62));
    g.add(box([hobAt - 0.14, hobAt + 0.14, d / 2 - 0.3, d / 2 - 0.02, 1.74, 2.4], M.steel));
    // wall cabinets (skip over hood)
    const ud = 0.35;
    for (let i = 0; i < n; i++) {
      const x = -w / 2 + dw * (i + 0.5);
      if (Math.abs(x - hobAt) < 0.5) continue;
      g.add(rbox(dw - 0.004, ud, 0.72, 0.004, M.lacquer, x, d / 2 - ud / 2, 1.55));
    }
    // a few objects
    g.add(rbox(0.38, 0.26, 0.02, 0.008, M.walnut, -w / 2 + 0.5, 0, h));
    vase(g, -w / 2 + 0.95, 0.12, h, 0.8);
    return g;
  },

  fridge({ size: [w, d, h] = [0.7, 0.7, 1.85] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, h, 0.02, M.steel, 0, 0, 0));
    g.add(box([-w / 2 + 0.01, w / 2 - 0.01, -d / 2 - 0.002, -d / 2 + 0.001, h * 0.62, h * 0.62 + 0.006], M.blackMetal));
    for (const [z1, z2] of [[h * 0.3, h * 0.55], [h * 0.68, h * 0.95]]) {
      g.add(box([w / 2 - 0.08, w / 2 - 0.06, -d / 2 - 0.05, -d / 2 - 0.03, z1, z2], M.steel));
      g.add(box([w / 2 - 0.08, w / 2 - 0.06, -d / 2 - 0.05, -d / 2, z1, z1 + 0.015], M.steel));
      g.add(box([w / 2 - 0.08, w / 2 - 0.06, -d / 2 - 0.05, -d / 2, z2 - 0.015, z2], M.steel));
    }
    return g;
  },

  washer({ size: [w, d, h] = [0.6, 0.6, 0.85] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, h, 0.03, M.lacquer, 0, 0, 0));
    g.add(box([-w / 2 + 0.03, w / 2 - 0.03, -d / 2 - 0.004, -d / 2 + 0.01, h - 0.12, h - 0.03], M.matteWhite));
    g.add(box([w / 2 - 0.2, w / 2 - 0.06, -d / 2 - 0.006, -d / 2, h - 0.1, h - 0.05], M.blackGlass));
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.2, 0.025, 12, 40), M.steel);
    ring.rotation.x = Math.PI / 2;
    ring.position.set(0, -d / 2 - 0.01, 0.38);
    g.add(ring);
    const door = new THREE.Mesh(new THREE.CircleGeometry(0.19, 40), M.blackGlass);
    door.rotation.x = Math.PI / 2;
    door.position.set(0, -d / 2 - 0.012, 0.38);
    g.add(door);
    return g;
  },

  laundrySink({ size: [w, d, h] = [0.6, 0.55, 0.85] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, h - 0.2, 0.01, M.matteWhite, 0, 0, 0));
    g.add(rbox(w, d, 0.2, 0.03, M.porcelain, 0, 0, h - 0.2));
    g.add(box([-w / 2 + 0.05, w / 2 - 0.05, -d / 2 + 0.05, d / 2 - 0.08, h - 0.001, h + 0.002], new THREE.MeshStandardMaterial({ color: 0xcfd2d4, roughness: 0.3 })));
    g.add(cyl(0.014, 0.014, 0.22, M.steel, 0, d / 2 - 0.04, h));
    return g;
  },

  bed({ size: [w, d, h] = [1.6, 2.0, 0.5] }) {
    const M = materials(), g = new THREE.Group();
    const headD = 0.1;
    g.add(rbox(w + 0.06, d, 0.28, 0.02, M.fabricTaupe, 0, 0, 0.06));
    legs4(g, w, d - 0.1, 0.06, 0.06, 0.02, M.oak);
    g.add(rbox(w + 0.2, headD, 1.05, 0.03, M.fabricTaupe, 0, d / 2 - headD / 2, 0.06));
    const md = d - headD - 0.02;
    g.add(rbox(w, md, 0.2, 0.05, M.linen, 0, -headD / 2, 0.34));
    // duvet over the lower 2/3 + throw at the foot
    g.add(rbox(w + 0.05, md * 0.72, 0.07, 0.035, M.linen, 0, -d / 2 + (md * 0.72) / 2 - 0.01, 0.5));
    g.add(rbox(w + 0.08, 0.5, 0.08, 0.035, M.fabricGrey, 0, -d / 2 + 0.3, 0.5));
    const n = w > 1.7 ? 3 : 2, pw = Math.min(0.62, (w - 0.1) / n);
    for (let i = 0; i < n; i++) {
      const p = rbox(pw - 0.04, 0.36, 0.14, 0.06, M.linen, -w / 2 + 0.05 + pw * (i + 0.5), d / 2 - headD - 0.24, 0.53);
      p.rotation.x = -0.35;
      g.add(p);
    }
    for (const [sx, m] of [[-0.18, M.fabricSage], [0.18, M.fabricGrey]]) {
      const c = rbox(0.4, 0.12, 0.34, 0.06, m, sx, d / 2 - headD - 0.48, 0.55);
      c.rotation.set(-0.35, 0, sx * 0.4);
      g.add(c);
    }
    return g;
  },

  nightstand() {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(0.45, 0.4, 0.45, 0.01, M.oak, 0, 0, 0.08));
    legs4(g, 0.4, 0.35, 0.03, 0.08, 0.012, M.blackMetal);
    g.add(box([-0.2, 0.2, -0.202, -0.198, 0.3, 0.304], M.blackMetal));
    lamp(g, 0.05, 0.04, 0.53, 0.45, true);
    return g;
  },

  wardrobe({ size: [w, d, h] = [1.8, 0.6, 2.2] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d - 0.02, h, 0.006, M.matteWhite, 0, 0.01, 0));
    const n = Math.max(2, Math.round(w / 0.48)), dw = w / n;
    for (let i = 0; i < n; i++) {
      const x = -w / 2 + dw * (i + 0.5);
      g.add(rbox(dw - 0.004, 0.02, h - 0.04, 0.003, M.oak, x, -d / 2 + 0.01, 0.02));
      const hx = i % 2 ? x - dw / 2 + 0.04 : x + dw / 2 - 0.04;
      g.add(box([hx - 0.006, hx + 0.006, -d / 2 - 0.025, -d / 2, 0.9, 1.25], M.blackMetal));
    }
    return g;
  },

  desk({ size: [w, d, h] = [1.2, 0.55, 0.75] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, 0.03, 0.006, M.oak, 0, 0, h - 0.03));
    for (const sx of [-1, 1]) g.add(box([sx * (w / 2 - 0.03) - 0.012, sx * (w / 2 - 0.03) + 0.012, -d / 2 + 0.03, d / 2 - 0.03, 0, h - 0.03], M.blackMetal));
    // laptop + lamp + books
    const lap = rbox(0.33, 0.23, 0.012, 0.004, M.steel, -0.05, 0, h);
    g.add(lap);
    const scr = rbox(0.33, 0.008, 0.22, 0.004, M.steel, -0.05, 0.115, h + 0.01);
    scr.rotation.x = -0.25;
    g.add(scr);
    if (w > 0.9) { lamp(g, w / 2 - 0.16, 0.1, h, 0.38); books(g, -w / 2 + 0.08, -w / 2 + 0.3, 0.1, h, 0.2, 3); }
    if (w > 1.5) {
      const succ = placeModel('potted_plant_04', 0.22);
      if (succ) { succ.position.set(-w / 2 + 0.45, 0.12, h); g.add(succ); }
    }
    return g;
  },

  deskChair() {
    const M = materials(), g = new THREE.Group();
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2;
      const leg = box([0, 0.27, -0.015, 0.015, 0.05, 0.075], M.blackMetal);
      leg.position.set(Math.cos(a) * 0.135, Math.sin(a) * 0.135, 0.06);
      leg.rotation.z = a;
      g.add(leg);
      g.add(cyl(0.025, 0.025, 0.05, M.blackMetal, Math.cos(a) * 0.27, Math.sin(a) * 0.27, 0, 10));
    }
    g.add(cyl(0.025, 0.025, 0.36, M.blackMetal, 0, 0, 0.08));
    g.add(rbox(0.5, 0.48, 0.08, 0.035, M.fabricGrey, 0, 0, 0.44));
    const back = rbox(0.46, 0.06, 0.48, 0.03, M.fabricGrey, 0, 0.23, 0.55);
    back.rotation.x = 0.12;
    g.add(back);
    return g;
  },

  toilet() {
    const M = materials(), g = new THREE.Group();
    // concealed-cistern style: slim tank against the wall, rounded pan
    g.add(rbox(0.4, 0.16, 0.8, 0.03, M.porcelain, 0, 0.25, 0));
    g.add(box([-0.09, 0.09, 0.165, 0.175, 0.7, 0.76], M.steel));
    const pan = new THREE.Mesh(new THREE.SphereGeometry(1, 28, 16), M.porcelain);
    pan.scale.set(0.19, 0.28, 0.2);
    pan.position.set(0, -0.06, 0.24);
    pan.castShadow = true;
    g.add(pan);
    g.add(cyl(0.13, 0.15, 0.24, M.porcelain, 0, 0.02, 0));
    const seat = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 32).rotateX(Math.PI / 2), M.porcelain);
    seat.scale.set(0.19, 0.27, 0.03);
    seat.position.set(0, -0.06, 0.44);
    seat.castShadow = true;
    g.add(seat);
    return g;
  },

  vanity({ size: [w, d, h] = [0.6, 0.46, 0.85] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, 0.36, 0.006, M.oak, 0, 0, h - 0.4));
    g.add(rbox(w + 0.02, d + 0.01, 0.025, 0.004, M.quartz, 0, 0, h - 0.04));
    const bw = Math.min(0.46, w - 0.08);
    g.add(rbox(bw, 0.34, 0.12, 0.05, M.porcelain, 0, -0.02, h - 0.015));
    g.add(cyl(0.014, 0.014, 0.25, M.steel, 0, d / 2 - 0.05, h - 0.015));
    g.add(box([-0.012, 0.012, d / 2 - 0.16, d / 2 - 0.05, h + 0.21, h + 0.235], M.steel));
    // mirror cabinet
    g.add(rbox(w * 0.92, 0.12, 0.75, 0.01, M.matteWhite, 0, d / 2 - 0.06, h + 0.38));
    g.add(box([-w * 0.44, w * 0.44, d / 2 - 0.125, d / 2 - 0.118, h + 0.4, h + 1.11], M.mirror));
    return g;
  },

  rug({ size: [w, d] = [2, 3], id }) {
    const seed = [...id].reduce((a, c) => a + c.charCodeAt(0), 0);
    const mat = new THREE.MeshStandardMaterial({ map: T.rug(seed, 34, 80), roughness: 1 });
    const g = new THREE.Group();
    g.add(box([-w / 2, w / 2, -d / 2, d / 2, 0.003, 0.015], mat, { uvWorld: false }));
    g.children[0].castShadow = false;
    return g;
  },

  shelf({ size: [w, d, h] = [2.4, 0.36, 0.9] }) {
    const M = materials(), g = new THREE.Group();
    g.add(rbox(w, d, 0.025, 0.005, M.oak, 0, 0, h - 0.025));
    g.add(rbox(w, d, 0.06, 0.005, M.oak, 0, 0, 0));
    const n = Math.round(w / 0.6);
    for (let i = 0; i <= n; i++) {
      const x = -w / 2 + (w * i) / n;
      g.add(box([x - (i === 0 ? 0 : 0.01), x + (i === n ? 0 : 0.01), -d / 2, d / 2, 0.06, h - 0.025], M.oak));
    }
    g.add(box([-w / 2, w / 2, -d / 2, d / 2, h / 2 - 0.01, h / 2 + 0.01], M.oak));
    for (let i = 0; i < n; i++) {
      const x1 = -w / 2 + (w * i) / n + 0.03, x2 = -w / 2 + (w * (i + 1)) / n - 0.03;
      books(g, x1, i % 2 ? (x1 + x2) / 2 : x2, 0, 0.06, 0.24, 11 + i);
      if (i % 2) vase(g, (x1 + x2) / 2 + 0.12, 0, h / 2 + 0.01, 0.8);
    }
    vase(g, -w / 2 + 0.3, 0, h, 1.1);
    return g;
  },

  car({ size: [w, l, h] = [1.82, 4.5, 1.45], opts: { color = 'white' } = {} }) {
    const M = materials(), g = new THREE.Group();
    const paint = color === 'white' ? M.paintWhite : M.paintGraphite;
    const r = 0.33, ax = l / 2 - 0.82, arch = r + 0.05;
    // side profile (u = along the car, front at -l/2; v = height), extruded across the width
    const sideToCar = new THREE.Matrix4().set(0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1);
    const extrude = (shape, width, bevel) => {
      const geo = new THREE.ExtrudeGeometry(shape, { depth: width - 2 * bevel, bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 4, curveSegments: 18 });
      geo.applyMatrix4(sideToCar);
      geo.translate(-(width - 2 * bevel) / 2, 0, 0);
      geo.computeVertexNormals();
      return geo;
    };
    const body = new THREE.Shape();
    const u0 = -l / 2, u1 = l / 2;
    body.moveTo(u0 + 0.12, 0.24);
    body.lineTo(-ax - arch, 0.24);
    body.absarc(-ax, r, arch, Math.PI, 0, true);
    body.lineTo(ax - arch, 0.24);
    body.absarc(ax, r, arch, Math.PI, 0, true);
    body.lineTo(u1 - 0.1, 0.24);
    body.quadraticCurveTo(u1 + 0.02, 0.3, u1, 0.62);
    body.quadraticCurveTo(u1 - 0.02, 0.86, u1 - 0.32, 0.9);
    body.lineTo(-l / 2 + 1.25, 0.86);
    body.quadraticCurveTo(u0 + 0.25, 0.8, u0 + 0.02, 0.66);
    body.quadraticCurveTo(u0 - 0.03, 0.4, u0 + 0.12, 0.24);
    const bodyMesh = new THREE.Mesh(extrude(body, w, 0.06), paint);
    const cabin = new THREE.Shape();
    cabin.moveTo(-l / 2 + 1.25, 0.85);
    cabin.quadraticCurveTo(-l / 2 + 1.75, 1.33, -0.25, h - 0.02);
    cabin.lineTo(0.75, h - 0.03);
    cabin.quadraticCurveTo(1.2, h - 0.08, u1 - 0.7, 0.9);
    cabin.lineTo(-l / 2 + 1.25, 0.85);
    const cabinMesh = new THREE.Mesh(extrude(cabin, w - 0.2, 0.05), M.tint);
    // painted roof skin + pillars over the glass
    const roof = rbox(w - 0.3, 1.0, 0.03, 0.015, paint, 0, 0.25, h - 0.06);
    const bPillar = box([-(w - 0.2) / 2 - 0.006, (w - 0.2) / 2 + 0.006, 0.12, 0.2, 0.9, h - 0.05], paint);
    for (const m of [bodyMesh, cabinMesh]) { m.castShadow = true; m.receiveShadow = true; g.add(m); }
    g.add(roof, bPillar);
    for (const sx of [-1, 1]) {
      for (const sy of [-1, 1]) {
        const wheel = new THREE.Group();
        const tyre = new THREE.Mesh(new THREE.TorusGeometry(r - 0.07, 0.075, 12, 32), M.tyre);
        tyre.rotation.y = Math.PI / 2;
        const rim = new THREE.Mesh(new THREE.CylinderGeometry(r - 0.11, r - 0.11, 0.16, 28), M.steel);
        rim.rotation.z = Math.PI / 2;
        wheel.add(tyre, rim);
        for (let k = 0; k < 5; k++) {
          const sp = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.04, (r - 0.11) * 1.8), M.blackMetal);
          sp.position.x = sx * 0.085;
          sp.rotation.x = (k / 5) * Math.PI;
          wheel.add(sp);
        }
        wheel.position.set(sx * (w / 2 - 0.13), sy * ax, r);
        wheel.traverse((o) => { if (o.isMesh) o.castShadow = true; });
        g.add(wheel);
      }
      g.add(box([sx * (w / 2 - 0.28) - 0.2, sx * (w / 2 - 0.28) + 0.2, u0 - 0.004, u0 + 0.06, 0.6, 0.66], M.headLight));
      g.add(box([sx * (w / 2 - 0.25) - 0.22, sx * (w / 2 - 0.25) + 0.22, u1 - 0.07, u1 + 0.004, 0.68, 0.74], M.tailLight));
      g.add(rbox(0.1, 0.16, 0.09, 0.03, paint, sx * (w / 2 + 0.03), -l / 2 + 1.35, 0.9));
    }
    g.add(box([-0.3, 0.3, u0 - 0.006, u0 + 0.02, 0.36, 0.48], M.blackMetal)); // grille
    return g;
  },

  // Shower = drain + glass screens + rain head, from world-space rect (no rotation).
  shower({ opts: { rect: [x1, x2, y1, y2], screen = [], head = 'W' } }) {
    const M = materials(), g = new THREE.Group();
    g.userData.worldSpace = true;
    g.add(box([x1 + 0.08, x2 - 0.08, (y1 + y2) / 2 - 0.03, (y1 + y2) / 2 + 0.03, 0.001, 0.016], M.steel));
    for (const [ax, ay, bx, by] of screen) {
      const horiz = Math.abs(by - ay) < 1e-6;
      const b = horiz ? [Math.min(ax, bx), Math.max(ax, bx), ay - 0.005, ay + 0.005] : [ax - 0.005, ax + 0.005, Math.min(ay, by), Math.max(ay, by)];
      g.add(box([...b, 0.02, 2.0], M.glass));
      g.add(box(horiz ? [b[0], b[1], ay - 0.012, ay + 0.012, 1.98, 2.0] : [ax - 0.012, ax + 0.012, b[2], b[3], 1.98, 2.0], M.blackMetal));
      g.add(box(horiz ? [b[0], b[1], ay - 0.012, ay + 0.012, 0.0, 0.02] : [ax - 0.012, ax + 0.012, b[2], b[3], 0, 0.02], M.blackMetal));
    }
    const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
    const at = { W: [x1, cy, 1, 0], E: [x2, cy, -1, 0], N: [cx, y2, 0, -1], S: [cx, y1, 0, 1] }[head];
    const [hx, hy, dx, dy] = at;
    const arm = box([Math.min(hx, hx + dx * 0.35), Math.max(hx, hx + dx * 0.35) + (dx ? 0 : 0.015), Math.min(hy, hy + dy * 0.35), Math.max(hy, hy + dy * 0.35) + (dy ? 0 : 0.015), 2.08, 2.1], M.blackMetal);
    g.add(arm);
    g.add(cyl(0.12, 0.12, 0.012, M.blackMetal, hx + dx * 0.35, hy + dy * 0.35, 2.06, 32));
    g.add(box([hx + dx * 0.005 - 0.05, hx + dx * 0.005 + 0.05, hy + dy * 0.005 - 0.05, hy + dy * 0.005 + 0.05, 1.05, 1.15], M.blackMetal));
    return g;
  },
};

export function buildFurniture(item, floorZ) {
  const b = BUILDERS[item.type];
  if (!b) throw new Error(`no builder for ${item.type}`);
  const g = b(item);
  if (g.userData.worldSpace) {
    g.position.set(0, 0, floorZ);
  } else {
    g.position.set(item.at[0], item.at[1], floorZ);
    g.rotation.z = THREE.MathUtils.degToRad(item.rot || 0);
  }
  return g;
}
