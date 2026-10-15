// Procedural canvas textures (no external assets). Every texture is authored for a known
// real-world size so that geometry UVs in metres (see worldUV) map 1:1 to physical scale.
import * as THREE from 'three';

function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

function canvas(px) {
  const c = document.createElement('canvas');
  c.width = c.height = px;
  return [c, c.getContext('2d')];
}

function noise(ctx, px, amount, rand, alpha = 0.05, size = 1) {
  for (let i = 0; i < amount; i++) {
    const v = rand() > 0.5 ? 255 : 0;
    ctx.fillStyle = `rgba(${v},${v},${v},${alpha * rand()})`;
    ctx.fillRect(rand() * px, rand() * px, size, size);
  }
}

function finish(c, metres, srgb = true) {
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(1 / metres, 1 / metres);
  t.anisotropy = 8;
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const hsl = (h, s, l) => `hsl(${h},${s}%,${l}%)`;

// Light oak planks, 190 mm wide, random lengths. Returns colour + bump.
export function oakFloor() {
  const px = 1024, metres = 2.0, ppm = px / metres, rand = rng(7);
  const [c, g] = canvas(px);
  const [b, gb] = canvas(px);
  gb.fillStyle = '#fff'; gb.fillRect(0, 0, px, px);
  const pw = 0.19 * ppm;
  for (let row = 0; row * pw < px; row++) {
    let x = -rand() * 1.4 * ppm;
    while (x < px) {
      const len = (1.1 + rand() * 0.9) * ppm;
      const l = 62 + rand() * 9, h = 30 + rand() * 6;
      g.fillStyle = hsl(h, 38 + rand() * 10, l);
      g.fillRect(x, row * pw, len, pw);
      // grain
      for (let k = 0; k < 26; k++) {
        const y = row * pw + rand() * pw;
        g.strokeStyle = `rgba(110,72,40,${0.04 + rand() * 0.08})`;
        g.lineWidth = 0.6 + rand() * 1.4;
        g.beginPath();
        g.moveTo(x, y);
        for (let s = 0; s <= 12; s++) g.lineTo(x + (len * s) / 12, y + Math.sin(s * 0.9 + k) * rand() * 2.2);
        g.stroke();
      }
      if (rand() > 0.7) { // knot
        g.fillStyle = 'rgba(100,62,32,0.18)';
        g.beginPath(); g.ellipse(x + rand() * len, row * pw + rand() * pw, 7, 3, 0, 0, 7); g.fill();
      }
      g.fillStyle = 'rgba(70,45,25,0.5)'; g.fillRect(x, row * pw, 1.2, pw);
      gb.fillStyle = '#555'; gb.fillRect(x, row * pw, 1.5, pw);
      x += len;
    }
    g.fillStyle = 'rgba(70,45,25,0.45)'; g.fillRect(0, row * pw, px, 1.2);
    gb.fillStyle = '#555'; gb.fillRect(0, row * pw, px, 1.5);
  }
  noise(g, px, 30000, rand, 0.04);
  return { map: finish(c, metres), bumpMap: finish(b, metres, false) };
}

// Large-format porcelain tile with faint marbling and grout.
export function tiles({ tileW, tileH, metres, base = [36, 18, 82], vary = 2.5, grout = '#b7ad9f', veins = 0.05, seed = 3 }) {
  const px = 1024, ppm = px / metres, rand = rng(seed);
  const [c, g] = canvas(px);
  const [b, gb] = canvas(px);
  gb.fillStyle = '#fff'; gb.fillRect(0, 0, px, px);
  const tw = tileW * ppm, th = tileH * ppm;
  for (let y = 0; y < px; y += th) {
    for (let x = 0; x < px; x += tw) {
      g.fillStyle = hsl(base[0], base[1], base[2] + (rand() - 0.5) * vary);
      g.fillRect(x, y, tw, th);
      for (let k = 0; k < 4; k++) {
        g.strokeStyle = `rgba(120,110,100,${veins * rand()})`;
        g.lineWidth = 1 + rand() * 3;
        g.beginPath();
        let vx = x + rand() * tw, vy = y;
        g.moveTo(vx, vy);
        for (let s = 0; s < 10; s++) { vx += (rand() - 0.5) * tw * 0.3; vy += th / 10; g.lineTo(vx, vy); }
        g.stroke();
      }
    }
  }
  noise(g, px, 60000, rand, 0.035);
  g.fillStyle = grout; gb.fillStyle = '#333';
  for (let y = 0; y < px; y += th) { g.fillRect(0, y, px, 2); gb.fillRect(0, y, px, 2.5); }
  for (let x = 0; x < px; x += tw) { g.fillRect(x, 0, 2, px); gb.fillRect(x, 0, 2.5, px); }
  return { map: finish(c, metres), bumpMap: finish(b, metres, false) };
}

export function plaster() {
  const px = 512, metres = 2, rand = rng(11);
  const [c, g] = canvas(px);
  g.fillStyle = '#ffffff'; g.fillRect(0, 0, px, px);
  noise(g, px, 40000, rand, 0.025, 2);
  return finish(c, metres);
}

export function fabric(seed = 5) {
  const px = 256, metres = 0.25, rand = rng(seed);
  const [c, g] = canvas(px);
  g.fillStyle = '#ffffff'; g.fillRect(0, 0, px, px);
  for (let i = 0; i < px; i += 2) {
    g.fillStyle = `rgba(0,0,0,${0.03 + rand() * 0.04})`; g.fillRect(0, i, px, 1);
    g.fillStyle = `rgba(0,0,0,${0.02 + rand() * 0.03})`; g.fillRect(i, 0, 1, px);
  }
  noise(g, px, 6000, rand, 0.08);
  return finish(c, metres);
}

export function woodVeneer(seed = 9, light = 60) {
  const px = 512, metres = 1.0, rand = rng(seed);
  const [c, g] = canvas(px);
  g.fillStyle = hsl(31, 40, light); g.fillRect(0, 0, px, px);
  for (let k = 0; k < 140; k++) {
    const y = rand() * px;
    g.strokeStyle = `rgba(105,68,38,${0.03 + rand() * 0.09})`;
    g.lineWidth = 0.5 + rand() * 2;
    g.beginPath(); g.moveTo(0, y);
    for (let s = 0; s <= 16; s++) g.lineTo((px * s) / 16, y + Math.sin(s * 0.7 + k * 0.3) * 3 * rand());
    g.stroke();
  }
  noise(g, px, 12000, rand, 0.03);
  return finish(c, metres);
}

export function quartz() {
  const px = 512, metres = 1.0, rand = rng(21);
  const [c, g] = canvas(px);
  g.fillStyle = '#f1efea'; g.fillRect(0, 0, px, px);
  for (let i = 0; i < 2600; i++) {
    const v = 150 + rand() * 80;
    g.fillStyle = `rgba(${v},${v - 5},${v - 10},${0.25 * rand()})`;
    g.fillRect(rand() * px, rand() * px, 1 + rand() * 2, 1 + rand() * 2);
  }
  return finish(c, metres);
}

export function pavers() {
  return tiles({ tileW: 0.6, tileH: 0.6, metres: 2.4, base: [35, 6, 70], vary: 4, grout: '#8f8a83', veins: 0.02, seed: 17 });
}

// Rug: woven field with a border, mapped 0..1 (not world UVs).
export function rug(seed = 2, hue = 34, light = 78) {
  const px = 512, rand = rng(seed);
  const [c, g] = canvas(px);
  g.fillStyle = hsl(hue, 12, light); g.fillRect(0, 0, px, px);
  noise(g, px, 50000, rand, 0.06, 2);
  g.strokeStyle = hsl(hue, 10, light - 12); g.lineWidth = 10;
  g.strokeRect(22, 22, px - 44, px - 44);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}
