import { useView, api } from '../api/client';

const LAYERS = [
  { id: 'L1', name: 'Water leaks', reads: 'Your water meter (flow) and supply pressure.', never: 'Which tap or room you use, or when you shower.' },
  { id: 'L2', name: 'Electrical safety', reads: 'Current on each circuit, earth leakage and safety-switch trips at the distribution board.', never: 'What you watch, cook or charge.' },
  { id: 'L3', name: 'Appliance health', reads: 'Power drawn by aircons and the fridge, with outdoor weather.', never: 'Room temperatures or who is in a room.' },
  { id: 'L4', name: 'Maintenance', reads: 'Install dates and manuals from your handover record.', never: 'Anything from sensors.' },
  { id: 'L5', name: 'Management notices', reads: 'Notices your township management posts for your street.', never: 'Nothing is sent to management unless you tap Send.' },
] as const;

export default function Settings({ embedded = false }: { embedded?: boolean }) {
  const v = useView();
  return (
    <div className={embedded ? '' : 'page'} style={embedded ? undefined : { maxWidth: 760 }}>
      {!embedded && <h1 className="serif" style={{ fontSize: 34 }}>Settings & privacy</h1>}
      <p className="muted">Penjaga senses the building, not the people. No cameras, no microphones. Turn any layer off and it stops processing straight away.</p>
      <div className="card" style={{ marginTop: 18 }}>
        {LAYERS.map((l) => {
          const on = v.consent[l.id];
          return (
            <div className="list-item" key={l.id} style={{ alignItems: 'flex-start' }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600 }}>{l.name}</div>
                <div className="small" style={{ marginTop: 4 }}><span className="muted">Reads:</span> {l.reads}</div>
                <div className="small"><span className="muted">Never:</span> {l.never}</div>
                {!on && <div className="small" style={{ color: 'var(--act)', marginTop: 4 }}>Off. Penjaga isn't processing this layer.</div>}
              </div>
              <button role="switch" aria-checked={on} aria-label={l.name} onClick={() => api.setConsent(l.id, !on)}
                style={{ width: 52, height: 30, borderRadius: 999, border: 0, background: on ? 'var(--accent)' : 'var(--sunk)', position: 'relative', flex: 'none' }}>
                <span style={{ position: 'absolute', top: 3, left: on ? 25 : 3, width: 24, height: 24, borderRadius: '50%', background: '#fff', transition: 'left 0.2s' }} />
              </button>
            </div>
          );
        })}
      </div>
      <div className="card pad" style={{ marginTop: 18 }}>
        <div className="label">Where your data is processed</div>
        <p className="small" style={{ marginBottom: 0 }}>In the product, detection runs on a hub inside your home and only alerts leave it. In this prototype, processing runs in the cloud, and every sensor reading is simulated.</p>
      </div>
    </div>
  );
}
