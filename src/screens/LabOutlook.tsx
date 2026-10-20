// The Lab's "what if they wait?": pick an open case and drag through the coming days (or hours); the house shows
// where it gets to. Only cases whose finding measured something that adds up can be carried forward (a leak's litres
// a day, an appliance's extra energy, rising earth leakage, a draining tank); the others say why not. The numbers
// come from the Core API (GET /api/v1/outlook: the case's own measurements and cost of waiting); this only carries
// them forward. Operator only: the homeowner app never shows a slider into the future.
import { useEffect, useMemo, useRef, useState } from 'react';
import { ViewerSlot, useEngine, type Engine } from '../app/engine';
import { Icon } from '../components/ui';
import { toSpecIds } from '../api/spec-ids';
import { milestones, project, when, type Outlook } from '../lab/outlook';

const POLL_MS = 5000;
const PLAY_MS = 8000;

export default function LabOutlook() {
  const { engine } = useEngine();
  const [list, setList] = useState<Outlook[] | null>(null);
  const [reach, setReach] = useState(true);
  const [pick, setPick] = useState<string | null>(null);
  const [s, setS] = useState(0); // slider position 0..1 (time grows with its square: fine control early on)
  const [playing, setPlaying] = useState(false);
  const raf = useRef(0);

  useEffect(() => {
    let alive = true;
    const load = () => fetch('/lab/api/v1/outlook')
      .then((r) => (r.ok ? r.json() as Promise<Outlook[]> : Promise.reject(new Error(String(r.status)))))
      .then((l) => { if (alive) { setList(l); setReach(true); } })
      .catch(() => { if (alive) setReach(false); });
    void load();
    const t = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const open = list ?? [];
  const ready = open.filter((o) => o.projectable && o.horizon);
  const sel = ready.find((o) => o.case_id === pick) ?? ready[0] ?? null;
  const max = sel?.horizon?.max ?? 1;
  const unit = sel?.horizon?.unit ?? 'day';
  const x = max * s * s;
  const p = sel ? project(sel, x) : null;
  const marks = useMemo(() => (sel ? milestones(sel) : []), [sel]);
  const ids = useMemo(() => (sel ? toSpecIds(sel.focus.object_ids) : []), [sel]);
  const maxWater = useMemo(() => (sel?.kind === 'water' ? project(sel, max).waterM3 : null), [sel, max]);

  // a new case starts from today
  useEffect(() => { setS(0); setPlaying(false); }, [sel?.case_id]);
  // the house follows the slider
  useEffect(() => {
    if (!engine) return;
    if (!sel || !p) { engine.clearProjection(); return; }
    engine.projection({ key: sel.case_id, layer: sel.focus.layer, ids, severity: p.severity, label: p.label, waterM3: p.waterM3, maxWaterM3: maxWater });
  }, [engine, sel?.case_id, p?.label, p?.severity, p?.waterM3]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { engine?.clearProjection(); }, [engine]);

  // play: run the slider to the end (or from the start if it's already there)
  useEffect(() => {
    if (!playing) return;
    const from = s >= 0.999 ? 0 : s;
    const t0 = performance.now();
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / (PLAY_MS * (1 - from)));
      setS(from + (1 - from) * k);
      if (k < 1) raf.current = requestAnimationFrame(step); else setPlaying(false);
    };
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [playing]); // eslint-disable-line react-hooks/exhaustive-deps

  const onReady = (e: Engine) => { e.setInteractive(true); e.setAutoRotate(false); e.setInsets({ left: 0 }); return () => e.clearProjection(); };
  const posOf = (v: number) => `${Math.sqrt(Math.min(1, v / max)) * 100}%`;

  return (
    <section className="lab-card tone-score lab-outlook">
      <div className="lab-sec-head">
        <span className="lab-sec-icon"><Icon name="replay" size={16} /></span>
        <div>
          <h2>What if they wait?</h2>
          <p>Carry an open case forward and watch where it gets to. Only what the case measured, at the rate it measured: the homeowner never sees this slider.</p>
        </div>
        {ready.length > 0 && <span className="lab-count"><i />{ready.length} to carry forward</span>}
      </div>

      {ready.length > 1 && (
        <div className="lab-tiles lo-cases" role="radiogroup" aria-label="Case">
          {ready.map((o) => (
            <button key={o.case_id} role="radio" aria-checked={o.case_id === sel?.case_id} className={`lab-tile ${o.case_id === sel?.case_id ? 'on' : ''}`} onClick={() => setPick(o.case_id)}>
              <span>{o.title}</span>
            </button>
          ))}
        </div>
      )}

      <div className="lo-stage">
        <ViewerSlot className="lo-view" label="The house, carried forward" onReady={onReady} />
        {!sel && (
          <div className="lo-empty">
            {!reach ? <p>Can't reach the Core API. Is the backend running?</p> : list === null ? <p>Loading the open cases…</p> : (
              <>
                <b>Nothing to carry forward yet</b>
                <p>Start a leak, an appliance fault or rising earth leakage on the left, and wait for its case: the projection uses the case's own measurements.</p>
                {open.filter((o) => !o.projectable).map((o) => <p key={o.case_id} className="lo-why"><b>{o.title}:</b> {o.why_not}</p>)}
              </>
            )}
          </div>
        )}
        {sel && p && (
          <div className="lo-readout" aria-live="polite">
            <div className="lo-when">{s === 0 ? 'Now' : `In ${when(x, unit)}`}</div>
            <div className="lo-figs">{p.figures.map(([v, w]) => <div key={w}><b>{v}</b><span>{w}</span></div>)}</div>
          </div>
        )}
      </div>

      {sel && (
        <div className="lo-controls">
          <button className="lab-btn primary lo-play" onClick={() => setPlaying((v) => !v)} aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
              : <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5v14l12-7z" /></svg>}
          </button>
          <div className="lo-track">
            <input type="range" min={0} max={1000} value={Math.round(s * 1000)} onChange={(e) => { setPlaying(false); setS(Number(e.target.value) / 1000); }} aria-label={`How far ahead, up to ${when(max, unit)}`} aria-valuetext={s === 0 ? 'now' : `in ${when(x, unit)}`} />
            <div className="lo-marks" aria-hidden="true">
              {marks.reduce<{ at: number; what: string; low: boolean }[]>((out, [at, what]) => {
                // a label too close to the one before it goes to the other row
                const prev = out[out.length - 1];
                const close = !!prev && Math.sqrt(at / max) - Math.sqrt(prev.at / max) < 0.12;
                return [...out, { at, what, low: close ? !prev.low : false }];
              }, []).map(({ at, what, low }) => <span key={what} style={{ left: posOf(at) }} className={`${x >= at ? 'past' : ''} ${low ? 'low' : ''}`}><i />{what}</span>)}
            </div>
            <div className="lo-ends" aria-hidden="true"><span>Now</span><span>{when(max, unit)}</span></div>
          </div>
        </div>
      )}
      {sel && (
        <p className="lo-basis">
          <b>{sel.title}</b> · {sel.location}. {sel.basis}
          {sel.cost?.how ? <> Cost: {sel.cost.how}</> : sel.cost?.why_not ? <> No ringgit figure: {sel.cost.why_not}</> : null}
        </p>
      )}
    </section>
  );
}
