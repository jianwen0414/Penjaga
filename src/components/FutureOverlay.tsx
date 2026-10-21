// "What happens if I wait?" in the homeowner app: the house plays the future of one case, from now to the end of its
// own horizon (two weeks for a burst, a year for a slow leak), and a small readout counts it up. The numbers are the
// case's own measurements carried forward (src/outlook/project.ts); nothing here is a guess.
//
// Two ways to drive it: the owner asks (it plays once, with pause, replay and "Back to now"), or the presenter's Lab
// slider mirrors into this screen (remote: the Lab moves it, no controls but closing).
import { useEffect, useMemo, useRef, useState } from 'react';
import { useEngine } from '../app/engine';
import { toSpecIds } from '../api/spec-ids';
import { Icon } from './ui';
import { milestones, project, when, type Outlook } from '../outlook/project';
import '../styles/future.css';

const PLAY_MS = 9000;
const reduce = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

export type FutureMode = { kind: 'play' } | { kind: 'remote'; x: number };

export default function FutureOverlay({ outlook: o, mode, onClose }: { outlook: Outlook; mode: FutureMode; onClose: () => void }) {
  const { engine } = useEngine();
  const max = o.horizon?.max ?? 1;
  const unit = o.horizon?.unit ?? 'day';
  // slider position 0..1; time grows with its square, so the first days get room before the months fly by
  const [s, setS] = useState(mode.kind === 'remote' ? Math.sqrt(Math.min(1, Math.max(0, mode.x / max))) : 0);
  const [playing, setPlaying] = useState(mode.kind === 'play');
  const raf = useRef(0);
  const remote = mode.kind === 'remote';
  useEffect(() => { if (mode.kind === 'remote') setS(Math.sqrt(Math.min(1, Math.max(0, mode.x / max)))); }, [mode, max]);

  // play to the end once (reduced motion: straight to the end)
  useEffect(() => {
    if (remote || !playing) return;
    if (reduce()) { setS(1); setPlaying(false); return; }
    const from = s >= 0.999 ? 0 : s;
    const t0 = performance.now();
    const span = PLAY_MS * (1 - from);
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / span);
      setS(from + (1 - from) * k);
      if (k < 1) raf.current = requestAnimationFrame(step); else setPlaying(false);
    };
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, remote]); // eslint-disable-line react-hooks/exhaustive-deps

  const x = max * s * s;
  const p = project(o, x);
  const ids = useMemo(() => toSpecIds(o.focus.object_ids), [o.focus.object_ids]);
  const maxWater = useMemo(() => (o.kind === 'water' ? project(o, max).waterM3 : null), [o, max]);
  const marks = useMemo(() => milestones(o), [o]);

  // the house follows
  useEffect(() => {
    if (!engine) return;
    engine.projection({ key: `future:${o.case_id}`, layer: o.focus.layer, ids, severity: p.severity, label: p.label, waterM3: p.waterM3, maxWaterM3: maxWater, fit: 1.05 });
  }, [engine, o.case_id, p.label, p.severity, p.waterM3]); // eslint-disable-line react-hooks/exhaustive-deps
  // put away on the way out (a no-op if something else took the stage first); the caller reframes if it wants
  useEffect(() => () => { engine?.clearProjection(); }, [engine]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [onClose]);

  const done = s >= 0.999;
  return (
    <section className="future" role="region" aria-label="What happens if you wait" aria-live="polite">
      <header className="future-head">
        <span className="future-eyebrow">{remote ? 'If this keeps going' : 'If nothing changes'}</span>
        <button className="future-x" onClick={onClose} aria-label="Back to now"><Icon name="close" size={14} /></button>
      </header>
      <div className="future-when">{s === 0 ? 'Now' : `In ${when(x, unit)}`}</div>
      <div className="future-figs">
        {p.figures.slice(0, 2).map(([v, w]) => <div key={w}><b>{v}</b><span>{w}</span></div>)}
      </div>
      <div className="future-track" aria-hidden="true">
        <i style={{ width: `${s * 100}%` }} />
        {marks.map(([at, what]) => <span key={what} title={what} style={{ left: `${Math.sqrt(Math.min(1, at / max)) * 100}%` }} className={x >= at ? 'past' : ''} />)}
      </div>
      <div className="future-ends" aria-hidden="true"><span>Now</span><span>{when(max, unit)}</span></div>
      {!remote && (
        <div className="future-ctl">
          <button className="a-chip primary" onClick={() => { if (done) setS(0); setPlaying((v) => (done ? true : !v)); }}>
            {playing ? 'Pause' : done ? <><Icon name="replay" size={14} />Play again</> : 'Play'}
          </button>
          <button className="a-chip" onClick={onClose}>Back to now</button>
        </div>
      )}
      <p className="future-basis">{o.basis}</p>
    </section>
  );
}
