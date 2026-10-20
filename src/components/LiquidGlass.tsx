// Liquid glass for a surface (the chat panel): the backdrop bends through a curved rim, as at the edge of a thick
// lens, and a specular line catches the light along it. A trimmed port of the "Apple Tahoe liquid glass" component:
// its squircle outline, displacement map, rim overlay and filter chain are kept as they were; the press/hover
// physics, the button, dark appearance, tint and dispersion are dropped. Only Chromium can run an SVG filter as a
// backdrop; elsewhere the surface frosts and still gets the rim overlay.
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';

export type GlassMaterial = 'clear' | 'regular';

export interface LiquidGlassOptions {
  /** "clear" = pure refraction, "regular" = frosted. */
  material?: GlassMaterial;
  /** Rim pull in px: small values suit chips, 18 buttons, large surfaces 40–60. */
  refraction?: number;
  /** Width (px) of the curved edge. Default half the short side, capped at 32. */
  bezel?: number;
  /** Smoothing blur (px) after the bend. */
  blur?: number;
  /** Fallback tone where the filter can't run. */
  saturation?: number;
  brightness?: number;
  /** Blur (px) where the browser can't refract. */
  fallbackBlur?: number;
  /** Off: no layers and no work (e.g. a layout where nothing sits behind the surface). */
  enabled?: boolean;
}

/* ---------------- environment ---------------- */

let detectedEngine: 'chromium' | 'webkit' | 'gecko' | null = null;
/** Chromium renders SVG filters in backdrop-filter. Chrome's UA contains "Safari/", so Safari is detected by having
 * no Chromium token. */
function detectEngine() {
  if (detectedEngine) return detectedEngine;
  if (typeof navigator === 'undefined') return 'webkit';
  const ua = navigator.userAgent;
  if (/Firefox\//.test(ua)) return (detectedEngine = 'gecko');
  const brands = (navigator as Navigator & { userAgentData?: { brands?: { brand: string }[] } }).userAgentData?.brands;
  if (brands?.length) return (detectedEngine = brands.some((b) => /Chromium|Google Chrome|Microsoft Edge/i.test(b.brand)) ? 'chromium' : 'webkit');
  return (detectedEngine = /(Chrome|Chromium|Edg|SamsungBrowser)\/\d+/.test(ua) ? 'chromium' : 'webkit');
}

/* ---------------- materials and tone (light appearance) ---------------- */

const MATERIALS = {
  clear: { shadowLift: 0.18, highlightRolloff: 0 },
  // less milk than the reference: content stays readable through the frost
  regular: { blur: 4.5, fillSigma: 15, fillOpacity: 0.34, tone: [0.36, 0.67, -0.1113, 1.1031] as const },
};

const toneCache = new Map<GlassMaterial, string>();
function toneTable(material: GlassMaterial): string {
  const cached = toneCache.get(material);
  if (cached) return cached;
  let values: number[];
  if (material === 'regular') {
    const [a, b, q] = MATERIALS.regular.tone;
    values = Array.from({ length: 33 }, (_, n) => { const v = n / 32; return 0.8 * (a + b * v + q * v * v) + 0.2 * v; });
  } else {
    const spec = MATERIALS.clear;
    values = Array.from({ length: 33 }, (_, n) => {
      const v = n / 32;
      const tone = v + spec.shadowLift * (1 - v) ** 3 - spec.highlightRolloff * v ** 3;
      return Math.max(0, Math.min(1, 0.94 * tone + 0.06 * (0.78 * v + 0.26)));
    });
  }
  const table = values.map((v) => v.toFixed(5)).join(' ');
  toneCache.set(material, table);
  return table;
}

/* ---------------- squircle outline (Lissé corner construction) ---------------- */

const SMOOTHING = 0.8125;
const rad = (d: number) => (d * Math.PI) / 180;

function cornerParams(radius: number, budget: number) {
  const e = radius;
  let p = (1 + SMOOTHING) * e;
  const o = 90 * (1 - SMOOTHING);
  const i = Math.sin(rad(o / 2)) * e * Math.SQRT2;
  const g = e * Math.tan(rad((90 - o) / 4));
  const c = g * Math.cos(rad(45 * SMOOTHING));
  const d = c * Math.tan(rad(45 * SMOOTHING));
  let u = (p - i - c - d) / 3;
  let a = 2 * u;
  if (p > budget) {
    const rem = budget - d - i - c;
    const l = rem - rem / 6;
    u = Math.min(u, l);
    a = rem - u;
    p = budget;
  }
  return { a, b: u, c, d, p, i };
}

interface Outline {
  /** Corner point/normal runs [x,y,nx,ny]*n, centred coords: TR BR BL TL. */
  corners: [Float64Array, Float64Array, Float64Array, Float64Array];
  extent: number;
  /** CSS polygon() for clip-path, in element coordinates. */
  polygon: string;
  width: number;
  height: number;
}

function bezier(out: number[], x0: number, y0: number, x1: number, y1: number, x2: number, y2: number, x3: number, y3: number, steps: number) {
  for (let s = 1; s <= steps; s++) {
    const t = s / steps, w = 1 - t;
    out.push(x0 * w * w * w + 3 * w * w * t * x1 + 3 * w * t * t * x2 + t * t * t * x3, y0 * w * w * w + 3 * w * w * t * y1 + 3 * w * t * t * y2 + t * t * t * y3);
  }
}

const outlineCache = new Map<string, Outline>();
function squircleOutline(w: number, h: number, radius: number): Outline {
  const key = `${w}|${h}|${radius}`;
  const cached = outlineCache.get(key);
  if (cached) return cached;
  const hw = w / 2, hh = h / 2;
  const budget = Math.min(hw, hh);
  const r = Math.min(radius, budget);
  if (r < 0.5) {
    const empty = new Float64Array(0);
    const box: Outline = { corners: [empty, empty, empty, empty], extent: 0, polygon: `polygon(0px 0px, ${w}px 0px, ${w}px ${h}px, 0px ${h}px)`, width: w, height: h };
    outlineCache.set(key, box);
    return box;
  }
  const { a, b, c, d, p, i } = cornerParams(r, budget);
  // top-right corner, from the top edge to the right edge (clockwise on screen)
  const pts: number[] = [];
  const x0 = hw - p, y0 = -hh;
  bezier(pts, x0, y0, x0 + a, y0, x0 + a + b, y0, x0 + a + b + c, y0 + d, 24);
  const ax = x0 + a + b + c, ay = y0 + d;
  const chord = i * Math.SQRT2;
  const offset = Math.sqrt(Math.max(0, r * r - (chord * chord) / 4));
  const cx = ax + i / 2 - (i / chord) * offset;
  const cy = ay + i / 2 + (i / chord) * offset;
  const angle = Math.atan2(ay - cy, ax - cx);
  const sweep = 2 * Math.asin(Math.min(1, chord / (2 * r)));
  for (let s = 1; s <= 8; s++) { const th = angle + (sweep * s) / 8; pts.push(cx + r * Math.cos(th), cy + r * Math.sin(th)); }
  const qx = ax + i, qy = ay + i;
  bezier(pts, qx, qy, qx + d, qy + c, qx + d, qy + b + c, qx + d, qy + a + b + c, 24);

  // outward normals: right of travel for this winding; anchors keep the end tangents exact
  const run = [x0, y0, ...pts, hw, -hh + p];
  const count = run.length / 2;
  const trPts = new Float64Array(count * 2), trNormals = new Float64Array(count * 2);
  for (let k = 0; k < count; k++) {
    const prev = 2 * Math.max(0, k - 1), next = 2 * Math.min(count - 1, k + 1);
    let tdx = run[next] - run[prev], tdy = run[next + 1] - run[prev + 1];
    if (k === 0) { tdx = 1; tdy = 0; }
    if (k === count - 1) { tdx = 0; tdy = 1; }
    const len = Math.hypot(tdx, tdy) || 1;
    trPts[2 * k] = run[2 * k];
    trPts[2 * k + 1] = run[2 * k + 1];
    trNormals[2 * k] = tdy / len;
    trNormals[2 * k + 1] = -tdx / len;
  }
  const mirror = (mx: boolean, my: boolean) => {
    const out = new Float64Array(count * 4);
    const sx = mx ? -1 : 1, sy = my ? -1 : 1;
    for (let k = 0; k < count; k++) {
      out[4 * k] = sx * trPts[2 * k];
      out[4 * k + 1] = sy * trPts[2 * k + 1];
      out[4 * k + 2] = sx * trNormals[2 * k];
      out[4 * k + 3] = sy * trNormals[2 * k + 1];
    }
    return out;
  };
  const tr = mirror(false, false), br = mirror(false, true), bl = mirror(true, true), tl = mirror(true, false);
  // mirrored runs flip direction: BR and TL are walked backwards so the polygon never crosses itself
  const poly: string[] = [];
  const push = (rn: Float64Array, step: number) => {
    for (let k = step > 0 ? 0 : count - 1; step > 0 ? k < count : k >= 0; k += step) poly.push(`${(rn[4 * k] + hw).toFixed(2)}px ${(rn[4 * k + 1] + hh).toFixed(2)}px`);
  };
  push(tr, 1); push(br, -1); push(bl, 1); push(tl, -1);
  const outline: Outline = { corners: [tr, br, bl, tl], extent: p, polygon: `polygon(${poly.join(', ')})`, width: w, height: h };
  if (outlineCache.size > 64) outlineCache.delete(outlineCache.keys().next().value!);
  outlineCache.set(key, outline);
  return outline;
}

const sat = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);

/** Signed distance and outward unit normal of the squircle, centred coords. */
function outlineField(px: number, py: number, o: Outline): { d: number; nx: number; ny: number } {
  const hw = o.width / 2, hh = o.height / 2, p = o.extent;
  const ax = Math.abs(px), ay = Math.abs(py);
  const qx = ax - (hw - p), qy = ay - (hh - p);
  if (qx > 0 && qy > 0) {
    const run = px >= 0 ? (py < 0 ? o.corners[0] : o.corners[1]) : py < 0 ? o.corners[3] : o.corners[2];
    if (run.length === 0) {
      const dx = ax - hw, dy = ay - hh, dist = Math.hypot(dx, dy);
      return { d: dist, nx: dx / (dist || 1), ny: dy / (dist || 1) };
    }
    // nearest point on each polyline segment, not on its sample points
    let best2 = Infinity, bx = 0, by = 0, bnx = 0, bny = 0;
    const n = run.length / 4;
    for (let k = 0; k < n - 1; k++) {
      const x1 = run[4 * k], y1 = run[4 * k + 1];
      const ex = run[4 * k + 4] - x1, ey = run[4 * k + 5] - y1;
      const len2 = ex * ex + ey * ey;
      const t = len2 > 1e-12 ? sat(((px - x1) * ex + (py - y1) * ey) / len2) : 0;
      const sx = x1 + ex * t, sy = y1 + ey * t;
      const d2 = (px - sx) ** 2 + (py - sy) ** 2;
      if (d2 < best2) { best2 = d2; bx = sx; by = sy; bnx = run[4 * k + 2]; bny = run[4 * k + 3]; }
    }
    const dist = Math.sqrt(best2);
    const dx = px - bx, dy = py - by;
    const dir = dx * bnx + dy * bny >= 0 ? 1 : -1;
    return dist > 1e-5 ? { d: dist * dir, nx: (dx / dist) * dir, ny: (dy / dist) * dir } : { d: 0, nx: bnx, ny: bny };
  }
  if (qx > qy) return { d: qx - p, nx: Math.sign(px) || 1, ny: 0 };
  return { d: qy - p, nx: 0, ny: Math.sign(py) || 1 };
}

/* ---------------- map planes: the displacement field and the rim overlay ---------------- */

const COS_SPREAD = Math.cos(1.08), COS_DIFFUSE = Math.cos(1.08 * 0.65), COS_SIDE = Math.cos(0.98);
const pixelScale = (w: number, h: number) => (w * h > 250_000 ? 0.5 : Math.min(Math.max(window.devicePixelRatio || 1, 1), 2));

function renderPlanes(w: number, h: number, radius: number, bezel: number): { field: string; overlay: string } {
  try {
    const outline = squircleOutline(w, h, radius);
    const hw = w / 2, hh = h / 2, p = outline.extent;
    const bz = Math.max(1, Math.min(bezel, hw, hh));
    const pad = 2; // the overlay runs 2px past the shape: half the specular stroke and the side shade live there
    const out = { field: '', overlay: '' };
    for (const isField of [true, false]) {
      const cw = isField ? w : w + pad * 2, ch = isField ? h : h + pad * 2;
      // the field is smooth and can be coarse on a big surface; the rim light is a 1px line and must not be (at half
      // resolution it breaks into dark dots round the corners)
      const scale = isField ? pixelScale(cw, ch) : Math.min(Math.max(window.devicePixelRatio || 1, 1), 2);
      const width = Math.max(1, Math.round(cw * scale)), height = Math.max(1, Math.round(ch * scale));
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) return { field: '', overlay: '' };
      const img = ctx.createImageData(width, height);
      const data = img.data;
      for (let j = 0; j < height; j++) {
        const py = (j + 0.5) / scale - ch / 2;
        for (let i = 0; i < width; i++) {
          const px = (i + 0.5) / scale - cw / 2;
          const o = (j * width + i) * 4;
          if (isField) {
            // deep interior: no displacement (skips the distance work for most of a big panel)
            if (Math.abs(px) < hw - bz - 4 && Math.abs(py) < hh - bz - 4 && Math.abs(px) < hw - p && Math.abs(py) < hh - p) {
              data[o] = 128; data[o + 1] = 128; data[o + 2] = 128; data[o + 3] = 255;
              continue;
            }
            const { d, nx, ny } = outlineField(px, py, outline);
            const coverage = sat(0.5 - d);
            if (d < -(bz + 4)) { data[o] = 128; data[o + 1] = 128; data[o + 2] = 128; data[o + 3] = 255; continue; }
            // circular falloff, full at the lip
            const depth = sat(-d / bz);
            const edge = 1 - sat(Math.sqrt((2 - depth) * depth));
            data[o] = Math.round(255 * (0.5 - nx * edge * 0.5));
            data[o + 1] = Math.round(255 * (0.5 - ny * edge * 0.5));
            data[o + 2] = 128;
            data[o + 3] = Math.round(255 * coverage);
          } else {
            const qx = Math.abs(px) - (hw - p), qy = Math.abs(py) - (hh - p);
            if (Math.max(qx, qy) - p > pad + 6 || (Math.abs(px) < hw - 8 && Math.abs(py) < hh - 8 && qx < 0 && qy < 0)) continue;
            const { d, nx, ny } = outlineField(px, py, outline);
            const coverage = sat(0.5 - d);
            const directional = Math.pow(sat((Math.abs(ny) - COS_SPREAD) / (1 - COS_SPREAD)), 1.25);
            const diffuseDir = Math.pow(sat((Math.abs(ny) - COS_DIFFUSE) / (1 - COS_DIFFUSE)), 1.25);
            const stroke = sat(d + 0.9 + 0.5) * sat(0.5 - d);
            let specular = directional * stroke;
            specular = specular / (1 + 0.5 * (1 - specular));
            const diffuse = diffuseDir * 0.18 * Math.pow(1 - sat(-d / 4.5), 2) * coverage;
            const highlight = sat(specular + diffuse);
            const adjusted = d - 0.5;
            const falloff = 1 - sat(-adjusted / 0.5);
            const ramp = (falloff > 0 ? 1 : 0) * 0.25 + falloff * 0.75;
            const band = ramp * sat(adjusted + 0.5 + 0.5) * sat(-adjusted + 0.5);
            const wx = sat((nx - COS_SIDE) / (1 - COS_SIDE)) * band;
            const wy = sat((-nx - COS_SIDE) / (1 - COS_SIDE)) * band;
            const shade = (wx / (1 + 0.4 * (1 - wx)) + wy / (1 + 0.4 * (1 - wy))) * 0.48;
            const alpha = highlight + shade * (1 - highlight);
            if (alpha <= 0.002) continue;
            data[o] = data[o + 1] = data[o + 2] = Math.round(255 * Math.min(1, highlight / Math.max(alpha, 1e-5)));
            data[o + 3] = Math.round(255 * alpha);
          }
        }
      }
      ctx.putImageData(img, 0, 0);
      out[isField ? 'field' : 'overlay'] = canvas.toDataURL('image/png');
    }
    return out;
  } catch {
    return { field: '', overlay: '' };
  }
}

const planeCache = new Map<string, { field: string; overlay: string }>();
function planes(w: number, h: number, radius: number, bezel: number) {
  const key = `${w}x${h}r${radius}b${bezel}`;
  let hit = planeCache.get(key);
  if (!hit) {
    hit = renderPlanes(w, h, radius, bezel);
    if (planeCache.size > 16) planeCache.delete(planeCache.keys().next().value!);
    planeCache.set(key, hit);
  }
  return hit;
}

/* ---------------- one shared, document-level host for the filters ---------------- */

let defsHost: SVGSVGElement | null = null;
const filterRefs = new Map<string, { el: SVGFilterElement; count: number }>();

function acquireFilter(id: string, markup: string) {
  const existing = filterRefs.get(id);
  if (existing) { existing.count++; return; }
  if (!defsHost) {
    defsHost = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    defsHost.setAttribute('aria-hidden', 'true');
    defsHost.setAttribute('width', '0');
    defsHost.setAttribute('height', '0');
    Object.assign(defsHost.style, { position: 'fixed', width: '0', height: '0', overflow: 'hidden', pointerEvents: 'none' });
    document.body.appendChild(defsHost);
  }
  const filter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
  filter.setAttribute('id', id);
  filter.setAttribute('color-interpolation-filters', 'sRGB');
  filter.innerHTML = markup;
  defsHost.appendChild(filter);
  filterRefs.set(id, { el: filter, count: 1 });
}

function releaseFilter(id: string) {
  const entry = filterRefs.get(id);
  if (entry && --entry.count <= 0) { entry.el.remove(); filterRefs.delete(id); }
}

/** The lens chain: (frost), bend through the field, tone, clip to the shape. */
function filterMarkup(w: number, h: number, field: string, material: GlassMaterial, refraction: number): string {
  const parts = [`<feImage href="${field}" x="0" y="0" width="${w}" height="${h}" preserveAspectRatio="none" result="field"/>`];
  let base = 'SourceGraphic';
  if (material === 'regular') {
    const r = MATERIALS.regular;
    parts.push(
      `<feGaussianBlur in="SourceGraphic" stdDeviation="${r.blur}" result="frost"/>`,
      `<feGaussianBlur in="SourceGraphic" stdDeviation="${r.fillSigma}" result="fill"/>`,
      `<feBlend in="frost" in2="fill" mode="lighten" result="blend"/>`,
      `<feComposite in="blend" in2="frost" operator="arithmetic" k2="${r.fillOpacity}" k3="${1 - r.fillOpacity}" result="mix"/>`,
    );
    base = 'mix';
  }
  const table = toneTable(material);
  parts.push(
    `<feDisplacementMap in="${base}" in2="field" scale="${refraction * 2}" xChannelSelector="R" yChannelSelector="G" result="refracted"/>`,
    `<feComponentTransfer in="refracted" result="tone"><feFuncR type="table" tableValues="${table}"/><feFuncG type="table" tableValues="${table}"/><feFuncB type="table" tableValues="${table}"/></feComponentTransfer>`,
  );
  let color = 'tone';
  if (material === 'regular') { parts.push(`<feColorMatrix in="tone" type="saturate" values="${MATERIALS.regular.tone[3]}" result="color"/>`); color = 'color'; }
  parts.push(`<feComposite in="${color}" in2="field" operator="in"/>`);
  return parts.join('');
}

/* ---------------- the hook ---------------- */

interface MapState { field: string; overlay: string; clip: string; filterId: string }

/**
 * Glass layers for a surface: render `layers` as the surface's first children. The surface needs a position, its own
 * stacking context (isolation: isolate) and no backdrop-filter, filter or opacity of its own (any of those would
 * hide the page behind it from the lens).
 */
export function useLiquidGlass({
  material = 'regular', refraction = 18, bezel, blur = 1, saturation = 1.2, brightness = 1.05, fallbackBlur = 10, enabled = true,
}: LiquidGlassOptions = {}): { mode: 'refract' | 'frost'; layers: ReactNode } {
  const lensRef = useRef<HTMLSpanElement | null>(null);
  const [map, setMap] = useState<MapState | null>(null);
  const engine = useMemo(() => detectEngine(), []);
  const canRefract = engine === 'chromium';

  useLayoutEffect(() => {
    if (!enabled) { setMap(null); return; }
    const lens = lensRef.current;
    if (!lens) return;
    let raf = 0;
    let held: string | null = null;
    const measure = () => {
      raf = 0;
      const w = Math.round(lens.offsetWidth), h = Math.round(lens.offsetHeight);
      if (w < 2 || h < 2) return;
      const r = parseFloat(getComputedStyle(lens).borderTopLeftRadius) || 0;
      const radius = Math.round(Math.max(0, Math.min(r, w / 2, h / 2)));
      const bz = Math.round(bezel ?? Math.min(32, Math.min(w, h) / 2));
      const outline = squircleOutline(w, h, radius);
      const { field, overlay } = planes(w, h, radius, bz);
      let filterId = '';
      if (canRefract && field) {
        filterId = `lgf${w}x${h}r${radius}b${bz}f${Math.round(refraction * 10)}${material === 'regular' ? 'R' : 'C'}`;
        if (held !== filterId) {
          acquireFilter(filterId, filterMarkup(w, h, field, material, refraction));
          if (held) releaseFilter(held);
          held = filterId;
        }
      }
      setMap((prev) => (prev && prev.filterId === filterId && prev.overlay === overlay ? prev : { field, overlay, clip: outline.polygon, filterId }));
    };
    const ro = new ResizeObserver(() => { if (!raf) raf = requestAnimationFrame(measure); });
    measure();
    ro.observe(lens);
    return () => { ro.disconnect(); cancelAnimationFrame(raf); if (held) releaseFilter(held); };
  }, [enabled, material, refraction, bezel, canRefract]);

  const refracting = canRefract && !!map?.filterId;
  if (!enabled) return { mode: 'frost', layers: null };

  const tone = `saturate(${saturation}) brightness(${brightness})`;
  const fallback = Math.min(fallbackBlur, engine === 'webkit' ? 16 : fallbackBlur);
  const backdrop = refracting
    ? `url(#${map!.filterId})${blur > 0 ? ` blur(${blur}px)` : ''}`
    : `blur(${material === 'regular' ? Math.max(2, fallback * 0.5) : fallback}px) ${tone}`;
  const layer: CSSProperties = { position: 'absolute', inset: 0, zIndex: -1, borderRadius: 'inherit', pointerEvents: 'none' };

  const layers = (
    <>
      {/* the lens: the backdrop, bent through the field and toned, clipped to the squircle the maps were built from */}
      <span ref={lensRef} aria-hidden="true" style={{ ...layer, backdropFilter: backdrop, WebkitBackdropFilter: backdrop, clipPath: map?.clip }} />
      {map && (
        <>
          {/* the rim light: its own alpha is the mask */}
          <span aria-hidden="true" style={{ ...layer, inset: -2, borderRadius: 0, backgroundImage: `url(${map.overlay})`, backgroundSize: '100% 100%', backgroundRepeat: 'no-repeat' }} />
          {/* frosted fill where the filter can't run (it bakes the frost in when refracting) */}
          {!refracting && material === 'regular' && <span aria-hidden="true" style={{ ...layer, backgroundColor: `rgb(255 255 255 / ${MATERIALS.regular.fillOpacity})` }} />}
        </>
      )}
    </>
  );
  return { mode: refracting ? 'refract' : 'frost', layers };
}
