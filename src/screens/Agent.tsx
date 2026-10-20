// Penjaga, the home agent. One living screen: the house fills the stage, Penjaga speaks beside it and shows
// what it's talking about in the house. The owner acts by replying (or typing), never by hunting through pages.
// The conversation is rebuilt from case data on every render (so it survives reloads); only the owner's
// free-form questions and taps on the house are session-only.
import { Fragment, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';
import spec from '../../house.spec.json';
import { useView, api, useLive, houseNow } from '../api/client';
import { ViewerSlot, useEngine, type Engine } from '../app/engine';
import { Icon, Chart, Sheet, SEV_LABEL, fmtDay, fmtTime } from '../components/ui';
import ThinkingOrb, { type ThinkingOrbHandle } from '../components/ThinkingOrb';
import Character, { moodFor, type Mood } from '../components/Character';
import NotifyBell from '../components/NotifyBell';
import type { Case, ChatMessage, FocusCommand, Severity, Vendor } from '../api/types';
import type { ElementInfo, DrillResult } from '../viewer/engine';
import NoticesPanel from './Notices';
import SettingsPanel from './Settings';

type Chip = { label: string; icon?: string; primary?: boolean; tone?: 'danger'; onClick: () => void };
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
const FOLD_KEY = 'penjaga.chat.collapsed';
const SEEN_KEY = 'penjaga.notifications.seen';
/** Something that happened at home, for the bell and its list. */
type Note = { id: string; t: number; tone: Severity | 'ok' | 'calm'; title: string; detail: string; open: () => void };

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}
const usualness = (v: number, t: number) => {
  if (!t) return '';
  const p = Math.round(((v - t) / t) * 100);
  return Math.abs(p) <= 8 ? 'about usual' : p > 0 ? `${p}% more than usual` : `${-p}% less than usual`;
};

export default function Agent({ panel: routePanel }: { panel?: 'notices' | 'settings' | 'record' }) {
  const v = useView();
  const live = useLive();
  const { engine } = useEngine();
  const { id: routeCase } = useParams();
  const nav = useNavigate();
  const [session, setSession] = useState<Item[]>([]);
  const [flow, setFlow] = useState<Flow>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [panel, setPanel] = useState<null | 'notices' | 'settings' | 'record'>(routePanel ?? null);
  const [stage, setStage] = useState({ view: 'split', water: false, electrical: false, xray: false, drill: false });
  // The panel can fold down to its header and the latest message; the choice is remembered on this device.
  const [collapsed, setCollapsedState] = useState(() => { try { return localStorage.getItem(FOLD_KEY) === '1'; } catch { return false; } });
  const setCollapsed = (c: boolean) => { setCollapsedState(c); try { localStorage.setItem(FOLD_KEY, c ? '1' : '0'); } catch { /* ignore */ } };
  const [thinking, setThinking] = useState(false);
  const [drawer, setDrawer] = useState(false);
  // The list drops down under the bell: where the bell is, re-measured if the window changes while it's open.
  const bellRef = useRef<HTMLButtonElement | null>(null);
  const [bellAt, setBellAt] = useState<DOMRect | null>(null);
  const openDrawer = (bell: HTMLButtonElement) => { bellRef.current = bell; setBellAt(bell.getBoundingClientRect()); setDrawer(true); };
  const [seen, setSeen] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) ?? '[]') as string[]); } catch { return new Set(); } });
  const markSeen = (ids: string[]) => setSeen((s) => {
    const n = new Set(s);
    ids.forEach((i) => n.add(i));
    try { localStorage.setItem(SEEN_KEY, JSON.stringify([...n].slice(-500))); } catch { /* ignore */ }
    return n;
  });
  const closeDrawerRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeDrawerRef.current(); };
    const onResize = () => { if (bellRef.current) setBellAt(bellRef.current.getBoundingClientRect()); };
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('resize', onResize); };
  }, [drawer]);
  const orb = useRef<ThinkingOrbHandle>(null);
  const pillSlot = useRef<HTMLDivElement>(null);
  const threadEnd = useRef<HTMLDivElement>(null);
  const caseById = (id: string) => v.cases.find((c) => c.id === id);
  const say = (it: Omit<Item, 'key' | 't'> & { t?: number }) => setSession((s) => [...s, { key: `s${Date.now()}${Math.random()}`, t: houseNow(), ...it }]);
  // What the 3D view is pointing at right now, so the owner can always put it away again.
  const [shown, setShown] = useState<string | null>(null);
  const focusOn = (f: FocusCommand) => {
    const ok = !!engine?.focus(f);
    setShown(ok ? f.label ?? 'This part' : null);
    return ok;
  };
  const unfocus = (opts?: { reframe?: boolean }) => { engine?.clearFocus(opts); setShown(null); };
  useEffect(() => {
    if (!shown) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') unfocus(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [shown]); // eslint-disable-line react-hooks/exhaustive-deps
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
    const chips: Chip[] = [{ label: 'Show me where', icon: 'pin', onClick: () => show(c.focus) }];
    for (const a of c.actions) {
      if (a.kind === 'guided_check') chips.unshift({ label: 'Help me find it', icon: 'water', primary: true, onClick: () => { setFlow({ type: 'check', caseId: c.id }); } });
      if (a.kind === 'book_vendor') chips.splice(a.primary ? 0 : chips.length, 0, { label: a.label, icon: 'wrench', primary: a.primary, tone: c.severity === 'urgent' && a.primary ? 'danger' : undefined, onClick: () => setFlow({ type: 'book', caseId: c.id }) });
      if (a.kind === 'show_in_3d') chips.push({ label: a.label, icon: 'bolt', onClick: () => show(a.focus) });
      if (a.kind === 'send_management') chips.push({ label: 'Tell management', icon: 'send', onClick: () => setFlow({ type: 'mgmt', caseId: c.id }) });
      if (a.kind === 'dismiss') chips.push({ label: 'Not an issue', onClick: () => dismissAsk(c) });
    }
    chips.push({ label: open[`ev:${c.id}`] ? 'Hide the evidence' : 'Why do you think so?', onClick: () => setOpen((o) => ({ ...o, [`ev:${c.id}`]: !o[`ev:${c.id}`] })) });
    if (c.impact.how) chips.push({ label: 'How did you get the cost?', onClick: () => setOpen((o) => ({ ...o, [`cost:${c.id}`]: !o[`cost:${c.id}`] })) });
    return chips;
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
      text: <><b className="a-lead">{c.guided?.done && c.status !== 'resolved' ? 'Something in your home needs a look.' : c.title}.</b> {c.guided?.done ? '' : c.summary}</>,
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
        {' '}So far today you've used {t.water_l.toLocaleString()} L of water ({usualness(t.water_l, t.water_typical_l)}) and {t.elec_kwh} kWh of power ({usualness(t.elec_kwh, t.elec_typical_kwh)}).
        {v.maintenance[0] && <> Next on my list: {v.maintenance[0].title.toLowerCase()}, due {fmtDay(v.maintenance[0].due)}.</>}
      </>
    ),
    frame: true,
    chips: [
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
    lastShown.current = latestKey;
    if (latest.focus) focusOn(latest.focus);
    else { unfocus(); openCases.forEach((c) => engine.flag(c.focus.object_ids.slice(0, 1), c.severity)); }
  }, [engine, latestKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // Follow the newest message, including a reply that replaces the "looking into it" line (same length).
  const lastKey = thread[thread.length - 1]?.key;
  useEffect(() => { threadEnd.current?.scrollIntoView({ behavior: engine?.reduceMotion ? 'auto' : 'smooth', block: 'end' }); }, [thread.length, lastKey, flow, collapsed]);
  useEffect(() => { // notification deep link: open the panel and bring that case forward
    const c = routeCase ? caseById(routeCase) : null;
    if (!c || !engine) return;
    setCollapsed(false);
    focusOn(c.focus);
    requestAnimationFrame(() => document.getElementById(`${c.id}:open`)?.scrollIntoView({ block: 'center' }));
  }, [routeCase, engine]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setPanel(routePanel ?? null); }, [routePanel]);
  const flowCase = flow ? caseById(flow.caseId) : null;
  useEffect(() => { if (flow?.type === 'check' && flowCase?.guided?.done) setFlow(null); }, [flow, flowCase?.guided?.done]); // eslint-disable-line react-hooks/exhaustive-deps

  const onReady = (e: Engine) => { e.setInteractive(true); e.setAutoRotate(false); e.setInsets({ left: 470 }); lastShown.current = ''; return () => e.setInsets({ left: 0 }); };

  // The orb shows Penjaga working on it; the reply enters the thread when the orb resolves.
  const ask = async (q: string) => {
    say({ who: 'you', text: q });
    setThinking(true);
    const m = await api.ask(q, { caseId: openCases[0]?.id });
    return () => { setThinking(false); reply(m); };
  };
  const reply = (a: ChatMessage) => {
    say({
      who: 'penjaga', tone: 'calm', text: a.text, focus: a.chips?.find((c) => c.focus)?.focus,
      chips: a.chips?.map((c) => ({
        label: c.label, icon: c.focus ? 'pin' : undefined,
        onClick: () => { if (c.focus) show(c.focus); else if (c.drill) engine?.setDrill(true); else if (c.to?.startsWith('/cases/')) { const id = c.to.split('/').pop()!; const cc = caseById(id); if (cc) { show(cc.focus); document.getElementById(`${id}:open`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }); } } },
      })),
    });
  };
  const preview = [...thread].reverse().find((i) => i.who === 'penjaga' && i.text);
  // Penjaga's face in the header: thinking while it answers, else how the house is (asleep in quiet night hours,
  // pleased just after a fix).
  const hour = new Date(houseNow()).getHours();
  const justFixed = !openCases.length && v.cases.some((c) => c.status === 'resolved' && houseNow() - (c.resolved_at ?? 0) < 3600e3);
  const headMood: Mood = thinking ? 'thinking'
    : status === 'urgent' ? 'concerned'
      : status === 'attention' ? 'alert'
        : justFixed ? 'happy'
          : hour >= 23 || hour < 6 ? 'sleeping' : 'calm';
  const suggestions = openCases.length
    ? ['What happens if I wait?', 'How much is this costing me?', "What's wrong at home?"]
    : ['Where does the master bathroom heater get its power?', 'Where are the pipes in Bathroom 2?', 'Where is my water tank?'];

  // ---------- notifications: the bell keeps ringing for anything new until the owner opens the list ----------
  const lookAt = (elId: string, focus?: FocusCommand) => {
    setCollapsed(false);
    show(focus);
    setTimeout(() => document.getElementById(elId)?.scrollIntoView({ block: 'center', behavior: engine?.reduceMotion ? 'auto' : 'smooth' }), 80);
  };
  const notes: Note[] = [];
  for (const c of v.cases) {
    const look = () => lookAt(`${c.id}:open`, c.focus);
    notes.push({ id: `case:${c.id}`, t: c.opened_at, tone: c.severity, title: c.title, detail: c.location, open: look });
    c.timeline.forEach((e, k) => {
      if (e.actor === 'vendor' || e.actor === 'management') notes.push({ id: `case:${c.id}:${k}`, t: e.t, tone: 'calm', title: e.text, detail: c.title, open: look });
    });
    if (c.status === 'resolved') notes.push({ id: `case:${c.id}:fixed`, t: c.resolved_at ?? c.opened_at, tone: 'ok', title: `Fixed: ${c.title}`, detail: c.outcome ?? c.location, open: () => lookAt(`${c.id}:resolved`) });
  }
  for (const n of v.notices) {
    notes.push({ id: `notice:${n.id}`, t: Math.min(n.starts_at, houseNow()), tone: 'info', title: n.title, detail: `${n.issuer} · ${fmtDay(n.starts_at)}, ${fmtTime(n.starts_at)}–${fmtTime(n.ends_at)}`, open: () => setPanel('notices') });
  }
  if (v.wellbeing) {
    const w = v.wellbeing;
    notes.push({ id: `wb:${w.id}`, t: w.since, tone: 'info', title: w.title, detail: 'Family check', open: () => lookAt(`wb:${w.id}`, { layer: 'structure', object_ids: ['floor_GF_bed4', 'floor_GF_bath4'], severity: 'info', label: "Mak's suite" }) });
  }
  notes.sort((a, b) => b.t - a.t);
  const fresh = notes.filter((n) => !seen.has(n.id) && n.t >= houseNow() - 3 * 86400e3);
  const earlier = notes.filter((n) => !fresh.includes(n));
  const bellTone = fresh.some((n) => n.tone === 'urgent') ? 'urgent' : fresh.some((n) => n.tone === 'act_soon' || n.tone === 'heads_up') ? 'act' : 'calm';
  const closeDrawer = () => { setDrawer(false); markSeen(notes.map((n) => n.id)); };
  closeDrawerRef.current = closeDrawer;
  // the list's left edge sits just left of the bell (kept on screen), with a notch pointing up at it
  const drawerW = Math.min(400, window.innerWidth - 24);
  const drawerAt = bellAt && (() => {
    const mid = bellAt.left + bellAt.width / 2;
    const left = Math.max(12, Math.min(mid - 30, window.innerWidth - drawerW - 12));
    return { left, top: bellAt.bottom + 10, right: 'auto', '--notch': `${mid - left}px` } as CSSProperties;
  })();
  const when = (t: number) => (new Date(t).toDateString() === new Date(houseNow()).toDateString() ? fmtTime(t) : fmtDay(t));

  return (
    <div className={`agent ${collapsed ? 'is-collapsed' : ''}`}>
      <div className="agent-stage">
        <ViewerSlot label="Your home in 3D. Tap anything and Penjaga will tell you about it." onReady={onReady} />
        <div className="stage-tools-r">
          <NotifyBell count={fresh.length} tone={bellTone} ringing={fresh.length > 0 && !drawer} onClick={openDrawer} />
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
          <button className="round" onClick={() => unfocus()} aria-label="Whole house"><Icon name="house3d" size={17} /></button>
        </div>
        {shown && (
          <div className="stage-shown" role="status">
            <span className="stage-shown-what">Showing <b>{shown}</b></span>
            <button onClick={() => unfocus()}><Icon name="close" size={14} /><span className="long">Back to whole house</span><span className="short">Whole house</span></button>
          </div>
        )}
      </div>

      <section className={`agent-col ${collapsed ? 'collapsed' : ''}`} aria-label="Penjaga">
        <header className="agent-head">
          <Character mood={headMood} size={30} live />
          <div className="agent-title">
            <b>Penjaga</b>
            <span className="sep" aria-hidden="true">|</span>
            <span className="listening">Listening · water {live.water_lpm.toFixed(2)} L/min · power {live.power_kw.toFixed(1)} kW</span>
          </div>
          <button className="icon-btn fold" onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed} aria-label={collapsed ? 'Open the conversation' : 'Fold the conversation'}><Icon name="chevron" size={16} /></button>
        </header>

        {collapsed ? (
          preview && (
            <button className={`a-preview ${preview.tone ?? ''}`} onClick={() => setCollapsed(false)} aria-label="Open the conversation">
              <span className="a-avatar pj" aria-hidden="true"><Character mood={moodFor(preview.tone)} size={28} live /></span>
              <span className="a-body">
                <span className="a-meta"><b className="a-name">Penjaga</b>{preview.kicker && <span className="a-kicker">{preview.kicker}</span>}</span>
                <span className="a-line">{preview.text}</span>
              </span>
            </button>
          )
        ) : (
          <div className="thread" aria-live="polite">
            {thread.map((i, n) => {
              const cont = n > 0 && thread[n - 1].who === i.who && !i.kicker; // same speaker again: no second avatar
              return (
                <article key={i.key} id={i.key} className={`a-item ${i.who} ${i.tone ?? ''} ${cont ? 'cont' : ''}`}>
                  <span className={`a-avatar ${i.who === 'penjaga' ? 'pj' : 'me'}`} aria-hidden="true">{i.who === 'penjaga' ? <Character mood={moodFor(i.tone)} size={28} live={i.key === preview?.key} /> : 'A'}</span>
                  <div className="a-body">
                    {!cont && <div className="a-meta"><b className="a-name">{i.who === 'penjaga' ? 'Penjaga' : 'You'}</b>{i.kicker && <span className="a-kicker">{i.kicker}</span>}</div>}
                    {i.text && <div className="a-text">{i.text}</div>}
                    {i.card}
                    {i.chips && i.chips.length > 0 && (
                      <div className="a-chips">
                        {i.chips.map((ch) => (
                          <button key={ch.label} className={`a-chip ${ch.primary ? 'primary' : ''} ${ch.tone ?? ''}`} onClick={ch.onClick}>
                            {ch.icon && <Icon name={ch.icon} size={14} />}{ch.label}
                          </button>
                        ))}
                      </div>
                    )}
                    {i.who === 'penjaga' && i.focus && !i.chips?.length && i.key !== latest?.key && (
                      <button className="a-show" onClick={() => show(i.focus)}><Icon name="pin" size={13} /> Show in the house</button>
                    )}
                  </div>
                </article>
              );
            })}
            <div ref={threadEnd} />
          </div>
        )}

        <div className="agent-compose">
          {!collapsed && <div className="suggest">{suggestions.map((s) => <button type="button" key={s} onClick={() => orb.current?.ask(s)}>{s}</button>)}</div>}
          <div className="compose-slot" ref={pillSlot} />
          {!collapsed && <div className="compose-note">Penjaga acts only after you confirm</div>}
        </div>
        <ThinkingOrb ref={orb} anchor={pillSlot} onAsk={ask} onFocus={() => { if (collapsed) setCollapsed(false); }} />
      </section>

      {drawer && (
        <>
          <div className="ndrawer-scrim" onClick={closeDrawer} />
          <section className="ndrawer" role="dialog" aria-label="Notifications" style={drawerAt ?? undefined}>
            <header className="ndrawer-h">
              <h2>Notifications</h2>
              <button className="icon-btn" onClick={closeDrawer} aria-label="Close"><Icon name="close" size={16} /></button>
            </header>
            <div className="ndrawer-list">
              {!notes.length && <div className="ndrawer-empty">Nothing yet. When something happens at home, the bell will ring.</div>}
              {fresh.length > 0 && <div className="label ndrawer-sec">New</div>}
              {[...fresh, ...earlier.slice(0, 30)].map((n, k) => (
                <Fragment key={n.id}>
                  {k === fresh.length && fresh.length > 0 && <div className="label ndrawer-sec">Earlier</div>}
                  <button className={`nitem ${k < fresh.length ? 'new' : ''}`} onClick={() => { closeDrawer(); n.open(); }}>
                    <Character mood={moodFor(n.tone)} size={30} />
                    <span className="nbody">
                      <span className="ntitle"><span>{n.title}</span><span className="ntime">{when(n.t)}</span></span>
                      <span className="ndetail">{n.detail}</span>
                    </span>
                  </button>
                </Fragment>
              ))}
            </div>
            <div className="ndrawer-links">
              <button onClick={() => { closeDrawer(); setPanel('record'); }}><Icon name="house3d" size={16} />Home Passport</button>
              <button onClick={() => { closeDrawer(); setPanel('notices'); }}><Icon name="notices" size={16} />Notices</button>
              <button onClick={() => { closeDrawer(); setPanel('settings'); }}><Icon name="shield" size={16} />Privacy</button>
              <button onClick={() => { closeDrawer(); nav('/welcome'); }}><Icon name="replay" size={16} />Replay the tour</button>
            </div>
          </section>
        </>
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
function CheckMeter({ onDone }: { onDone: () => void }) {
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
      <div className="label">Your water meter · live</div>
      <div className="meter" aria-hidden>{bars.map((b, k) => <i key={k} style={{ height: `${b * 100}%` }} />)}</div>
      <button className="a-chip primary" onClick={run} disabled={phase === 'watching'}>{phase === 'watching' ? 'Watching the meter…' : "It's closed · check now"}</button>
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

