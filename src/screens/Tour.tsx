import { useState } from 'react';
import { useNavigate } from 'react-router';
import { api } from '../api/client';
import { ViewerSlot, useEngine, type Engine } from '../app/engine';
import { Icon } from '../components/ui';

// First-run tour (the part of onboarding kept for the first submission): three stops in the 3D home.
const STOPS = [
  { kicker: 'Welcome home, Aiman', title: 'This is your home, as it was built.', body: 'Representative of an SDP Elmina Green Six double-storey terrace: 4 bedrooms, 4 bathrooms, handed over June 2024.' },
  { kicker: 'Every room', title: 'Two floors, every wall in place.', body: 'Drag to look around, pinch to zoom, tap anything to see its record.' },
  { kicker: 'The part you never see', title: 'Penjaga knows what runs inside your walls.', body: 'Every pipe and cable, by name. When something goes wrong, it shows you exactly where.' },
  { kicker: 'Your privacy', title: 'It watches the building, not the people.', body: 'Only your water meter and electrical board. No cameras, no microphones. You can switch any layer off.' },
];

export default function Tour() {
  const [i, setI] = useState(0);
  const { engine } = useEngine();
  const nav = useNavigate();
  const apply = (e: Engine, k: number) => {
    e.clearFocus({ reframe: false });
    e.setInteractive(true);
    if (k === 0) { e.setLayer('water', false); e.setLayer('electrical', false); e.setView('split'); e.setAutoRotate(true); }
    if (k === 1) { e.setAutoRotate(false); e.setView('stack'); }
    if (k === 2) { e.setView('split'); e.setLayer('water', true); e.setLayer('electrical', true); }
    if (k === 3) { e.setLayer('water', false); e.setLayer('electrical', false); e.frame(); }
  };
  const go = (k: number) => {
    if (k >= STOPS.length) { finish(); return; }
    setI(k);
    if (engine) apply(engine, k);
  };
  const finish = () => {
    api.markToured();
    if (engine) { engine.setAutoRotate(false); engine.setLayer('water', false); engine.setLayer('electrical', false); engine.setView('split'); }
    nav('/');
  };
  const s = STOPS[i];
  return (
    <div className="tour">
      <ViewerSlot label="A tour of your home in 3D" onReady={(e) => { e.setInsets({ left: 0 }); apply(e, 0); return () => e.setAutoRotate(false); }} />
      <div className="tour-card card pad">
        <div className="steps" aria-hidden>{STOPS.map((_, k) => <i key={k} className={k <= i ? 'on' : ''} />)}</div>
        <div className="label">{s.kicker}</div>
        <h1>{s.title}</h1>
        <p className="muted" style={{ marginTop: 0 }}>{s.body}</p>
        <div className="row">
          <button className="btn ghost" onClick={finish}>Skip</button>
          <button className="btn primary" style={{ flex: 1 }} onClick={() => go(i + 1)}>{i === STOPS.length - 1 ? 'Go to my home' : 'Next'} <Icon name="chevron" size={16} /></button>
        </div>
      </div>
    </div>
  );
}
