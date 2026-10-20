// Penjaga, alive: the little house from the app icon, drawn so that every part can move, and moved every frame by
// the rig (rig.ts). Same shapes and colours as Character.tsx, so it reads as the same character everywhere; the
// static one stays for the many small avatars in the chat, this one is used where Penjaga is "present": the panel
// header, the resting screen, and while it listens or talks.
//
// Updates go straight to the SVG's attributes (no React render per frame), and the loop sleeps while the character
// is off screen or the tab is hidden.
import { useEffect, useId, useRef } from 'react';
import { Rig, type Activity, type Mood, type Pose } from './rig';
import '../styles/live-character.css';

export type { Activity, Mood } from './rig';

const INK = '#12352E';
const PAPER = '#F6F1E7';
const EYES = [216, 296] as const;
const EYE_Y = 292;

// the last place the pointer was, shared by every character on the page (one listener)
const pointer = { x: 0, y: 0, at: -Infinity };
let pointerUsers = 0;
const onPointer = (e: PointerEvent) => { pointer.x = e.clientX; pointer.y = e.clientY; pointer.at = performance.now(); };

export interface LiveCharacterProps {
  mood: Mood;
  activity?: Activity;
  size?: number;
  /** The green disc behind the house (the app-icon look). Off: a free-standing character with a soft shadow. */
  disc?: boolean;
  /** Look at the pointer while it moves. */
  track?: boolean;
  /** Penjaga's voice level, 0..1, read every frame (the mouth follows it). */
  voice?: () => number;
  /** The owner's voice level while listening, 0..1, read every frame. */
  mic?: () => number;
  className?: string;
  title?: string;
}

export default function LiveCharacter({ mood, activity = 'idle', size = 120, disc = true, track = false, voice, mic, className = '', title }: LiveCharacterProps) {
  const svg = useRef<SVGSVGElement>(null);
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const live = useRef({ mood, activity, voice, mic, track });
  live.current = { mood, activity, voice, mic, track };

  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)');
    const rig = new Rig();
    const q = <T extends Element>(sel: string) => el.querySelector<T>(sel)!;
    const parts = {
      body: q<SVGGElement>('.lc-body'),
      eyes: [0, 1].map((i) => ({ g: q<SVGGElement>(`.lc-eye${i}`), ball: q<SVGEllipseElement>(`.lc-eye${i} .lc-ball`), shine: q<SVGCircleElement>(`.lc-eye${i} .lc-shine`), lid: q<SVGPathElement>(`.lc-eye${i} .lc-lid`) })),
      brows: [q<SVGPathElement>('.lc-brow0'), q<SVGPathElement>('.lc-brow1')],
      mouth: q<SVGPathElement>('.lc-mouth'),
      mouthClip: q<SVGPathElement>(`#lc-m-${uid} path`),
      tongue: q<SVGEllipseElement>('.lc-tongue'),
      cheeks: [q<SVGEllipseElement>('.lc-cheek0'), q<SVGEllipseElement>('.lc-cheek1')],
      lamp: q<SVGCircleElement>('.lc-lamp'),
      glowStop: q<SVGStopElement>(`#lc-g-${uid} stop`),
      glow: q<SVGCircleElement>('.lc-glow'),
      halo: q<SVGCircleElement>('.lc-halo'),
      shadow: el.querySelector<SVGEllipseElement>('.lc-shadow'),
      fx: q<SVGGElement>('.lc-fx'),
    };
    if (live.current.track) { if (!pointerUsers++) window.addEventListener('pointermove', onPointer, { passive: true }); }
    const tracking = live.current.track;

    let raf = 0, last = performance.now(), visible = true;
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) wake(); }, { rootMargin: '40px' });
    io.observe(el);
    const wake = () => { if (!raf && visible && !document.hidden) { last = performance.now(); raf = requestAnimationFrame(frame); } };
    const onVis = () => wake();
    document.addEventListener('visibilitychange', onVis);

    function look(): { x: number; y: number } | null {
      if (!live.current.track || performance.now() - pointer.at > 2500) return null;
      const r = el!.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height * 0.55;
      return { x: (pointer.x - cx) / Math.max(260, innerWidth * 0.4), y: (pointer.y - cy) / Math.max(220, innerHeight * 0.45) };
    }

    function frame(now: number) {
      raf = 0;
      if (!visible || document.hidden) return;
      const dt = (now - last) / 1000;
      last = now;
      const c = live.current;
      const pose = rig.update(dt, {
        mood: c.mood, activity: c.activity,
        voice: c.activity === 'speaking' ? c.voice?.() ?? 0 : 0,
        mic: c.activity === 'listening' ? c.mic?.() ?? 0 : 0,
        look: look(), reduceMotion: reduce.matches,
      });
      apply(pose, rig);
      raf = requestAnimationFrame(frame);
    }

    function apply(p: Pose, r: Rig) {
      parts.body.setAttribute('transform', `translate(${p.x.toFixed(2)} ${p.y.toFixed(2)}) translate(256 398) rotate(${p.rot.toFixed(2)}) scale(${p.sx.toFixed(4)} ${p.sy.toFixed(4)}) translate(-256 -398)`);
      // eyes: the ball shrinks to a slit when the lid closes; the lid line takes over (arched up when happy,
      // down when asleep); the highlight moves a little more than the eye, which gives it depth
      const dx = p.gazeX * 9, dy = p.gazeY * 7;
      const open = p.eyeOpen;
      const shown = smooth(0.06, 0.32, open);
      parts.eyes.forEach((e, i) => {
        const cx = EYES[i] + dx, cy = EYE_Y + dy;
        e.ball.setAttribute('cx', cx.toFixed(2));
        e.ball.setAttribute('cy', cy.toFixed(2));
        e.ball.setAttribute('rx', (13 * Math.sqrt(Math.max(0.2, p.eyeWide))).toFixed(2));
        e.ball.setAttribute('ry', Math.max(0.01, 17 * Math.max(0.2, p.eyeWide) * open).toFixed(2));
        e.ball.setAttribute('opacity', shown.toFixed(3));
        e.shine.setAttribute('cx', (cx + 4 + p.gazeX * 2.5).toFixed(2));
        e.shine.setAttribute('cy', (cy - 6 * open + p.gazeY * 2).toFixed(2));
        e.shine.setAttribute('r', Math.max(0, 4 * Math.min(1, p.eyeWide)).toFixed(2));
        e.shine.setAttribute('opacity', smooth(0.45, 0.8, open).toFixed(3));
        const ex = EYES[i] + dx * 0.5, ey = EYE_Y + 2 + p.lid * 2 + dy * 0.4;
        const bend = -p.lid * (p.lid > 0 ? 18 : 11);
        e.lid.setAttribute('d', `M${(ex - 14).toFixed(2)} ${ey.toFixed(2)} Q${ex.toFixed(2)} ${(ey + bend).toFixed(2)} ${(ex + 14).toFixed(2)} ${ey.toFixed(2)}`);
        e.lid.setAttribute('opacity', (1 - shown).toFixed(3));
      });
      // brows: lift, and worry raises the inner ends
      const by = 258 - p.browLift * 9 + p.gazeY * 2;
      parts.brows.forEach((b, i) => {
        const s = i === 0 ? 1 : -1; // mirror for the right brow
        const outer = EYES[i] - 18 * s, inner = EYES[i] + 18 * s;
        const oy = by + p.browWorry * 4, iy = by - p.browWorry * 9;
        b.setAttribute('d', `M${(outer + dx * 0.4).toFixed(2)} ${oy.toFixed(2)} Q${(EYES[i] + dx * 0.4).toFixed(2)} ${(by - 10 * (1 - Math.max(0, p.browWorry) * 0.85)).toFixed(2)} ${(inner + dx * 0.4).toFixed(2)} ${iy.toFixed(2)}`);
        b.setAttribute('opacity', p.browShow.toFixed(3));
      });
      // mouth: corners, an upper and a lower lip; zero height is a line (the stroke), open is a shape
      const w = p.mouthWidth * (1 - p.mouthRound * 0.35);
      const cx = 256 + p.mouthSkew * 5 + dx * 0.35, cy = 333 + dy * 0.3;
      const lift = p.mouthSmile * 5;
      const ly = cy - lift + p.mouthSkew * 3, ry = cy - lift - p.mouthSkew * 4;
      // closed, the lips are one curve (the smile); opening, the upper lip flattens towards the corners and the lower
      // lip drops and rounds, so an open mouth reads as a "D" (or an "o" when rounded), never a "V"
      const k = smooth(0, 12, p.mouthOpen);
      const corner = (ly + ry) / 2;
      // cubic lips with their control points spread towards the corners: a round "U" bottom, never a point
      const curve = corner + p.mouthSmile * 13; // where both lips meet when closed (the smile's depth)
      const up = mix(curve, corner - p.mouthOpen * 0.25, k) - p.mouthRound * p.mouthOpen * 0.8;
      const low = curve + p.mouthOpen * 1.65 * (1 - p.mouthRound * 0.1);
      const f = (v: number) => v.toFixed(2);
      const d = `M${f(cx - w)} ${f(ly)} C${f(cx - w * 0.55)} ${f(up)} ${f(cx + w * 0.55)} ${f(up)} ${f(cx + w)} ${f(ry)} C${f(cx + w * 0.95)} ${f(low)} ${f(cx - w * 0.95)} ${f(low)} ${f(cx - w)} ${f(ly)} Z`;
      parts.mouth.setAttribute('d', d);
      parts.mouthClip.setAttribute('d', d);
      parts.tongue.setAttribute('cx', cx.toFixed(2));
      parts.tongue.setAttribute('cy', (corner + (low - corner) * 0.75 - p.mouthOpen * 0.3).toFixed(2)); // a cubic reaches 3/4 of its control
      parts.tongue.setAttribute('rx', Math.max(0, w * 0.6).toFixed(2));
      parts.tongue.setAttribute('ry', Math.max(0, p.mouthOpen * 0.42).toFixed(2));
      parts.tongue.setAttribute('opacity', smooth(5, 11, p.mouthOpen).toFixed(3));
      parts.cheeks.forEach((c) => c.setAttribute('opacity', (0.75 * p.cheek).toFixed(3)));
      // lamp
      const [lr, lg, lb] = p.lamp.map((v) => Math.round(v));
      const col = `rgb(${lr},${lg},${lb})`;
      parts.lamp.setAttribute('fill', col);
      parts.glowStop.setAttribute('stop-color', col);
      parts.glow.setAttribute('opacity', (0.25 + p.lampGlow * 0.75).toFixed(3));
      parts.glow.setAttribute('r', Math.max(0, 26 + p.lampGlow * 18).toFixed(2));
      parts.halo.setAttribute('fill', col);
      parts.halo.setAttribute('opacity', (p.halo * 0.45).toFixed(3));
      parts.halo.setAttribute('r', Math.max(0, 18 + p.halo * 12).toFixed(2));
      if (parts.shadow) {
        parts.shadow.setAttribute('rx', Math.max(0, 118 * p.shadow * p.sx).toFixed(2));
        parts.shadow.setAttribute('opacity', (0.22 * p.shadow).toFixed(3));
      }
      drawBursts(parts.fx, r, disc);
    }

    wake();
    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      document.removeEventListener('visibilitychange', onVis);
      if (tracking && !--pointerUsers) window.removeEventListener('pointermove', onPointer);
    };
  }, [uid, disc]);

  return (
    <svg
      ref={svg}
      className={`lc ${disc ? 'lc-disc' : 'lc-free'} ${className}`}
      viewBox={disc ? '0 0 512 512' : '20 40 472 452'} width={size} height={size}
      role={title ? 'img' : undefined} aria-hidden={title ? undefined : true} aria-label={title}
    >
      <defs>
        <radialGradient id={`lc-g-${uid}`}><stop offset="0" stopColor="#5BD18B" stopOpacity="0.85" /><stop offset="1" stopColor="#5BD18B" stopOpacity="0" /></radialGradient>
        <clipPath id={`lc-m-${uid}`}><path d="" /></clipPath>
        {!disc && <filter id={`lc-s-${uid}`} x="-50%" y="-200%" width="200%" height="500%"><feGaussianBlur stdDeviation="9" /></filter>}
      </defs>
      {disc && <circle cx="256" cy="256" r="256" fill="#0E5C4D" />}
      {!disc && <ellipse className="lc-shadow" cx="256" cy="436" rx="118" ry="15" fill="#0b2b24" opacity="0.22" filter={`url(#lc-s-${uid})`} />}
      <g className="lc-body">
        <rect x="316" y="150" width="26" height="62" rx="5" fill="#A44F2C" />
        <circle className="lc-glow" cx="329" cy="132" r="30" fill={`url(#lc-g-${uid})`} />
        <circle className="lc-halo" cx="329" cy="130" r="20" fill="#5BD18B" opacity="0" />
        <circle className="lc-lamp" cx="329" cy="132" r="12" fill="#5BD18B" />
        <rect x="146" y="214" width="220" height="182" rx="38" fill={PAPER} />
        <path d="M256 124 L388 232 L124 232 Z" fill="#C8643B" stroke="#C8643B" strokeWidth="26" strokeLinejoin="round" />
        <ellipse className="lc-cheek0" cx="196" cy="326" rx="15" ry="8" fill="#F2A6A0" opacity="0.6" />
        <ellipse className="lc-cheek1" cx="316" cy="326" rx="15" ry="8" fill="#F2A6A0" opacity="0.6" />
        {EYES.map((x, i) => (
          <g key={x} className={`lc-eye${i}`}>
            <ellipse className="lc-ball" cx={x} cy={EYE_Y} rx="13" ry="17" fill={INK} />
            <circle className="lc-shine" cx={x + 4} cy={EYE_Y - 6} r="4" fill="#fff" />
            <path className="lc-lid" d="" fill="none" stroke={INK} strokeWidth="9" strokeLinecap="round" opacity="0" />
          </g>
        ))}
        <path className="lc-brow0" d="" fill="none" stroke={INK} strokeWidth="9" strokeLinecap="round" opacity="0" />
        <path className="lc-brow1" d="" fill="none" stroke={INK} strokeWidth="9" strokeLinecap="round" opacity="0" />
        <path className="lc-mouth" d="M240 333 Q256 344 272 333 Q256 344 240 333 Z" fill={INK} stroke={INK} strokeWidth="9" strokeLinejoin="round" strokeLinecap="round" />
        <g clipPath={`url(#lc-m-${uid})`}><ellipse className="lc-tongue" cx="256" cy="340" rx="8" ry="0" fill="#E07B7F" opacity="0" /></g>
        <g className="lc-fx" />
      </g>
    </svg>
  );
}

const mix = (a: number, b: number, k: number) => a + (b - a) * k;
const smooth = (a: number, b: number, v: number) => { const k = Math.min(1, Math.max(0, (v - a) / (b - a))); return k * k * (3 - 2 * k); };

// The z's, thought bubbles and sparkles: a few elements kept in the fx group, created as needed and moved per frame.
const NS = 'http://www.w3.org/2000/svg';
function drawBursts(fx: SVGGElement, r: Rig, onDisc: boolean) {
  // light on the green disc, dark green on the page
  const [fill, edge] = onDisc ? [PAPER, '#0E5C4D'] : ['#1f4d40', PAPER];
  const t = r.time;
  const want = r.bursts;
  while (fx.childElementCount < want.length) fx.appendChild(document.createElementNS(NS, 'g'));
  while (fx.childElementCount > want.length) fx.lastElementChild!.remove();
  want.forEach((b, i) => {
    const g = fx.children[i] as SVGGElement;
    if (g.dataset.kind !== b.kind) {
      g.dataset.kind = b.kind;
      g.innerHTML = b.kind === 'z'
        ? `<text font-family="Helvetica, Arial, sans-serif" font-weight="700" font-size="42" fill="${fill}" stroke="${edge}" stroke-width="3" paint-order="stroke">z</text>`
        : b.kind === 'bubble'
          ? `<circle r="10" fill="${fill === PAPER ? PAPER : '#fff'}" stroke="${onDisc ? edge : '#1f4d40'}" stroke-width="3" />`
          : '<path d="M0 -16 L4 -4 L16 0 L4 4 L0 16 L-4 4 L-16 0 L-4 -4 Z" fill="#F2A33A" />';
    }
    const k = (t - b.born) / (b.kind === 'bubble' ? 1.6 : 3);
    if (k >= 1) { g.setAttribute('opacity', '0'); return; }
    const fade = Math.min(1, k * 5) * (1 - Math.max(0, (k - 0.6) / 0.4));
    let x: number, y: number, s: number, rot = 0;
    if (b.kind === 'z') {
      x = 372 + k * 70 + Math.sin(k * 6 + b.seed * 6) * 10; y = 222 - k * 120; s = 0.55 + k * 0.7; rot = -12 + Math.sin(k * 4 + b.seed) * 14;
    } else if (b.kind === 'bubble') {
      x = 384 + k * 40 + b.seed * 10; y = 222 - k * 80; s = 0.5 + k * 1.1;
    } else {
      x = 380 + b.seed * 50; y = 180 - b.seed * 40; s = Math.sin(k * Math.PI) * 1.2; rot = k * 90;
    }
    g.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${rot.toFixed(1)}) scale(${s.toFixed(3)})`);
    g.setAttribute('opacity', fade.toFixed(3));
  });
}
