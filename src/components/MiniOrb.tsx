// Penjaga thinking: a small dotted orb that turns while lights run across its dots. The thinking orb's look
// (graphite dots on the light page, lit in turn), drawn at icon size with fewer, larger dots.
import { useEffect, useRef } from 'react';

const TAU = Math.PI * 2;
const RINGS = 9;
const DOTS = (() => {
  const out: { x: number; y: number; z: number; seed: number }[] = [];
  for (let k = 0; k < RINGS; k++) {
    const y = 1 - ((k + 0.5) / RINGS) * 2;
    const r = Math.sqrt(1 - y * y);
    const m = Math.max(4, Math.round(16 * r));
    for (let j = 0; j < m; j++) {
      const a = (j / m) * TAU + k * 0.35;
      out.push({ x: Math.cos(a) * r, y, z: Math.sin(a) * r, seed: (k * 31 + j * 17) % 63 / 10 });
    }
  }
  return out;
})();
const CP = Math.cos(0.35), SP = Math.sin(0.35); // tilted towards the viewer a little

export default function MiniOrb({ size = 22 }: { size?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current, ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const R = size * 0.4, C = size / 2;
    let raf = 0;
    const draw = (now: number) => {
      const t = reduce ? 1.2 : now / 1000;
      ctx.clearRect(0, 0, size, size);
      const yaw = t * 1.1, cy = Math.cos(yaw), sy = Math.sin(yaw);
      // two lights wander over the sphere
      const a1 = t * 1.6, b1 = Math.sin(t) * 0.9, a2 = t * 1.1 + 2.1, b2 = Math.cos(t * 0.8) * 0.9;
      const f = [[Math.cos(b1) * Math.cos(a1), Math.sin(b1), Math.cos(b1) * Math.sin(a1)], [Math.cos(b2) * Math.cos(a2), Math.sin(b2), Math.cos(b2) * Math.sin(a2)]];
      const pts = DOTS.map((d) => {
        const lit = Math.max(0, ...f.map(([x, y, z]) => (d.x * x + d.y * y + d.z * z - 0.6) / 0.4));
        const x1 = d.x * cy + d.z * sy, z1 = -d.x * sy + d.z * cy;
        const y2 = d.y * CP - z1 * SP, z2 = d.y * SP + z1 * CP;
        const depth = (z2 + 1) / 2;
        const a = Math.min(1, 0.12 + 0.03 * Math.sin(d.seed + t * 1.6) + 0.35 * depth * depth + 0.7 * lit * lit);
        return { x: C + x1 * R, y: C - y2 * R, r: R * (0.055 + 0.06 * depth + 0.05 * lit), a, depth };
      }).sort((p, q) => p.depth - q.depth); // back dots first
      for (const p of pts) {
        ctx.fillStyle = `rgba(72,67,60,${p.a.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, TAU);
        ctx.fill();
      }
      if (!reduce) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size]);
  return <canvas ref={ref} className="mini-orb" style={{ width: size, height: size }} aria-hidden="true" />;
}
