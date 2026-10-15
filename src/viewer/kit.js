// Shared PBR materials and geometry helpers. Spec/local space is Z-up metres.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import * as T from './textures.js';
import { assets } from './assets.js';

// UVs in metres, projected along the dominant normal axis, so textures keep real scale on any box.
export function worldUV(geom, offset = [0, 0, 0]) {
  const pos = geom.attributes.position, nor = geom.attributes.normal;
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i) + offset[0], y = pos.getY(i) + offset[1], z = pos.getZ(i) + offset[2];
    const ax = Math.abs(nor.getX(i)), ay = Math.abs(nor.getY(i)), az = Math.abs(nor.getZ(i));
    if (az >= ax && az >= ay) { uv[i * 2] = x; uv[i * 2 + 1] = y; }
    else if (ax >= ay) { uv[i * 2] = y; uv[i * 2 + 1] = z; }
    else { uv[i * 2] = x; uv[i * 2 + 1] = z; }
  }
  geom.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  return geom;
}

let M = null;
export function materials() {
  if (M) return M;
  const std = (o) => new THREE.MeshStandardMaterial(o);
  const phys = (o) => new THREE.MeshPhysicalMaterial(o);
  const oakF = T.oakFloor();
  const tile = T.tiles({ tileW: 1.2, tileH: 0.6, metres: 2.4, base: [36, 16, 84], seed: 3 });
  const bath = T.tiles({ tileW: 0.6, tileH: 0.3, metres: 1.2, base: [40, 4, 76], vary: 2, grout: '#a9a7a2', veins: 0.07, seed: 8 });
  const pav = T.pavers();
  const roofTiles = T.tiles({ tileW: 0.6, tileH: 0.6, metres: 2.4, base: [34, 10, 83], vary: 3, grout: '#bdb5aa', veins: 0.03, seed: 23 });
  const plaster = T.plaster();
  const fab = T.fabric(5);
  const oakV = T.woodVeneer(9, 62);
  const darkV = T.woodVeneer(4, 38);
  const qz = T.quartz();

  M = {
    wall: std({ color: 0xf1ece4, roughness: 0.93, map: plaster }),
    cap: std({ color: 0x2b2a29, roughness: 0.85 }),
    tile: std({ color: 0xffffff, map: tile.map, bumpMap: tile.bumpMap, bumpScale: 0.6, roughness: 0.32 }),
    oakfloor: std({ color: 0xffffff, map: oakF.map, bumpMap: oakF.bumpMap, bumpScale: 0.6, roughness: 0.55 }),
    bathtile: std({ color: 0xffffff, map: bath.map, bumpMap: bath.bumpMap, bumpScale: 0.6, roughness: 0.28 }),
    concrete: std({ color: 0xffffff, map: pav.map, bumpMap: pav.bumpMap, bumpScale: 0.8, roughness: 0.92 }),
    roof: std({ color: 0xffffff, map: roofTiles.map, bumpMap: roofTiles.bumpMap, bumpScale: 0.7, roughness: 0.85 }),
    oak: std({ color: 0xffffff, map: oakV, roughness: 0.5 }),
    walnut: std({ color: 0xffffff, map: darkV, roughness: 0.45 }),
    lacquer: std({ color: 0xefede8, roughness: 0.42 }),
    matteWhite: std({ color: 0xf4f2ee, roughness: 0.7 }),
    quartz: phys({ color: 0xffffff, map: qz, roughness: 0.22, clearcoat: 0.3, clearcoatRoughness: 0.2 }),
    fabric: std({ color: 0xd9cfbf, map: fab, roughness: 0.95 }),
    fabricGrey: std({ color: 0x9a958e, map: fab, roughness: 0.95 }),
    fabricSage: std({ color: 0x8d9a80, map: fab, roughness: 0.95 }),
    fabricTaupe: std({ color: 0xb4a490, map: fab, roughness: 0.95 }),
    linen: std({ color: 0xf6f3ec, map: fab, roughness: 0.95 }),
    curtain: phys({ color: 0xf2eee6, map: fab, roughness: 0.95, side: THREE.DoubleSide, transparent: true, opacity: 0.88 }),
    leather: std({ color: 0x6b4a33, roughness: 0.55 }),
    steel: std({ color: 0xc4c7ca, metalness: 1, roughness: 0.32 }),
    blackMetal: std({ color: 0x222222, metalness: 0.6, roughness: 0.45 }),
    frame: std({ color: 0x3b3b3c, metalness: 0.5, roughness: 0.45 }),
    porcelain: phys({ color: 0xfbfbf9, roughness: 0.1, clearcoat: 1, clearcoatRoughness: 0.05 }),
    ceramic: std({ color: 0xe5ded3, roughness: 0.6 }),
    terracotta: std({ color: 0xb98a6a, roughness: 0.8 }),
    mirror: std({ color: 0xe8ecee, metalness: 1, roughness: 0.03 }),
    glass: phys({ color: 0xe2eff2, metalness: 0, roughness: 0.04, transparent: true, opacity: 0.2, depthWrite: false, envMapIntensity: 1.5 }),
    screen: std({ color: 0x0c0d0f, roughness: 0.18, metalness: 0.2 }),
    blackGlass: std({ color: 0x111214, roughness: 0.12, metalness: 0.3 }),
    leaf: std({ color: 0x4f6e3d, roughness: 0.75, side: THREE.DoubleSide }),
    leafDark: std({ color: 0x3b5730, roughness: 0.75, side: THREE.DoubleSide }),
    soil: std({ color: 0x3a2e25, roughness: 1 }),
    lampShade: std({ color: 0xfff4e2, roughness: 0.9, emissive: 0xffd9a0, emissiveIntensity: 0.9 }),
    paintGraphite: phys({ color: 0x3a3e44, metalness: 0.6, roughness: 0.35, clearcoat: 1, clearcoatRoughness: 0.08 }),
    paintWhite: phys({ color: 0xeeeeec, metalness: 0.2, roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.08 }),
    tyre: std({ color: 0x1b1b1b, roughness: 0.9 }),
    tint: phys({ color: 0x1d252b, roughness: 0.06, metalness: 0.3, clearcoat: 1 }),
    tailLight: std({ color: 0x8a1010, emissive: 0x6a0808, roughness: 0.3 }),
    headLight: std({ color: 0xf4f4f4, emissive: 0x888888, roughness: 0.2 }),
    books: [0x8c6f56, 0x3f4d5a, 0xc9b79a, 0x6b7461, 0xa45a3c, 0xdedad2].map((c) => std({ color: c, roughness: 0.8 })),
  };
  // Photo-scanned CC0 PBR sets replace the procedural maps when they loaded (see assets.js).
  // `map: false` keeps our own colour and borrows only the surface (normal + roughness + AO).
  const P = (id, { map = true, normal = 1, ao = 0.6, ...rest } = {}) => {
    const t = assets.tex[id];
    if (!t) return null;
    return std({
      color: 0xffffff, ...(map ? { map: t.map } : {}),
      normalMap: t.normalMap, normalScale: new THREE.Vector2(normal, normal),
      roughnessMap: t.arm, roughness: 1, aoMap: t.arm, aoMapIntensity: ao, ...rest,
    });
  };
  const upgrade = {
    wall: P('painted_plaster_wall', { map: false, color: 0xf2ede6, normal: 0.35, ao: 0.3 }),
    oakfloor: P('laminate_floor_02', { normal: 0.6 }),
    oak: P('oak_veneer_01', { normal: 0.5, roughness: 0.85 }),
    walnut: P('natural_walnut_veneer', { normal: 0.5, color: 0xb59b80 }),
    quartz: P('marble_01', { normal: 0.25, roughnessMap: null, roughness: 0.22, color: 0xf7f4ef }),
    fabric: P('poly_wool_herringbone', { map: false, color: 0xd9cfbf, normal: 1.2 }),
    fabricGrey: P('rough_linen', { map: false, color: 0x9a958e, normal: 1.2 }),
    fabricSage: P('rough_linen', { map: false, color: 0x8d9a80, normal: 1.2 }),
    fabricTaupe: P('poly_wool_herringbone', { map: false, color: 0xb4a490, normal: 1.2 }),
    linen: P('rough_linen', { map: false, color: 0xf6f3ec, normal: 1.0 }),
  };
  for (const [k, m] of Object.entries(upgrade)) if (m) M[k] = m;
  for (const k of ['lampShade', 'tailLight', 'headLight']) M[k].userData.lamp = true;
  return M;
}

// Box from min/max extents in local space, world-scaled UVs.
export function box([x1, x2, y1, y2, z1, z2], material, { uvWorld = true, cast = true } = {}) {
  const g = new THREE.BoxGeometry(x2 - x1, y2 - y1, z2 - z1);
  const c = [(x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2];
  if (uvWorld) worldUV(g, c);
  const m = new THREE.Mesh(g, material);
  m.position.set(...c);
  m.castShadow = cast && !isTransparent(material);
  m.receiveShadow = true;
  return m;
}

// Rounded box sized w×d×h with its base at z (centred on x,y).
export function rbox(w, d, h, r, material, x = 0, y = 0, z = 0, segs = 3) {
  const rr = Math.min(r, w / 2 - 1e-3, d / 2 - 1e-3, h / 2 - 1e-3);
  const g = new RoundedBoxGeometry(w, d, h, segs, Math.max(rr, 0.001));
  worldUV(g, [x, y, z + h / 2]);
  const m = new THREE.Mesh(g, material);
  m.position.set(x, y, z + h / 2);
  m.castShadow = !isTransparent(material);
  m.receiveShadow = true;
  return m;
}

// Z-axis cylinder with base at z.
export function cyl(rTop, rBot, h, material, x = 0, y = 0, z = 0, segs = 24) {
  const g = new THREE.CylinderGeometry(rTop, rBot, h, segs);
  g.rotateX(Math.PI / 2);
  const m = new THREE.Mesh(g, material);
  m.position.set(x, y, z + h / 2);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

export function isTransparent(m) {
  return Array.isArray(m) ? false : !!m.transparent;
}

export function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

// Collapse every mesh under `root` into one mesh per material (draw calls are the main cost:
// a sofa is ~20 boxes, the house ~3k). Lights and non-mesh children are kept as-is.
const _inv = new THREE.Matrix4(), _m = new THREE.Matrix4();
export function mergeByMaterial(root) {
  root.updateMatrixWorld(true);
  _inv.copy(root.matrixWorld).invert();
  const buckets = new Map(), meshes = [];
  const walk = (o) => {
    if (o.userData.noMerge) return; // loaded GLB models: shared + quantised geometry, keep as-is
    if (o.isMesh) meshes.push(o);
    o.children.forEach(walk);
  };
  walk(root);
  if (meshes.length < 2) return root;
  for (const mesh of meshes) {
    _m.multiplyMatrices(_inv, mesh.matrixWorld);
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const src = mesh.geometry;
    const groups = Array.isArray(mesh.material) && src.groups.length ? src.groups : [{ start: 0, count: src.index ? src.index.count : src.attributes.position.count, materialIndex: 0 }];
    for (const grp of groups) {
      const mat = mats[grp.materialIndex];
      let g = src.index ? subIndexed(src, grp.start, grp.count) : src.clone();
      if (!g.attributes.uv) g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
      for (const k of Object.keys(g.attributes)) if (!['position', 'normal', 'uv'].includes(k)) g.deleteAttribute(k);
      g.applyMatrix4(_m);
      const key = mat.uuid;
      if (!buckets.has(key)) buckets.set(key, { mat, geoms: [], cast: mesh.castShadow });
      buckets.get(key).geoms.push(g);
    }
    mesh.geometry.dispose();
  }
  for (const m of meshes) m.parent.remove(m);
  // drop now-empty helper groups
  const empties = [];
  root.traverse((o) => { if (o !== root && o.type === 'Group' && !o.children.length) empties.push(o); });
  empties.forEach((o) => o.parent?.remove(o));
  for (const { mat, geoms, cast } of buckets.values()) {
    const merged = mergeGeometries(geoms, false);
    geoms.forEach((g) => g.dispose());
    const mesh = new THREE.Mesh(merged, mat);
    mesh.castShadow = cast && !mat.transparent;
    mesh.receiveShadow = true;
    root.add(mesh);
  }
  return root;
}

function subIndexed(src, start, count) {
  const g = new THREE.BufferGeometry();
  const idx = src.index.array.slice(start, start + count);
  for (const k of ['position', 'normal', 'uv']) if (src.attributes[k]) g.setAttribute(k, src.attributes[k].clone());
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  return g.toNonIndexed();
}
