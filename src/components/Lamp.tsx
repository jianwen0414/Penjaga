// The notification lamp: a bell-lamp hanging from the top of the screen. While there is nothing new it is tucked up
// with only its rim showing; when something happens it drops down, lights up and sways, and stays until the owner
// taps it (to look at the newest thing) or pulls it down (to see every notification).
// The bell is the "bell-notify" component's CSS art (src/styles/lamp.css), without its button.
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import '../styles/lamp.css';

const PULL_OPEN = 64; // px of pull that opens the list

export default function Lamp({ count, tone, onLook, onPull }: {
  /** New notifications; the lamp is down and lit while this is above 0. */
  count: number;
  tone: 'urgent' | 'act' | 'calm';
  /** Tap: look into the newest notification. */
  onLook: () => void;
  /** Pull down (or tap with nothing new): show every notification. */
  onPull: () => void;
}) {
  const drag = useRef<HTMLDivElement>(null);
  const grab = useRef<{ y: number; pull: number; moved: boolean } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [ring, setRing] = useState(false);

  // A new notification rings the lamp: a few big swings, then the slow sway.
  const last = useRef(count);
  useEffect(() => {
    if (count > last.current) {
      setRing(true);
      const t = setTimeout(() => setRing(false), 1700);
      last.current = count;
      return () => clearTimeout(t);
    }
    last.current = count;
  }, [count]);

  const tap = () => (count > 0 ? onLook() : onPull());
  const place = (px: number, spring: boolean) => {
    const el = drag.current;
    if (!el) return;
    el.classList.toggle('springing', spring);
    el.style.transform = px ? `translateY(${px}px)` : '';
  };

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    grab.current = { y: e.clientY, pull: 0, moved: false };
    setDragging(true);
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const g = grab.current;
    if (!g) return;
    const dy = Math.max(0, e.clientY - g.y);
    if (dy > 4) g.moved = true;
    g.pull = dy < 120 ? dy * 0.8 : 96 + (dy - 120) * 0.25; // the rope gives, then stiffens
    place(g.pull, false);
  };
  const onUp = () => {
    const g = grab.current;
    grab.current = null;
    setDragging(false);
    place(0, true);
    if (!g) return;
    if (!g.moved) tap();
    else if (g.pull >= PULL_OPEN) onPull();
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tap(); }
    if (e.key === 'ArrowDown') { e.preventDefault(); onPull(); }
  };

  const on = count > 0;
  return (
    <div className={`lamp ${on ? 'down' : ''} ${ring ? 'ring' : ''} ${dragging ? 'dragging' : ''}`}>
      <div className="lamp-hang">
        <div className="lamp-drag" ref={drag}>
          <div className="lamp-bell" style={{ fontSize: '1.1px', ['--base-clr' as string]: '#b7b5b4', ['--degofrot' as string]: 0.8 }}>
            <div
              className={`bell-container ${on ? '' : 'off'}`}
              role="button"
              tabIndex={0}
              aria-label={on ? `${count} new notification${count > 1 ? 's' : ''}. Press to look, or pull down to see them all.` : 'Notifications. Pull down to see them all.'}
              onPointerDown={onDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerCancel={onUp}
              onKeyDown={onKey}
            >
              <div className="rope" />
              <div className="bell-top" />
              <div className="bell-base" />
              <div className="bell-base" />
              <div className="shadow-l1" />
              <div className="shadow-l2" />
              <div className="left-glow" />
              <div className="left-glow2" />
              <div className="r-glow" />
              <div className="r-glow2" />
              <div className="mid-ring" />
              <div className="mid-ring small" />
              <div className="glow" />
              <div className="glow2" />
              <div className="bell-buff-t" />
              <div className="bell-buff" />
              <div className="bell-btm" />
              <div className="bell-btm2" />
              <div className="bell-ring-container">
                <div className="bell-ring" />
                <div className="bell-rays" />
              </div>
              <div className="volumetric">
                <div className="vl" />
                <div className="vr" />
              </div>
            </div>
          </div>
          {on && <span key={count} className={`lamp-count ${tone === 'urgent' ? '' : tone}`} aria-hidden="true">{count}</span>}
        </div>
        <span className="lamp-hint" aria-hidden="true">Tap to look · pull for all</span>
      </div>
    </div>
  );
}
