// Loads the CC0 assets in public/assets (Poly Haven; see docs/ASSETS.md).
// Every asset is optional: if one fails, the procedural fallback in kit.js / furniture.js is used.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { HDRLoader } from 'three/addons/loaders/HDRLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';

const BASE = `${import.meta.env.BASE_URL}assets`;

// Real-world size of one texture tile in metres (from the Poly Haven asset info), so worldUV maps 1:1.
export const TEXTURES = {
  laminate_floor_02: 1.7,
  painted_plaster_wall: 2.0,
  oak_veneer_01: 1.83,
  natural_walnut_veneer: 1.0,
  marble_01: 1.5,
  rough_linen: 0.27,
  poly_wool_herringbone: 0.27,
};

export const MODELS = ['potted_plant_01', 'potted_plant_02', 'potted_plant_04', 'ceramic_vase_01', 'ceramic_vase_03', 'exterior_aircon_unit', 'ceiling_fan'];

export const assets = { tex: {}, models: {}, hdr: null };

function loadTexture(loader, url, srgb) {
  return loader.loadAsync(url).then((t) => {
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.anisotropy = 8;
    if (srgb) t.colorSpace = THREE.SRGBColorSpace;
    return t;
  });
}

export async function loadAssets(onProgress = () => {}) {
  const tl = new THREE.TextureLoader();
  const gl = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  const jobs = [];
  for (const [id, metres] of Object.entries(TEXTURES)) {
    const f = (m) => `${BASE}/textures/${id}/${id}_${m}_1k.webp`;
    jobs.push(Promise.all([loadTexture(tl, f('diff'), true), loadTexture(tl, f('nor_gl'), false), loadTexture(tl, f('arm'), false)])
      .then(([map, normalMap, arm]) => {
        for (const t of [map, normalMap, arm]) t.repeat.set(1 / metres, 1 / metres);
        assets.tex[id] = { map, normalMap, arm };
      }));
  }
  for (const id of MODELS) {
    jobs.push(gl.loadAsync(`${BASE}/models/${id}.glb`).then((g) => {
      g.scene.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
      assets.models[id] = g.scene;
    }));
  }
  jobs.push(new HDRLoader().loadAsync(`${BASE}/hdri/brown_photostudio_02_1k.hdr`).then((t) => {
    t.mapping = THREE.EquirectangularReflectionMapping;
    assets.hdr = t;
  }));
  let done = 0;
  const results = await Promise.allSettled(jobs.map((j) => j.finally(() => onProgress(++done / jobs.length))));
  const failed = results.filter((r) => r.status === 'rejected');
  if (failed.length) console.warn(`[assets] ${failed.length} asset(s) failed, using procedural fallbacks`, failed.map((f) => f.reason?.message));
  return assets;
}

// Clone a loaded model, rotated from glTF Y-up into the spec's Z-up, scaled to a target height (metres),
// and with its base centred on the origin. Returns null if the asset isn't available.
export function placeModel(id, height) {
  const src = assets.models[id];
  if (!src) return null;
  const inner = src.clone(true);
  inner.rotation.x = Math.PI / 2;
  const wrap = new THREE.Group();
  wrap.userData.noMerge = true;
  wrap.add(inner);
  wrap.updateMatrixWorld(true);
  const b = new THREE.Box3().setFromObject(wrap);
  const s = height ? height / (b.max.z - b.min.z) : 1;
  inner.scale.setScalar(s);
  inner.position.set(-(b.min.x + b.max.x) / 2 * s, -(b.min.y + b.max.y) / 2 * s, -b.min.z * s);
  return wrap;
}
