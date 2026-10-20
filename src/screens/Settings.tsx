import { useView, api } from '../api/client';
import { setVoicePref, useVoicePrefs, type VoicePrefs } from '../voice/prefs';
import { canListen } from '../voice/listen';

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
      <p className="muted">Penjaga senses the building, not the people. No cameras or microphones in your home. Turn any layer off and it stops processing straight away.</p>
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
      <VoiceSettings />
      <div className="card pad" style={{ marginTop: 18 }}>
        <div className="label">Where your data is processed</div>
        <p className="small" style={{ marginBottom: 0 }}>In the product, detection runs on a hub inside your home and only alerts leave it. In this prototype, processing runs in the cloud, and every sensor reading is simulated.</p>
      </div>
    </div>
  );
}

// Talking with Penjaga: when the microphone opens, what happens to the words, and where Penjaga's voice comes from.
const VOICE: { key: keyof VoicePrefs; name: string; detail: string }[] = [
  { key: 'speak', name: 'Answer out loud', detail: 'When you ask by voice, Penjaga answers on screen and out loud.' },
  { key: 'emergencyListen', name: 'Talk me through emergencies', detail: "When something is urgent, Penjaga speaks first, asks where you are and listens for your answer, then walks you to the valve or switch that makes it safe. The microphone opens only for that answer, and you can see when it's on." },
];
function VoiceSettings() {
  const prefs = useVoicePrefs();
  return (
    <div className="card" style={{ marginTop: 18 }}>
      {VOICE.map((v) => (
        <div className="list-item" key={v.key} style={{ alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>{v.name}</div>
            <div className="small" style={{ marginTop: 4 }}>{v.detail}</div>
          </div>
          <button role="switch" aria-checked={prefs[v.key]} aria-label={v.name} onClick={() => setVoicePref(v.key, !prefs[v.key])}
            style={{ width: 52, height: 30, borderRadius: 999, border: 0, background: prefs[v.key] ? 'var(--accent)' : 'var(--sunk)', position: 'relative', flex: 'none' }}>
            <span style={{ position: 'absolute', top: 3, left: prefs[v.key] ? 25 : 3, width: 24, height: 24, borderRadius: '50%', background: '#fff', transition: 'left 0.2s' }} />
          </button>
        </div>
      ))}
      <p className="small muted" style={{ margin: 0, padding: '12px 16px 14px' }}>
        Your microphone is on only while you talk to Penjaga: after you tap the mic, or when you answer its question in an emergency.
        {' '}Your browser turns your speech into text{canListen() ? ' (in Chrome, through Google’s speech service)' : ''}; Penjaga keeps the words, never the audio.
        {' '}Penjaga's voice is made from the words on screen by Google Cloud Text-to-Speech.
      </p>
    </div>
  );
}
