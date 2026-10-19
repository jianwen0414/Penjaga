// Penjaga, the home agent. One living screen: the house fills the stage, Penjaga speaks beside it and shows
// what it's talking about in the house. The owner acts by replying (or typing), never by hunting through pages.
// The conversation is rebuilt from case data on every render (so it survives reloads); only the owner's
// free-form questions and taps on the house are session-only.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';
import spec from '../../house.spec.json';
import { useView, api, useLive, houseNow, BACKEND, type Live } from '../api/client';
import { ViewerSlot, useEngine, type Engine } from '../app/engine';
import { Icon, Chart, Sheet, SEV_LABEL, fmtDay, fmtTime } from '../components/ui';
import type { Case, ChatMessage, FocusCommand, Severity, Vendor } from '../api/types';
import type { ElementInfo, DrillResult, InspectPhase } from '../viewer/engine';
import NoticesPanel from './Notices';
import SettingsPanel from './Settings';

type Chip = { label: string; icon?: string; primary?: boolean; quiet?: boolean; flip?: boolean; tone?: 'danger'; onClick: () => void }; // quiet: a minor follow-up, drawn as a link
type Item = {
  key: string; t: number; who: 'penjaga' | 'you';
  kicker?: string; tone?: Severity | 'ok' | 'calm';
  text?: ReactNode; card?: ReactNode; chips?: Chip[];
  focus?: FocusCommand; frame?: boolean;
};
type Flow =
  | { type: 'check'; caseId: string }
  | { type: 'book'; caseId: string; vendor?: Vendor; slot?: number }
  | { type: 'mgmt'; caseId: string }
  | null;

const circuits = (spec as unknown as { services: { circuits: { id: string; label: string; rating_a: number; ends: string[] }[] } }).services.circuits;
const LAYER_NAME: Record<string, string> = { L1: 'Water', L2: 'Electrical safety', L5: 'Water supply' };

function greeting() {
  const h = new Date(houseNow()).getHours(); // the house's clock, like the time shown above it
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}
const usualness = (v: number, t: number) => {
  if (!t) return '';
  const p = Math.round(((v - t) / t) * 100);
  return ` (${Math.abs(p) <= 8 ? 'about usual' : p > 0 ? `${p}% more than usual` : `${-p}% less than usual`})`;
};
const SEV_RANK: Record<Severity, number> = { info: 0, heads_up: 1, act_soon: 2, urgent: 3 };

export default function Agent({ panel: routePanel }: { panel?: 'notices' | 'settings' | 'record' }) {
  const v = useView();
  const live = useLive();
  const { engine } = useEngine();
  const { id: routeCase } = useParams();
  const nav = useNavigate();
  const [session, setSession] = useState<Item[]>([]);
  const [flow, setFlow] = useState<Flow>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [panel, setPanel] = useState<null | 'menu' | 'notices' | 'settings' | 'record'>(routePanel ?? null);
  const [stage, setStage] = useState({ view: 'split', water: false, electrical: false, xray: false, drill: false });
  const [text, setText] = useState('');
  const [sensorsOpen, setSensorsOpen] = useState(false);
  const threadEnd = useRef<HTMLDivElement>(null);
  const caseById = (id: string) => v.cases.find((c) => c.id === id);
  const say = (it: Omit<Item, 'key' | 't'> & { t?: number }) => setSession((s) => [...s, { key: `s${Date.now()}${Math.random()}`, t: houseNow(), ...it }]);
  // What the 3D view is pointing at right now, so the owner can always put it away again.
  const [shown, setShown] = useState<string | null>(null);
  const [liveKey, setLiveKey] = useState<string | null>(null); // the message the 3D view is showing
  const focusOn = (f: FocusCommand) => {
    const ok = !!engine?.focus(f);
    setShown(ok ? f.label ?? 'This part' : null);
    return ok;
  };
  const unfocus = (opts?: { reframe?: boolean }) => { inspecting.current++; setHud(null); engine?.clearFocus(opts); setShown(null); setLiveKey(null); };
  useEffect(() => {
    if (!shown) return;
    // Esc puts the 3D focus away, unless an inspection picture is open: then it only closes that
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('.shot-viewer')) unfocus(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [shown]); // eslint-disable-line react-hooks/exhaustive-deps
  // "Scan my home": the beam crosses the house and each room shows what Penjaga knows about it right now (the open
  // cases, from the sensors that never stop listening); then the most serious problem is followed to its source.
  const [scanning, setScanning] = useState(false);
  async function scanHome() {
    if (!engine || scanning) return;
    setScanning(true);
    setShown(null);
    const live = v.cases.filter((c) => c.status !== 'resolved' && c.status !== 'dismissed');
    const rooms: Record<string, Severity> = {};
    for (const c of live) {
      for (const id of c.focus.object_ids) {
        const r = engine.roomOf(id);
        if (r && (!rooms[r] || SEV_RANK[c.severity] > SEV_RANK[rooms[r]])) rooms[r] = c.severity;
      }
    }
    await engine.scan(rooms);
    setScanning(false);
    setShown('Home scan');
    const worst = [...live].sort((a, b) => SEV_RANK[b.severity] - SEV_RANK[a.severity])[0];
    say({
      who: 'penjaga', tone: worst ? 'calm' : 'ok', kicker: 'Home scan',
      text: worst
        ? <><b className="a-lead">{live.length === 1 ? 'One thing needs a look' : `${live.length} things need a look`}.</b> Everywhere else is fine. I'm taking you to the most important one.</>
        : <><b className="a-lead">Every room looks fine.</b> Your water meter, switchboard, safety switches and fridge all read normal right now.</>,
      card: worst ? (
        <ul className="scan-list">
          {[...live].sort((a, b) => SEV_RANK[b.severity] - SEV_RANK[a.severity]).map((c) => (
            <li key={c.id}>
              <i className={`dot sev-${c.severity}`} aria-hidden="true" />
              <span><b>{c.title}</b><span className="small muted">{c.location}</span></span>
              <button className="a-chip" onClick={() => void inspectCase(c)}><Icon name="scan" size={14} />Inspect</button>
            </li>
          ))}
        </ul>
      ) : undefined,
    });
    if (worst) setTimeout(() => void inspectCase(worst), engine.reduceMotion ? 0 : 900);
  }
  // "Show me where" on a case: a 3D inspection. The view walks into the room at eye level, x-rays the wall and marks
  // the fault red, narrated by the HUD; the captured shot (and clip) lands in the chat. The model is the camera:
  // nothing in the house films anything.
  const [hud, setHud] = useState<Hud | null>(null);
  const [viewer, setViewer] = useState<Shot | null>(null);
  const inspecting = useRef(0);
  async function inspectCase(c: Case, focus: FocusCommand = c.focus) {
    if (!engine) return;
    if (!focus.object_ids.some((id) => engine.has(id))) { show(focus); return; } // says it isn't in the model
    // not pinpointed yet (e.g. "somewhere after your water meter"): a picture of the meter tells the owner nothing,
    // so just show where it was measured; the guided check finds the spot, and then it is worth inspecting
    if (focus === c.focus && c.guided && !c.guided.done) { show(focus); return; }
    const run = ++inspecting.current;
    setLiveKey(null);
    setShown(focus.label || c.title);
    setHud({ phase: 'walk', title: c.title, room: null });
    const r = await engine.inspect(focus, {
      onPhase: (phase, info) => { if (inspecting.current === run) setHud((h) => h && { ...h, phase, room: info.room ?? h.room }); },
    });
    if (inspecting.current !== run) return;
    setTimeout(() => { if (inspecting.current === run) setHud(null); }, 1400);
    if (!r) { setHud(null); return; } // put away before it finished
    const shot: Shot = { image: r.image, video: r.video ? URL.createObjectURL(r.video) : undefined, title: c.title, where: r.room ?? c.location };
    say({
      who: 'penjaga', tone: 'calm', kicker: '3D inspection',
      text: focus !== c.focus
        ? <><b className="a-lead">Here's the {(focus.label || 'spot').replace(/^the /i, '').replace(/^./, (ch) => ch.toLowerCase())}{r.room ? `, in the ${r.room.toLowerCase()}` : ''}.</b> Seen at eye level, as you would stand there.</>
        : <><b className="a-lead">Here it is{r.room ? `, in the ${r.room.toLowerCase()}` : ''}.</b> I stood where you would, at eye level, and looked through the wall. What's wrong is marked in red.</>,
      card: <ShotCard shot={shot} onOpen={() => setViewer(shot)} />,
      chips: [{ label: 'Open full view', icon: 'eye', primary: true, onClick: () => setViewer(shot) }],
    });
  }
  // A part the 3D model doesn't have yet: clear the old highlight (it would look like the answer) and say so.
  const show = (f?: FocusCommand) => {
    if (!f || !engine) return;
    if (!focusOn(f)) {
      unfocus();
      say({ who: 'penjaga', tone: 'calm', text: `${f.label ? `${f.label}: ` : ''}that part isn't in the 3D model of your home yet, so I can't point to it.` });
    }
  };

  // ---------- the house talking back: taps and "Can I drill here?" ----------
  useEffect(() => {
    if (!engine) return;
    const offs = [
      engine.on('select', (d: ElementInfo | null) => { if (d) describeElement(d); }),
      engine.on('drill', (r: DrillResult | null) => { if (r) describeDrill(r); }),
      engine.on('state', (s) => setStage({ view: s.view, water: s.water, electrical: s.electrical, xray: s.xray, drill: s.drill })),
    ];
    return () => offs.forEach((f) => f());
  }, [engine]); // eslint-disable-line react-hooks/exhaustive-deps

  function describeElement(d: ElementInfo) {
    const name = d.label ?? api.objectName(d.id);
    const circ = circuits.find((c) => c.ends.includes(d.id));
    const bits: string[] = [];
    if (d.meta?.kw) bits.push(`${d.meta.kw} kW`);
    if (d.meta?.hp) bits.push(`${d.meta.hp} hp`);
    if (d.rating_a) bits.push(`${d.rating_a} A breaker`);
    if (d.system) bits.push(`${d.system} water, ${d.d_mm} mm`);
    let sentence: string;
    const chips: Chip[] = [];
    if (d.kind === 'wall') {
      sentence = `That's a wall in the ${api.roomName(d.room).toLowerCase()}. Want to see what runs inside it before you drill or hang anything?`;
      chips.push({ label: "What's inside?", icon: 'drill', primary: true, onClick: () => { engine?.setDrill(true); const r = engine?.drillCheck(d.id); if (r) describeDrill(r); } });
    } else if (d.layer === 'furniture') {
      sentence = `That's the ${d.kind.replace(/([A-Z])/g, ' $1').toLowerCase()} in the ${api.roomName(d.room).toLowerCase()}.`;
    } else {
      sentence = `That's the ${name.toLowerCase()}${bits.length ? ` (${bits.join(', ')})` : ''}${d.meta?.installed ? `, installed ${new Date(String(d.meta.installed)).toLocaleDateString('en-MY', { month: 'long', year: 'numeric' })}` : ''}.`;
      if (circ) sentence += circ.ends.length === 1 ? ` It has its own ${circ.rating_a} A circuit.` : ` It's on "${circ.label}".`;
      if (circ) chips.push({ label: 'Where does its power come from?', icon: 'bolt', onClick: () => show({ layer: 'electrical', object_ids: [circ.id, d.id, 'db_main'], severity: 'info', label: circ.label }) });
      if (d.kind === 'pipe') chips.push({ label: 'Show the whole run', icon: 'water', onClick: () => show({ layer: 'water', object_ids: [d.id], severity: 'info', label: name }) });
    }
    say({ who: 'penjaga', kicker: 'In your home', tone: 'calm', text: sentence, chips });
  }
  function describeDrill(r: DrillResult) {
    const room = api.roomName(r.wall.room).toLowerCase();
    say({
      who: 'penjaga', kicker: 'Can I drill here?', tone: r.services.length ? 'heads_up' : 'ok',
      text: r.services.length
        ? `This ${room} wall has ${r.services.length} thing${r.services.length > 1 ? 's' : ''} inside it. Avoid these heights:`
        : `Nothing recorded inside this ${room} wall.`,
      card: (
        <div className="a-card">
          {r.services.map((s) => (
            <div className="svc" key={s.id}>
              <i style={{ background: s.layer === 'water' ? (s.system === 'hot' ? 'var(--urgent)' : s.system === 'waste' ? 'var(--muted)' : 'var(--water)') : 'var(--power)' }} />
              <div style={{ flex: 1 }}><div style={{ fontWeight: 600, fontSize: 14 }}>{s.label ?? api.objectName(s.id)}</div>
                <div className="tiny muted">{s.layer === 'water' ? `${s.system} water pipe` : `${s.rating_a} A cable`} · {s.from_m.toFixed(1)}–{s.to_m.toFixed(1)} m from the floor</div></div>
            </div>
          ))}
          <div className="tiny muted" style={{ marginTop: 10 }}><b>Based on your home's drawings.</b> Always confirm with a wire and pipe detector before drilling.</div>
        </div>
      ),
      chips: [{ label: 'Done drilling checks', onClick: () => engine?.setDrill(false) }],
    });
  }

  // ---------- case conversation ----------
  function caseChips(c: Case): Chip[] {
    if (c.status === 'resolved' || flow?.caseId === c.id) return [];
    const chips: Chip[] = [{ label: 'Show me where', icon: 'scan', onClick: () => void inspectCase(c) }];
    for (const a of c.actions) {
      if (a.kind === 'guided_check') { const power = c.guided?.steps[0]?.kind === 'breaker'; chips.unshift({ label: power ? 'Help me find the circuit' : 'Help me find it', icon: power ? 'bolt' : 'water', primary: true, onClick: () => { setFlow({ type: 'check', caseId: c.id }); } }); }
      if (a.kind === 'book_vendor') chips.splice(a.primary ? 0 : chips.length, 0, { label: a.label, icon: 'wrench', primary: a.primary, tone: c.severity === 'urgent' && a.primary ? 'danger' : undefined, onClick: () => setFlow({ type: 'book', caseId: c.id }) });
      // another place to look (e.g. the breaker); one that is the case's own place is "Show me where" already
      if (a.kind === 'show_in_3d' && a.focus && a.focus.object_ids.join() !== c.focus.object_ids.join()) { const f = a.focus; chips.push({ label: a.label, icon: 'bolt', onClick: () => void inspectCase(c, f) }); }
      if (a.kind === 'send_management') chips.push({ label: 'Tell management', icon: 'send', onClick: () => setFlow({ type: 'mgmt', caseId: c.id }) });
      if (a.kind === 'dismiss') chips.push({ label: 'Not an issue', onClick: () => dismissAsk(c) });
    }
    // The next step and "Show me where" up front; the other options wait under "More options".
    const main = chips.filter((ch) => ch.primary).slice(0, 2);
    const where = chips.find((ch) => ch.label === 'Show me where');
    if (where && !main.includes(where)) main.push(where);
    const rest = chips.filter((ch) => !main.includes(ch));
    const more = !!open[`opts:${c.id}`];
    const out: Chip[] = [...main];
    if (rest.length === 1) out.push(rest[0]); // one extra: no point hiding it behind another button
    else if (rest.length) out.push(...(more ? rest : []), { label: more ? 'Fewer options' : `More options (${rest.length})`, icon: 'chevron', quiet: true, flip: more, onClick: () => setOpen((o) => ({ ...o, [`opts:${c.id}`]: !o[`opts:${c.id}`] })) });
    out.push({ label: open[`ev:${c.id}`] ? 'Hide the evidence' : 'Why do you think so?', quiet: true, onClick: () => setOpen((o) => ({ ...o, [`ev:${c.id}`]: !o[`ev:${c.id}`] })) });
    if (c.impact.how) out.push({ label: 'How did you get the cost?', quiet: true, onClick: () => setOpen((o) => ({ ...o, [`cost:${c.id}`]: !o[`cost:${c.id}`] })) });
    return out;
  }
  function dismissAsk(c: Case) {
    say({
      who: 'penjaga', text: 'Okay. Tell me why, so I learn what\'s normal for your home.',
      chips: ['We were using water (filling buckets)', 'Already fixed', 'Not a problem for us'].map((r) => ({ label: r, onClick: () => { api.dismiss(c.id, r); say({ who: 'you', text: r }); say({ who: 'penjaga', tone: 'calm', text: "Got it. I've closed it and I'll keep that in mind." }); } })),
    });
  }

  function caseStory(c: Case): Item[] {
    const items: Item[] = [];
    const urgent = c.severity === 'urgent' && c.status !== 'resolved';
    items.push({
      key: `${c.id}:open`, t: c.opened_at, who: 'penjaga', tone: c.severity,
      kicker: `${LAYER_NAME[c.layer] ?? 'Home'} · ${SEV_LABEL[c.severity]}`,
      text: <><b className="a-lead">{c.guided?.done && c.status !== 'resolved' ? 'Something in your home needs a look.' : c.title}.</b> {c.guided?.done ? '' : <Clamp text={c.summary} open={!!open[`more:${c.id}`]} onMore={() => setOpen((o) => ({ ...o, [`more:${c.id}`]: true }))} />}</>,
      card: (
        <>
          {urgent && c.safety && (
            <div className="safety a-card" role="alert">
              <h3><Icon name="shield" size={17} /> Stay safe first</h3>
              <ol>{c.safety.map((s) => <li key={s}>{s}</li>)}</ol>
            </div>
          )}
          {!c.guided?.done && <CaseFacts c={c} showHow={!!open[`cost:${c.id}`]} />}
        </>
      ),
      focus: c.focus,
    });
    // what happened since, as a dialogue
    // keys carry the entry's position: two entries can share a millisecond of house time
    for (const [i, e] of c.timeline.slice(2).entries()) {
      const off = e.text.match(/^Switched off the (.+) circuit: leakage (dropped|unchanged)$/);
      if (off) {
        items.push({ key: `${c.id}:${e.t}:${i}:you`, t: e.t, who: 'you', text: `I switched off the ${off[1]} breaker.` });
        items.push(off[2] === 'dropped'
          ? { key: `${c.id}:${e.t}:${i}`, t: e.t + 1, who: 'penjaga', tone: 'ok', kicker: 'Found it', text: <><b className="a-lead">That's it: the {off[1]} circuit.</b> The leakage dropped the moment you switched it off, so the fault is on that circuit: a socket, its cable, or something plugged into it. Leave that breaker off and unplug what's on it. The electrician can now go straight to it instead of testing the whole house.</>, card: <CaseFacts c={c} showHow={!!open[`cost:${c.id}`]} />, focus: c.focus }
          : { key: `${c.id}:${e.t}:${i}`, t: e.t + 1, who: 'penjaga', tone: 'calm', text: `The leakage stayed, so it's not that circuit. You can switch it back on.` });
        continue;
      }
      const m = e.text.match(/^Checked (.+): flow (stopped|continued)$/);
      if (m) {
        items.push({ key: `${c.id}:${e.t}:${i}:you`, t: e.t, who: 'you', text: `I closed the valve under the ${m[1]}.` });
        items.push(m[2] === 'stopped'
          ? { key: `${c.id}:${e.t}:${i}`, t: e.t + 1, who: 'penjaga', tone: 'ok', kicker: 'Found it', text: <><b className="a-lead">That's it: the {m[1]}.</b> The flow stopped the moment you closed its valve, so it's a worn flush valve inside the tank, not a pipe in the wall. Small part, small repair. Open the valve again so the toilet works until then.</>, card: <CaseFacts c={c} showHow={!!open[`cost:${c.id}`]} />, focus: c.focus }
          : { key: `${c.id}:${e.t}:${i}`, t: e.t + 1, who: 'penjaga', tone: 'calm', text: `Still flowing, so it's not that one. You can open it again.` });
        continue;
      }
      const b = e.text.match(/^Booked (.+) for (.+)$/);
      if (b) { items.push({ key: `${c.id}:${e.t}:${i}:you`, t: e.t, who: 'you', text: `Book ${b[1]} for ${b[2]}.` }); items.push({ key: `${c.id}:${e.t}:${i}`, t: e.t + 1, who: 'penjaga', tone: 'calm', text: `Done. I've sent ${b[1]} the problem and where it is, and nothing else. I'll tell you when they confirm.` }); continue; }
      if (e.text.startsWith('Booked ')) { items.push({ key: `${c.id}:${e.t}:${i}:you`, t: e.t, who: 'you', text: e.text.replace('Booked', 'Book') + '.' }); items.push({ key: `${c.id}:${e.t}:${i}`, t: e.t + 1, who: 'penjaga', tone: 'calm', text: "Done. They have the problem and the location, nothing else. I'll tell you when they confirm." }); continue; }
      if (e.actor === 'vendor' && /confirmed/.test(e.text)) { items.push({ key: `${c.id}:${e.t}:${i}`, t: e.t, who: 'penjaga', tone: 'calm', kicker: 'Booking', text: `${e.text}${c.booking ? ` for ${fmtDay(c.booking.slot)}, ${fmtTime(c.booking.slot)}` : ''}.` }); continue; }
      if (e.actor === 'vendor' && /complete/.test(e.text)) { items.push({ key: `${c.id}:${e.t}:${i}`, t: e.t, who: 'penjaga', tone: 'calm', text: `${c.booking?.vendor_name ?? 'The vendor'} says the job is done. I'll check the readings to make sure.` }); continue; }
      if (/^Verified/.test(e.text) || (c.status === 'resolved' && e.actor === 'penjaga')) continue;
      if (e.text === 'Sent to management') { items.push({ key: `${c.id}:${e.t}:${i}:you`, t: e.t, who: 'you', text: 'Send this to management.' }); items.push({ key: `${c.id}:${e.t}:${i}`, t: e.t + 1, who: 'penjaga', tone: 'calm', text: "Sent. I'll show you their reply here." }); continue; }
    }
    if (c.status === 'resolved') {
      items.push({ key: `${c.id}:resolved`, t: c.resolved_at ?? houseNow(), who: 'penjaga', tone: 'ok', kicker: 'Fixed', text: <><b className="a-lead">{c.title}.</b> {c.layer === 'L1' ? 'Your water meter is still again at night. ' : c.layer === 'L2' && /leakage/i.test(c.title) ? 'Earth leakage is back to normal. ' : ''}{c.outcome}. I've added it to your Home Passport.</> });
    }
    if (open[`ev:${c.id}`]) {
      items.push({ key: `${c.id}:evidence`, t: houseNow() + 1, who: 'penjaga', tone: 'calm', kicker: 'Why I think so', text: c.evidence.explanation, card: <div className="a-card"><div className="label" style={{ marginBottom: 6 }}>{c.evidence.title}</div><Chart s={c.evidence.series} title={c.evidence.title} height={150} /><div className="a-stats">{c.evidence.stats.map((s) => <div key={s.label}><span className="tiny muted">{s.label}</span><b>{s.value}</b></div>)}</div></div> });
    }
    const last = [...items].reverse().find((i) => i.who === 'penjaga');
    if (last) last.chips = caseChips(c);
    return items;
  }

  // ---------- flows ----------
  function flowItems(): Item[] {
    if (!flow) return [];
    const c = caseById(flow.caseId);
    if (!c) return [];
    if (flow.type === 'check' && c.guided) {
      if (c.guided.done) return [];
      const step = c.guided.steps.find((s) => !s.result);
      if (!step) return [];
      const first = !c.guided.steps.some((s) => s.result);
      if (step.kind === 'breaker') {
        const name = step.fixture_label.replace(/"/g, '');
        return [{
          key: `flow:check:${step.fixture_id}`, t: houseNow() + 5, who: 'penjaga', tone: 'calm',
          kicker: 'Finding it together',
          text: <>{first ? "Let's find which circuit is leaking. I'll watch your safety switch while you switch off one breaker at a time. When the leakage drops, we've found it. " : ''}<b>{first ? 'Start with:' : 'Next:'}</b> at your switchboard, switch off the breaker labelled <b>{name}</b>.</>,
          card: <CheckMeter key={step.fixture_id} label="Your safety switch · leakage, live" busy="Watching the leakage…" ready="It's off · check now" onDone={() => api.runGuidedCheck(c.id, step.fixture_id)} />,
          focus: { layer: 'electrical', object_ids: [step.fixture_id], severity: 'info', label: `${name} circuit` },
          chips: [{ label: 'Stop the check', onClick: () => setFlow(null) }],
        }];
      }
      return [{
        key: `flow:check:${step.fixture_id}`, t: houseNow() + 5, who: 'penjaga', tone: 'calm',
        kicker: 'Finding it together',
        text: <>{first ? "Let's find it together. I'll watch the water meter while you close one toilet valve at a time. When the flow stops, we've found it. " : ''}<b>{first ? 'Start downstairs:' : 'Next:'}</b> close the small valve on the pipe under the <b>{step.fixture_label.toLowerCase()}</b> (clockwise until it stops).</>,
        card: <CheckMeter key={step.fixture_id} onDone={() => api.runGuidedCheck(c.id, step.fixture_id)} />,
        focus: { layer: 'water', object_ids: [step.fixture_id], severity: 'info', label: `Valve under ${step.fixture_label.toLowerCase()}` },
        chips: [{ label: 'Stop the check', onClick: () => setFlow(null) }],
      }];
    }
    if (flow.type === 'book') {
      const cat = c.actions.find((a) => a.kind === 'book_vendor')?.vendor_category ?? 'plumber';
      const out: Item[] = [{
        key: 'flow:book:vendors', t: houseNow() + 5, who: 'penjaga', tone: 'calm', kicker: cat === 'plumber' ? 'Plumbers near you' : 'Licensed electricians near you',
        text: `These are vetted and nearby. Who would you like?`,
        card: (
          <div className="a-options">
            {api.vendors(cat).map((x) => (
              <button key={x.id} className={`choice ${flow.vendor?.id === x.id ? 'on' : ''}`} onClick={() => setFlow({ ...flow, vendor: x, slot: undefined })}>
                <span style={{ flex: 1 }}><b>{x.name}</b><div className="small muted">★ {x.rating} · {x.jobs} jobs{x.licence ? ` · ${x.licence}` : ''}</div><div className="small">{x.price}</div></span>
              </button>
            ))}
          </div>
        ),
        chips: [{ label: 'Not now', onClick: () => setFlow(null) }],
      }];
      if (flow.vendor) {
        out.push({ key: 'flow:book:you1', t: houseNow() + 6, who: 'you', text: flow.vendor.name });
        out.push({
          key: 'flow:book:slot', t: houseNow() + 7, who: 'penjaga', tone: 'calm', text: 'When suits you?',
          chips: flow.vendor.next_slots.map((s) => ({ label: `${fmtDay(s)} · ${fmtTime(s)}`, primary: flow.slot === s, onClick: () => setFlow({ ...flow, slot: s }) })),
        });
      }
      if (flow.vendor && flow.slot) {
        out.push({ key: 'flow:book:you2', t: houseNow() + 8, who: 'you', text: `${fmtDay(flow.slot)}, ${fmtTime(flow.slot)}` });
        out.push({
          key: 'flow:book:confirm', t: houseNow() + 9, who: 'penjaga', tone: 'calm', text: `Before I book ${flow.vendor.name}, here's exactly what I'll share with them:`,
          card: <div className="a-card small"><ul style={{ margin: 0, paddingLeft: 18 }}><li>The problem and where it is ({c.location})</li><li>A picture of that spot in your home</li><li>Your name and phone number for the visit</li></ul><div className="tiny muted" style={{ marginTop: 8 }}>Never shared: meter readings, usage history, or anything about who is home.</div></div>,
          chips: [
            { label: 'Confirm booking', icon: 'check', primary: true, onClick: () => { api.book(c.id, flow.vendor!.id, flow.slot!); setFlow(null); } },
            { label: 'Pick another', onClick: () => setFlow({ type: 'book', caseId: c.id }) },
          ],
        });
      }
      return out;
    }
    if (flow.type === 'mgmt') {
      return [{
        key: 'flow:mgmt', t: houseNow() + 5, who: 'penjaga', tone: 'calm', kicker: 'To management',
        text: "Here's a draft. Edit anything, then send. Only the summary and location go with it.",
        card: <MgmtDraft c={c} onSend={(note) => { api.sendToManagement(c.id, note); setFlow(null); }} />,
        chips: [{ label: 'Cancel', onClick: () => setFlow(null) }],
      }];
    }
    return [];
  }

  // ---------- the whole conversation ----------
  const openCases = v.cases.filter((c) => c.status !== 'resolved');
  const t = v.home.today;
  const status = v.home.status;
  const briefing: Item = {
    key: 'brief', t: 0, who: 'penjaga', tone: status === 'all_quiet' ? 'ok' : status === 'urgent' ? 'urgent' : 'act_soon',
    kicker: `${fmtTime(houseNow())} · Today`,
    text: (
      <>
        <span className="a-hello">{greeting()}, Aiman.</span>{' '}
        {status === 'all_quiet' && <>Your home is quiet. Water, power and appliances all look normal.</>}
        {status === 'attention' && <>{openCases.length === 1 ? 'One thing needs a look' : `${openCases.length} things need a look`}. I've worked out what I can, and I have a next step ready.</>}
        {status === 'urgent' && <>Something needs you now: an electrical safety issue. I'll take you through it.</>}
        {' '}So far today you've used {t.water_l.toLocaleString()} L of water{usualness(t.water_l, t.water_typical_l)} and {t.elec_kwh} kWh of power{usualness(t.elec_kwh, t.elec_typical_kwh)}.
        {v.maintenance[0] && <> Next on my list: {v.maintenance[0].title.toLowerCase()}, due {fmtDay(v.maintenance[0].due)}.</>}
      </>
    ),
    frame: true,
    chips: [
      { label: 'Scan my home', icon: 'scan', primary: true, onClick: () => void scanHome() },
      { label: 'Show me my pipes and wiring', icon: 'layers', onClick: () => { unfocus(); engine?.setLayer('water', true); engine?.setLayer('electrical', true); } },
      { label: 'Can I drill somewhere?', icon: 'drill', onClick: () => { engine?.setDrill(true); say({ who: 'penjaga', tone: 'calm', kicker: 'Can I drill here?', text: 'Tap any wall in the house and I\'ll tell you what runs inside it.' }); } },
    ],
  };
  const others: Item[] = [];
  if (v.wellbeing) {
    const w = v.wellbeing;
    others.push({
      key: `wb:${w.id}`, t: houseNow() - 60e3, who: 'penjaga', tone: 'info', kicker: 'Family check',
      text: <><b className="a-lead">{w.title}.</b> {w.body} I only look at water and power use, never cameras or microphones.</>,
      focus: { layer: 'structure', object_ids: ['floor_GF_bed4', 'floor_GF_bath4'], severity: 'info', label: "Mak's suite" },
      chips: [
        { label: 'Call Mak', icon: 'phone', primary: true, onClick: () => { api.ackWellbeing(w.id, 'called'); location.href = 'tel:+60123456789'; } },
        { label: 'Let Mei Ling know', onClick: () => { api.ackWellbeing(w.id, 'family notified'); say({ who: 'penjaga', tone: 'calm', text: "I've let Mei Ling know." }); } },
        { label: "She's away today", onClick: () => { api.ackWellbeing(w.id, 'away'); say({ who: 'penjaga', tone: 'calm', text: "Thanks. I won't flag the suite again today." }); } },
      ],
    });
  }
  for (const n of v.notices.filter((n) => n.ends_at > houseNow() - 6 * 3600e3)) {
    others.push({
      key: `notice:${n.id}`, t: n.impact ? houseNow() - 30e3 : n.starts_at, who: 'penjaga', tone: n.impact ? 'ok' : 'info', kicker: `From management · ${n.issuer}`,
      text: <><b className="a-lead">{n.title}</b>, {fmtTime(n.starts_at)}–{fmtTime(n.ends_at)}, {n.area}. {n.impact ? <><br /><span className="a-insight">{n.impact}</span></> : 'I\'ll keep an eye on your supply while it\'s on.'}</>,
      focus: n.impact ? { layer: 'water', object_ids: ['tank_roof_main', 'meter_water_main'], severity: 'info', label: 'Roof tank' } : undefined,
      chips: [{ label: 'Show my water tank', icon: 'water', onClick: () => show({ layer: 'water', object_ids: ['tank_roof_main', 'valve_roof_tank_float'], severity: 'info', label: 'Roof tank · 1,000 L' }) }],
    });
  }
  // A case enters the conversation when it reaches the owner, not at its detection time in the past.
  const arrivals = useRef<Map<string, number> | null>(null);
  if (!arrivals.current) arrivals.current = new Map(v.cases.map((c) => [c.id, c.opened_at]));
  for (const c of v.cases) if (!arrivals.current.has(c.id)) arrivals.current.set(c.id, houseNow());
  const stories = v.cases.flatMap((c) => {
    const at = arrivals.current!.get(c.id)!;
    return caseStory(c).map((it, k) => ({ ...it, t: Math.max(it.t, at + k) }));
  });
  // What the owner is asking right now stays at the bottom, in the order it was said, below the case stories.
  const thread = [briefing, ...[...others, ...stories].sort((a, b) => a.t - b.t), ...session, ...flowItems()];

  // ---------- keep the stage in step with the conversation ----------
  const latest = [...thread].reverse().find((i) => i.focus || i.frame);
  const latestKey = latest ? latest.key + JSON.stringify(latest.focus ?? '') : '';
  const lastShown = useRef('');
  useEffect(() => {
    if (!engine || !latest || lastShown.current === latestKey) return;
    if (hud) { lastShown.current = latestKey; return; } // mid-inspection: don't yank the view away
    setLiveKey(latest.focus ? latest.key : null);
    lastShown.current = latestKey;
    if (latest.focus) focusOn(latest.focus);
    else { unfocus(); openCases.forEach((c) => engine.flag(c.focus.object_ids.slice(0, 1), c.severity)); }
  }, [engine, latestKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // Follow the newest message, including a reply that replaces the "looking into it" line (same length).
  const lastKey = thread[thread.length - 1]?.key;
  useEffect(() => { threadEnd.current?.scrollIntoView({ behavior: engine?.reduceMotion ? 'auto' : 'smooth', block: 'end' }); }, [thread.length, lastKey, flow]);
  useEffect(() => { // notification deep link: bring that case forward
    const c = routeCase ? caseById(routeCase) : null;
    if (c && engine) { focusOn(c.focus); document.getElementById(`${c.id}:open`)?.scrollIntoView({ block: 'center' }); }
  }, [routeCase, engine]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setPanel(routePanel ?? null); }, [routePanel]);
  const flowCase = flow ? caseById(flow.caseId) : null;
  useEffect(() => { if (flow?.type === 'check' && flowCase?.guided?.done) setFlow(null); }, [flow, flowCase?.guided?.done]); // eslint-disable-line react-hooks/exhaustive-deps

  const onReady = (e: Engine) => { e.setInteractive(true); e.setAutoRotate(false); e.setInsets({ left: 470 }); lastShown.current = ''; return () => e.setInsets({ left: 0 }); };

  const ask = (q: string) => {
    if (!q.trim()) return;
    say({ who: 'you', text: q });
    setText('');
    const ctxCase = openCases[0]?.id;
    const a = api.ask(q, { caseId: ctxCase });
    // The real agent answers in a few seconds: show that Penjaga is working on it until the reply lands.
    if (a instanceof Promise) {
      const key = `think${Date.now()}`;
      setSession((s) => [...s, { key, t: houseNow(), who: 'penjaga', tone: 'calm', text: <span className="thinking" role="status"><span className="dots" aria-hidden="true"><i /><i /><i /></span><span className="muted">Penjaga is looking into it</span></span> }]);
      void a.then((m) => { setSession((s) => s.filter((it) => it.key !== key)); reply(m); });
    } else setTimeout(() => reply(a), 500);
  };
  // The answer has passed the guardrails before it arrives; it types itself out, then its buttons appear.
  const reply = (a: ChatMessage) => {
    const key = `r${Date.now()}${Math.random()}`;
    const chips: Chip[] | undefined = a.chips?.map((c) => ({
      label: c.label, icon: c.focus ? 'pin' : undefined,
      onClick: () => { if (c.focus) show(c.focus); else if (c.drill) engine?.setDrill(true); else if (c.to?.startsWith('/cases/')) { const id = c.to.split('/').pop()!; const cc = caseById(id); if (cc) { show(cc.focus); document.getElementById(`${id}:open`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }); } } },
    }));
    const follow = () => threadEnd.current?.scrollIntoView({ block: 'end' });
    const done = () => { setSession((s) => s.map((it) => (it.key === key ? { ...it, chips } : it))); setTimeout(() => threadEnd.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }), 60); };
    setSession((s) => [...s, {
      key, t: houseNow(), who: 'penjaga', tone: 'calm', focus: a.chips?.find((c) => c.focus)?.focus,
      text: <Typewriter text={a.text} onTick={follow} onDone={done} />,
    }]);
  };
  const suggestions = openCases.length
    ? ['What happens if I wait?', 'How much is this costing me?', "What's wrong at home?"]
    : ['Where does the master bathroom heater get its power?', 'Where are the pipes in Bathroom 2?', 'Where is my water tank?'];

  return (
    <div className="agent">
      <div className="agent-stage">
        <ViewerSlot label="Your home in 3D. Tap anything and Penjaga will tell you about it." onReady={onReady} />
        <div className="stage-tools-r">
          <div className="seg" role="group" aria-label="Floors">
            {([['split', 'Both'], ['GF', 'Ground'], ['FF', 'First'], ['stack', 'Stacked']] as const).map(([id, l]) => (
              <button key={id} className={stage.view === id ? 'on' : ''} onClick={() => { unfocus({ reframe: false }); engine?.setView(id); }}>{l}</button>
            ))}
          </div>
          <div className="seg" role="group" aria-label="What to show">
            <button className={stage.water ? 'on' : ''} onClick={() => engine?.setLayer('water', !stage.water)} aria-label="Water"><Icon name="water" size={15} /><span className="lbl">Water</span></button>
            <button className={stage.electrical ? 'on' : ''} onClick={() => engine?.setLayer('electrical', !stage.electrical)} aria-label="Power"><Icon name="bolt" size={15} /><span className="lbl">Power</span></button>
            <button className={stage.xray ? 'on' : ''} onClick={() => engine?.setXray(!stage.xray)} aria-label="See through walls"><Icon name="eye" size={15} /><span className="lbl">See through</span></button>
          </div>
          <div className="seg"><button className={stage.drill ? 'on' : ''} onClick={() => engine?.setDrill(!stage.drill)} aria-label="Can I drill here?"><Icon name="drill" size={15} /><span className="lbl">Drill check</span></button></div>
          <button className={`scan-btn ${scanning ? 'busy' : ''}`} onClick={() => void scanHome()} disabled={scanning}><Icon name="scan" size={15} />{scanning ? 'Scanning…' : 'Scan my home'}</button>
          <button className="round" onClick={() => unfocus()} aria-label="Whole house"><Icon name="house3d" size={17} /></button>
        </div>
        {hud && <InspectHud hud={hud} />}
        {shown && (
          <div className="stage-shown" role="status">
            <span className="stage-shown-what">Showing <b>{shown}</b></span>
            <button onClick={() => unfocus()}><Icon name="close" size={14} /><span className="long">Back to whole house</span><span className="short">Whole house</span></button>
          </div>
        )}
      </div>

      {viewer && <ShotViewer shot={viewer} onClose={() => setViewer(null)} />}
      <section className="agent-col" aria-label="Penjaga">
        <header className="agent-head">
          <span className="agent-mark"><Icon name="shield" size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b className="serif" style={{ fontSize: 19 }}>Penjaga</b>
            <button className="listening" onClick={() => setSensorsOpen((o) => !o)} aria-expanded={sensorsOpen} aria-controls="sensors"><i className={status} />Listening · water {live.water_lpm.toFixed(2)} L/min · power {live.power_kw.toFixed(1)} kW<Icon name="chevron" size={12} /></button>
          </div>
          <button className="round" onClick={() => setPanel('menu')} aria-label="Your home"><Icon name="cases" size={17} /></button>
        </header>
        {sensorsOpen && <Sensors live={live} />}

        <div className="thread" aria-live="polite">
          {thread.map((i) => (
            <article key={i.key} id={i.key} className={`a-item ${i.who} ${i.tone ?? ''}`}>
              {i.who === 'penjaga' && (i.kicker || i.key === liveKey) && (
                <div className={`a-kicker ${i.kicker ? '' : 'bare'}`}>{i.kicker}{i.key === liveKey && <span className="live3d"><i aria-hidden="true" />Live in 3D</span>}</div>
              )}
              {i.text && <div className="a-text">{i.text}</div>}
              {i.card}
              {i.chips && i.chips.length > 0 && (
                <div className="a-chips">
                  {i.chips.map((ch) => (
                    <button key={ch.label} className={`a-chip ${ch.primary ? 'primary' : ''} ${ch.quiet ? 'quiet' : ''} ${ch.flip ? 'flip' : ''} ${ch.tone ?? ''}`} onClick={ch.onClick} aria-expanded={ch.flip !== undefined && ch.icon === 'chevron' ? ch.flip : undefined}>
                      {ch.icon && <Icon name={ch.icon} size={15} />}{ch.label}
                    </button>
                  ))}
                </div>
              )}
              {i.who === 'penjaga' && i.focus && !i.chips?.length && i.key !== latest?.key && (
                <button className="a-show" onClick={() => show(i.focus)}><Icon name="pin" size={13} /> Show in the house</button>
              )}
            </article>
          ))}
          <div ref={threadEnd} />
        </div>

        <form className="agent-compose" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
          <div className="suggest">{suggestions.map((s) => <button type="button" key={s} onClick={() => ask(s)}>{s}</button>)}</div>
          <div className="row">
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask Penjaga about your home" aria-label="Ask Penjaga" />
            <button className="btn primary" aria-label="Send"><Icon name="send" size={18} /></button>
          </div>
          <div className="tiny muted" style={{ textAlign: 'center' }}>Penjaga acts only after you confirm</div>
        </form>
      </section>

      {panel === 'menu' && (
        <Sheet title="Your home" onClose={() => setPanel(null)}>
          <div className="small muted" style={{ marginTop: -6, marginBottom: 12 }}>No. 12, Jalan Elmina Green 6 · Representative SDP Elmina Green Six terrace</div>
          {[['record', 'house3d', 'Home Passport & maintenance', `${v.passport.length} records · ${v.maintenance.length} coming up`], ['notices', 'notices', 'Notices from management', v.notices.length ? `${v.notices.length} notice${v.notices.length > 1 ? 's' : ''}` : 'Nothing right now'], ['settings', 'shield', 'Privacy & what Penjaga reads', 'Switch any layer off']].map(([id, icon, l, d]) => (
            <button key={id} className="choice" onClick={() => setPanel(id as 'record')}><Icon name={icon} /><span style={{ flex: 1 }}><b>{l}</b><div className="small muted">{d}</div></span><Icon name="chevron" size={16} /></button>
          ))}
          <button className="choice" onClick={() => { setPanel(null); nav('/welcome'); }}><Icon name="replay" /><span style={{ flex: 1 }}><b>Replay the tour</b></span></button>
        </Sheet>
      )}
      {panel === 'record' && (
        <Sheet title="Home Passport" onClose={() => setPanel(null)}>
          <p className="small muted" style={{ marginTop: 0 }}>A verified history of your home that stays with the house.</p>
          {v.passport.map((p) => <div className="svc" key={p.t + p.title}><i style={{ background: 'var(--ok)' }} /><div><b style={{ fontSize: 14 }}>{p.title}</b><div className="tiny muted">{fmtDay(p.t)} · {p.detail}</div></div></div>)}
          <div className="divider" />
          <div className="label">Coming up</div>
          {v.maintenance.map((m) => <div className="svc" key={m.id}><i style={{ background: 'var(--heads)' }} /><div style={{ flex: 1 }}><b style={{ fontSize: 14 }}>{m.title}</b><div className="tiny muted">Due {fmtDay(m.due)} · {m.interval} · {m.source}</div></div>{api.markMaintenanceDone && <button className="btn" onClick={() => api.markMaintenanceDone?.(m.id)}>Done</button>}</div>)}
        </Sheet>
      )}
      {panel === 'notices' && <Sheet title="Notices" onClose={() => setPanel(null)}><NoticesPanel embedded /></Sheet>}
      {panel === 'settings' && <Sheet title="Privacy" onClose={() => setPanel(null)}><SettingsPanel embedded /></Sheet>}
    </div>
  );
}

function CaseFacts({ c, showHow }: { c: Case; showHow: boolean }) {
  if (c.status === 'resolved') return null;
  return (
    <div className="a-card a-facts">
      <div className="a-where"><Icon name="pin" size={15} /> {c.location}</div>
      <div className="a-two">
        <div>
          <div className="tiny muted">How sure</div>
          <b>{c.confidence >= 0.95 ? 'Confirmed' : c.confidence >= 0.8 ? 'Likely' : 'Possible'} · {Math.round(c.confidence * 100)}%</b>
          <div className="confbar"><i style={{ width: `${c.confidence * 100}%` }} /></div>
        </div>
        <div>
          <div className="tiny muted">{c.layer === 'L2' ? 'Why it matters' : 'Cost of waiting'}</div>
          <b>{c.impact.headline}</b>
          <div className="tiny muted" style={{ marginTop: 2 }}>{c.impact.lines[0]}</div>
        </div>
      </div>
      {c.layer === 'L2' && <div className="tiny" style={{ marginTop: 8 }}>{c.impact.lines[1]}</div>}
      {showHow && c.impact.how && <div className="tiny muted" style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--line)' }}>{c.impact.how}</div>}
      {c.booking && <div className="tiny" style={{ marginTop: 8 }}><Icon name="calendar" size={13} /> {c.booking.vendor_name} · {fmtDay(c.booking.slot)}, {fmtTime(c.booking.slot)} · {c.booking.status === 'requested' ? 'waiting to confirm' : 'confirmed'}</div>}
    </div>
  );
}

// Live meter while the owner closes a valve: watch for a moment, then take the reading.
// Types a finished answer out word by word (a few seconds at most), with a caret; instant under reduced motion.
function Typewriter({ text, onTick, onDone }: { text: string; onTick?: () => void; onDone?: () => void }) {
  const words = text.split(/(\s+)/);
  const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const [n, setN] = useState(reduce ? words.length : 0);
  const finished = useRef(false);
  useEffect(() => {
    if (n >= words.length) {
      if (!finished.current) { finished.current = true; onDone?.(); }
      return;
    }
    const step = Math.max(1, Math.ceil(words.length / 90)); // long answers type faster: about 2 s at most
    const t = setTimeout(() => { setN((k) => Math.min(words.length, k + step * 2)); onTick?.(); }, 22);
    return () => clearTimeout(t);
  }, [n]); // eslint-disable-line react-hooks/exhaustive-deps
  const typing = n < words.length;
  return <>{words.slice(0, n).join('')}{typing && <span className="caret" aria-hidden="true" />}{typing && <span className="sr-only">{text}</span>}</>;
}

// A long case summary: its first two sentences, then "More" for the rest.
function Clamp({ text, open, onMore }: { text: string; open: boolean; onMore: () => void }) {
  const parts = text.split(/(?<=[.!?])\s+(?=[A-Z])/); // not at decimals like 0.51
  if (open || parts.length <= 2 || text.length < 200) return <>{text}</>;
  return <>{parts.slice(0, 2).join(' ')} <button className="more" onClick={onMore}>More</button></>;
}

type Hud = { phase: InspectPhase; title: string; room: string | null };
type Shot = { image: string; video?: string; title: string; where: string };

// The inspection's camera overlay: corner brackets, REC, and a line saying what the view is doing.
function InspectHud({ hud }: { hud: Hud }) {
  const line: Record<InspectPhase, string> = {
    walk: `Going to ${hud.room ? `the ${hud.room.toLowerCase()}` : 'the spot'}, at eye level`,
    look: 'This is what you would see',
    xray: 'X-ray: looking inside the wall',
    found: `Found: ${hud.title}`,
    done: 'Captured. The picture is in the chat',
  };
  return (
    <div className={`inspect-hud ph-${hud.phase}`} aria-live="polite">
      <i className="c tl" /><i className="c tr" /><i className="c bl" /><i className="c br" />
      <div className="hud-top"><span className="rec" />3D inspection</div>
      {hud.phase === 'xray' && <div className="scanline" aria-hidden="true" />}
      <div className="hud-line">{line[hud.phase]}</div>
    </div>
  );
}

function ShotCard({ shot, onOpen }: { shot: Shot; onOpen: () => void }) {
  return (
    <button className="shot" onClick={onOpen} aria-label={`Open the 3D inspection of ${shot.title}`}>
      <img src={shot.image} alt="" />
      {shot.video && <span className="shot-clip"><Icon name="replay" size={13} />Clip</span>}
      <span className="shot-cap"><b>{shot.title}</b><span>{shot.where}</span></span>
    </button>
  );
}

// Full view of an inspection: the picture or the clip, and a way to keep them.
function ShotViewer({ shot, onClose }: { shot: Shot; onClose: () => void }) {
  const [mode, setMode] = useState<'picture' | 'clip'>('picture');
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const file = shot.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return (
    <div className="shot-viewer" role="dialog" aria-modal="true" aria-label={`3D inspection: ${shot.title}`} onClick={onClose}>
      <div className="sv-panel" onClick={(e) => e.stopPropagation()}>
        <div className="sv-head">
          <div><div className="micro">3D inspection</div><b>{shot.title}</b><div className="small muted">{shot.where}</div></div>
          <button className="round" onClick={onClose} aria-label="Close" autoFocus><Icon name="close" size={17} /></button>
        </div>
        {shot.video && (
          <div className="seg sv-tabs" role="tablist">
            <button role="tab" aria-selected={mode === 'picture'} className={mode === 'picture' ? 'on' : ''} onClick={() => setMode('picture')}>Picture</button>
            <button role="tab" aria-selected={mode === 'clip'} className={mode === 'clip' ? 'on' : ''} onClick={() => setMode('clip')}>Clip</button>
          </div>
        )}
        <div className="sv-media">
          {mode === 'clip' && shot.video
            ? <video src={shot.video} controls autoPlay muted playsInline />
            : <img src={shot.image} alt={`3D inspection of ${shot.title}: the fault is marked in red`} />}
        </div>
        <div className="sv-foot">
          <span className="tiny muted">From your home's 3D model and live sensors. Nothing in your home films you.</span>
          <span className="row" style={{ gap: 8 }}>
            <a className="a-chip" href={shot.image} download={`penjaga-${file}.jpg`}>Save picture</a>
            {shot.video && <a className="a-chip" href={shot.video} download={`penjaga-${file}.webm`}>Save clip</a>}
          </span>
        </div>
      </div>
    </div>
  );
}

// What Penjaga listens to: the house's own sensors, live. Nothing here watches people (no cameras, no microphones).
function Sensors({ live }: { live: Live }) {
  const real = BACKEND === 'api';
  const fmt = (v: number | null | undefined, d: number, unit: string) => (v == null ? 'No reading yet' : `${v.toFixed(d)} ${unit}`);
  const tiles: [string, string, string][] = [
    ['Water meter', `${live.water_lpm.toFixed(2)} L/min`, 'Flow into the house'],
    ['Switchboard', `${live.power_kw.toFixed(2)} kW`, 'Every circuit, and both safety switches'],
    ...(real ? [
      ['Water pressure', fmt(live.pressure_bar, 2, 'bar'), 'Mains supply at the inlet'],
      ['Roof tank', fmt(live.tank_pct, 0, '%'), 'How full it is'],
      ['Fridge plug', fmt(live.fridge_w, 0, 'W'), 'Smart plug on the fridge'],
      ['Weather', fmt(live.outdoor_c, 1, '°C'), 'Outdoor temperature, Elmina'],
    ] as [string, string, string][] : []),
  ];
  return (
    <div className="sensors" id="sensors">
      {tiles.map(([k, v, hint]) => <div key={k} title={hint}><span className="micro">{k}</span><b>{v}</b></div>)}
      <p className="tiny muted">Penjaga senses the building, never the people in it: no cameras, no microphones.</p>
    </div>
  );
}

function CheckMeter({ onDone, label = 'Your water meter · live', busy = 'Watching the meter…', ready = "It's closed · check now" }: { onDone: () => void; label?: string; busy?: string; ready?: string }) {
  const [phase, setPhase] = useState<'ready' | 'watching'>('ready');
  const [bars, setBars] = useState<number[]>(() => Array(22).fill(0.66));
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);
  const run = () => {
    setPhase('watching');
    let i = 0;
    timer.current = setInterval(() => {
      i++;
      setBars((b) => [...b.slice(1), 0.58 + Math.random() * 0.14]);
      if (i >= 14) { clearInterval(timer.current!); onDone(); }
    }, 170);
  };
  return (
    <div className="a-card">
      <div className="label">{label}</div>
      <div className="meter" aria-hidden>{bars.map((b, k) => <i key={k} style={{ height: `${b * 100}%` }} />)}</div>
      <button className="a-chip primary" onClick={run} disabled={phase === 'watching'}>{phase === 'watching' ? busy : ready}</button>
    </div>
  );
}

function MgmtDraft({ c, onSend }: { c: Case; onSend: (note: string) => void }) {
  const [note, setNote] = useState(`${c.title} (${c.location}). Detected by Penjaga on ${fmtDay(c.opened_at)}. Is this covered under the defect liability period?`);
  return (
    <div className="a-card">
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4} aria-label="Message to management" />
      <button className="a-chip primary" onClick={() => onSend(note)}><Icon name="send" size={15} /> Send</button>
    </div>
  );
}

