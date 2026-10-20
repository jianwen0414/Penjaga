// Resting: when nobody has touched the app for a while, the panel steps aside, the house turns slowly, and Penjaga
// dozes at the bottom of the screen, with the time and how the home is. The house stays the picture. Any touch,
// move or key wakes it; tapping Penjaga wakes it and starts listening. If something needs a look, Penjaga stays
// awake (alert) instead of sleeping.
import { useEffect, useState } from 'react';
import LiveCharacter, { type Mood } from '../character/LiveCharacter';

export interface RestingProps {
  status: 'all_quiet' | 'attention' | 'urgent';
  line: string;
  houseNow: () => number;
  leaving: boolean;
  /** Woken by a touch (talk: false) or by tapping Penjaga (talk: true). */
  onWake: (talk: boolean) => void;
}

export default function Resting({ status, line, houseNow, leaving, onWake }: RestingProps) {
  const [now, setNow] = useState(houseNow());
  useEffect(() => { const t = setInterval(() => setNow(houseNow()), 1000); return () => clearInterval(t); }, [houseNow]);
  // any movement wakes; a short grace period stops the very move that ended a long wait from bouncing it back
  useEffect(() => {
    if (leaving) return;
    const since = performance.now();
    const wake = (e: Event) => {
      if (performance.now() - since < 600) return;
      if ((e.target as Element | null)?.closest?.('.resting-char button')) return; // its own click handles it
      onWake(false);
    };
    const evs = ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart'] as const;
    evs.forEach((ev) => window.addEventListener(ev, wake, { capture: true, passive: true }));
    return () => evs.forEach((ev) => window.removeEventListener(ev, wake, { capture: true }));
  }, [leaving, onWake]);
  const d = new Date(now);
  const time = d.toLocaleTimeString('en-MY', { hour: 'numeric', minute: '2-digit', hour12: true }).replace(/\s?([ap])\.?m\.?/i, ' $1m');
  const date = d.toLocaleDateString('en-MY', { weekday: 'long', day: 'numeric', month: 'long' });
  const mood: Mood = leaving ? (status === 'all_quiet' ? 'calm' : 'alert') : status === 'all_quiet' ? 'sleeping' : status === 'urgent' ? 'concerned' : 'alert';
  const size = Math.round(Math.min(260, Math.max(150, innerHeight * 0.26)));
  return (
    <div className={`resting ${leaving ? 'leaving' : ''}`} role="dialog" aria-label="Penjaga is resting. Touch anywhere to wake it.">
      <div className="resting-veil" />
      <div className="resting-clock" aria-hidden="true">
        <div className="time">{time}</div>
        <div className="date">{date}</div>
        <div className={`status ${status}`}><i />{line}</div>
      </div>
      <div className="resting-char">
        <button onClick={() => onWake(true)} aria-label="Talk to Penjaga">
          <LiveCharacter mood={mood} size={size} disc={false} track={!leaving && status !== 'all_quiet'} />
        </button>
        <span className="resting-hint">{status === 'all_quiet' ? 'Tap Penjaga to talk · move to wake' : 'Tap Penjaga to talk'}</span>
      </div>
    </div>
  );
}
