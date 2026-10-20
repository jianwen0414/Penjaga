// Dev server only (/character): every mood and activity of the living character side by side, a fake voice to
// watch the mouth, and the real voice (type a line, Penjaga says it). For tuning the character; never shipped.
import { useEffect, useRef, useState } from 'react';
import LiveCharacter, { type Activity, type Mood } from '../character/LiveCharacter';
import * as speaker from '../voice/speak';

const MOODS: Mood[] = ['calm', 'happy', 'alert', 'concerned', 'thinking', 'sleeping'];
const ACTS: Activity[] = ['idle', 'listening', 'speaking', 'thinking'];

export default function CharacterLab() {
  const [mood, setMood] = useState<Mood>('calm');
  const [act, setAct] = useState<Activity>('idle');
  const [line, setLine] = useState("Good evening. There's water running in Bathroom 2, about 720 litres a day. Where are you now?");
  const fake = useRef(0);
  const [real, setReal] = useState(false);
  useEffect(() => speaker.subscribe(() => setReal(speaker.snapshot().speaking)), []);
  // a fake voice: syllables at about 5 a second, phrases with pauses
  useEffect(() => {
    let raf = 0;
    const tick = (now: number) => {
      const t = now / 1000;
      const phrase = Math.sin(t * 0.9) > -0.6 ? 1 : 0;
      fake.current = phrase * Math.max(0, Math.sin(t * 31) * 0.5 + Math.sin(t * 17.3) * 0.4 + 0.25);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  const voice = () => (real ? speaker.level() : fake.current);
  const activity: Activity = real ? 'speaking' : act;
  return (
    <div style={{ minHeight: '100vh', background: '#ece6dd', padding: 24, fontFamily: 'system-ui' }}>
      <h1 style={{ margin: 0 }}>Penjaga, alive</h1>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '12px 0' }}>
        {MOODS.map((m) => <button key={m} onClick={() => setMood(m)} style={{ fontWeight: m === mood ? 700 : 400 }}>{m}</button>)}
        <span style={{ width: 16 }} />
        {ACTS.map((a) => <button key={a} onClick={() => setAct(a)} style={{ fontWeight: a === act ? 700 : 400 }}>{a}</button>)}
      </div>
      <div style={{ display: 'flex', gap: 8, margin: '12px 0' }}>
        <input value={line} onChange={(e) => setLine(e.target.value)} style={{ flex: 1, padding: 8 }} />
        <button onClick={() => void speaker.say(line)}>Say it</button>
        <button onClick={() => speaker.cancel()}>Stop</button>
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 40, flexWrap: 'wrap' }}>
        <div data-testid="big"><LiveCharacter mood={mood} activity={activity} size={320} disc={false} track voice={voice} mic={() => fake.current * 0.8} /></div>
        <LiveCharacter mood={mood} activity={activity} size={140} track voice={voice} mic={() => fake.current * 0.8} />
        <LiveCharacter mood={mood} activity={activity} size={30} voice={voice} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12, marginTop: 24 }}>
        {MOODS.map((m) => <figure key={m} style={{ margin: 0, textAlign: 'center' }}><LiveCharacter mood={m} size={110} disc={false} /><figcaption>{m}</figcaption></figure>)}
      </div>
    </div>
  );
}
