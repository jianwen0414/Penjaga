// Small pieces of the talking UI used by the chat (src/screens/Agent.tsx).
import { useEffect, useRef } from 'react';
import { useLive } from '../api/client';

export function MicIcon({ size = 17 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
    </svg>
  );
}

/** The ask pill while listening: level bars that follow the owner's voice, and what to do. */
export function ListeningLine({ level, hint }: { level: () => number; hint: string }) {
  const bars = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let raf = 0;
    const seeds = [0.55, 0.9, 1, 0.8, 0.6];
    const tick = (now: number) => {
      const l = level();
      bars.current?.querySelectorAll('i').forEach((b, i) => {
        const wobble = 0.65 + 0.35 * Math.sin(now / (90 + i * 23) + i * 1.7);
        (b as HTMLElement).style.setProperty('--h', String(Math.min(1, 0.08 + l * seeds[i] * wobble * 1.3)));
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [level]);
  return (
    <span className="ask-listening" role="status">
      <span className="bars" ref={bars} aria-hidden="true"><i /><i /><i /><i /><i /></span>
      <span className="hint">{hint}</span>
    </span>
  );
}

/** The emergency's steps: the walk, where the thing is, then the reviewed safety lines; the current one is marked. */
export function EmSteps({ steps, now = 0 }: { steps: string[]; now?: number }) {
  return (
    <ol className="em-steps">
      {steps.map((s, i) => <li key={s} className={i < now ? 'done' : i === now ? 'now' : ''}>{s}</li>)}
    </ol>
  );
}

/** After the owner says the main valve is closed: the water meter, live, until it shows no flow from the mains.
 * It says only what the meter shows (water stored in the roof tank can still run, so it never says the leak stopped). */
export function WaterCheck({ onStopped }: { onStopped?: () => void }) {
  const live = useLive();
  // only readings that arrived after the valve was closed count (the first one can be the reading from before)
  const readings = useRef(-1);
  const last = useRef<typeof live | null>(null);
  if (last.current !== live) { last.current = live; readings.current++; }
  const stopped = readings.current >= 2 && live.water_lpm < 0.05;
  const told = useRef(false);
  useEffect(() => { if (stopped && !told.current) { told.current = true; onStopped?.(); } }, [stopped, onStopped]);
  return (
    <div className={`em-verify ${stopped ? 'ok' : ''}`} role="status">
      <i aria-hidden="true" />
      {stopped
        ? <span>Your water meter has stopped: <b>{live.water_lpm.toFixed(2)} L/min</b>. No more water is coming in from the mains.</span>
        : <span>Watching your water meter: <b>{live.water_lpm.toFixed(2)} L/min</b></span>}
    </div>
  );
}
