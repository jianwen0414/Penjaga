// Penjaga, out on the stage: while it listens, talks or walks someone through an emergency, the little character in
// the panel header flies out to the open space beside the panel, big enough to see it think, listen and talk, and
// flies back when it's done. It moves (a shared-element transition from the header avatar) rather than appearing,
// so it reads as the same Penjaga. A short wait before it goes back keeps it from bouncing between sentences.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import LiveCharacter, { type Activity, type Mood } from '../character/LiveCharacter';

export interface StageBuddyProps {
  /** Out on the stage now (listening, speaking, an emergency). */
  present: boolean;
  mood: Mood;
  activity: Activity;
  voice: () => number;
  mic: () => number;
  /** The header avatar it comes from and goes back to. */
  home: () => HTMLElement | null;
  caption: string;
  onTap: () => void;
  /** Tell the header to hide its avatar while it's out (it is "here" now). */
  onAway: (away: boolean) => void;
}

const LINGER_MS = 1100;
const reduce = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

export default function StageBuddy({ present, mood, activity, voice, mic, home, caption, onTap, onAway }: StageBuddyProps) {
  const [phase, setPhase] = useState<'off' | 'in' | 'leaving'>('off');
  const box = useRef<HTMLDivElement>(null);
  const size = useSize();

  // come out at once; go back after a moment of quiet (and not if it's needed again meanwhile)
  useEffect(() => {
    if (present) { setPhase('in'); return; }
    if (phase !== 'in') return;
    const t = setTimeout(() => setPhase('leaving'), LINGER_MS);
    return () => clearTimeout(t);
  }, [present]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { onAway(phase !== 'off'); }, [phase]); // eslint-disable-line react-hooks/exhaustive-deps

  // the flight: from the header avatar to here (FLIP), and back
  const from = () => {
    const el = box.current, h = home();
    if (!el || !h) return null;
    const a = h.getBoundingClientRect(), b = el.getBoundingClientRect();
    const s = Math.max(0.12, a.width / b.width);
    return `translate(${a.left + a.width / 2 - (b.left + b.width / 2)}px, ${a.top + a.height / 2 - (b.top + b.height / 2)}px) scale(${s})`;
  };
  useLayoutEffect(() => {
    const el = box.current;
    if (phase !== 'in' || !el) return;
    const t = from();
    if (reduce() || !t) { el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 180 }); return; }
    el.animate(
      [{ transform: t, opacity: 0.3 }, { transform: 'none', opacity: 1 }],
      { duration: 620, easing: 'cubic-bezier(0.34, 1.36, 0.64, 1)' },
    );
  }, [phase === 'in']); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = box.current;
    if (phase !== 'leaving' || !el) return;
    const t = from();
    const anim = reduce() || !t
      ? el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, fill: 'forwards' })
      : el.animate([{ transform: 'none', opacity: 1 }, { transform: t, opacity: 0.2 }], { duration: 420, easing: 'cubic-bezier(0.55, 0, 0.75, 0.2)', fill: 'forwards' });
    anim.onfinish = () => setPhase('off');
    return () => anim.cancel();
  }, [phase]); // eslint-disable-line react-hooks/exhaustive-deps
  // needed again while flying home: turn round
  useEffect(() => { if (present && phase === 'leaving') setPhase('in'); }, [present, phase]);

  if (phase === 'off') return null;
  return (
    <div ref={box} className={`stage-buddy ${activity} ${mood}`} style={{ ['--buddy' as string]: `${size}px` }}>
      <button onClick={onTap} aria-label={caption}>
        <LiveCharacter mood={mood} activity={activity} size={size} disc={false} track voice={voice} mic={mic} />
      </button>
      <span className="stage-buddy-cap" aria-hidden="true">{caption}</span>
    </div>
  );
}

// big enough to read its face, small enough to leave the house the picture
function useSize(): number {
  const pick = () => (innerWidth >= 960 ? Math.round(Math.min(196, Math.max(140, innerHeight * 0.21))) : 96);
  const [s, setS] = useState(pick);
  useEffect(() => {
    const on = () => setS(pick());
    addEventListener('resize', on);
    return () => removeEventListener('resize', on);
  }, []);
  return s;
}
