// Penjaga, the home agent. One living screen: the house fills the stage, Penjaga speaks beside it and shows
// what it's talking about in the house. The owner acts by replying (or typing), never by hunting through pages.
// The conversation is rebuilt from case data on every render (so it survives reloads); only the owner's
// free-form questions and taps on the house are session-only.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';
import spec from '../../house.spec.json';
import { useView, api, useLive, houseNow } from '../api/client';
import { ViewerSlot, useEngine, type Engine } from '../app/engine';
import { Icon, Chart, Sheet, SEV_LABEL, fmtDay, fmtTime } from '../components/ui';
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
  const [panel, setPanel] = useState<null | 'menu' | 'notices' | 'settings' | 'record'>(routePanel ?? null);
  const [stage, setStage] = useState({ view: 'split', water: false, electrical: false, xray: false, drill: false });
  const [text, setText] = useState('');
  const threadEnd = useRef<HTMLDivElement>(null);
  const caseById = (id: string) => v.cases.find((c) => c.id === id);
  const say = (it: Omit<Item, 'key' | 't'> & { t?: number }) => setSession((s) => [...s, { key: `s${Date.now()}${Math.random()}`, t: houseNow(), ...it }]);
  // A part the 3D model doesn't have yet: clear the old highlight (it would look like the answer) and say so.
  const show = (f?: FocusCommand) => {
    if (!f || !engine) return;
    if (!engine.focus(f)) {
      engine.clearFocus();
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
      { label: 'Show me my pipes and wiring', icon: 'layers', onClick: () => { engine?.clearFocus(); engine?.setLayer('water', true); engine?.setLayer('electrical', true); } },
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
    if (latest.focus) engine.focus(latest.focus);
    else { engine.clearFocus(); openCases.forEach((c) => engine.flag(c.focus.object_ids.slice(0, 1), c.severity)); }
  }, [engine, latestKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // Follow the newest message, including a reply that replaces the "looking into it" line (same length).
  const lastKey = thread[thread.length - 1]?.key;
  useEffect(() => { threadEnd.current?.scrollIntoView({ behavior: engine?.reduceMotion ? 'auto' : 'smooth', block: 'end' }); }, [thread.length, lastKey, flow]);
  useEffect(() => { // notification deep link: bring that case forward
    const c = routeCase ? caseById(routeCase) : null;
    if (c && engine) { engine.focus(c.focus); document.getElementById(`${c.id}:open`)?.scrollIntoView({ block: 'center' }); }
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
      setSession((s) => [...s, { key, t: houseNow(), who: 'penjaga', tone: 'calm', text: <span className="muted">Penjaga is looking into it…</span> }]);
      void a.then((m) => { setSession((s) => s.filter((it) => it.key !== key)); reply(m); });
    } else setTimeout(() => reply(a), 500);
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
              <button key={id} className={stage.view === id ? 'on' : ''} onClick={() => { engine?.clearFocus({ reframe: false }); engine?.setView(id); }}>{l}</button>
            ))}
          </div>
          <div className="seg" role="group" aria-label="What to show">
            <button className={stage.water ? 'on' : ''} onClick={() => engine?.setLayer('water', !stage.water)} aria-label="Water"><Icon name="water" size={15} /><span className="lbl">Water</span></button>
            <button className={stage.electrical ? 'on' : ''} onClick={() => engine?.setLayer('electrical', !stage.electrical)} aria-label="Power"><Icon name="bolt" size={15} /><span className="lbl">Power</span></button>
            <button className={stage.xray ? 'on' : ''} onClick={() => engine?.setXray(!stage.xray)} aria-label="See through walls"><Icon name="eye" size={15} /><span className="lbl">See through</span></button>
          </div>
          <div className="seg"><button className={stage.drill ? 'on' : ''} onClick={() => engine?.setDrill(!stage.drill)} aria-label="Can I drill here?"><Icon name="drill" size={15} /><span className="lbl">Drill check</span></button></div>
          <button className="round" onClick={() => { engine?.clearFocus(); }} aria-label="Whole house"><Icon name="house3d" size={17} /></button>
        </div>
      </div>

      <section className="agent-col" aria-label="Penjaga">
        <header className="agent-head">
          <span className="agent-mark"><Icon name="shield" size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b className="serif" style={{ fontSize: 19 }}>Penjaga</b>
            <div className="listening"><i className={status} />Listening · water {live.water_lpm.toFixed(2)} L/min · power {live.power_kw.toFixed(1)} kW</div>
          </div>
          <button className="round" onClick={() => setPanel('menu')} aria-label="Your home"><Icon name="cases" size={17} /></button>
        </header>

        <div className="thread" aria-live="polite">
          {thread.map((i) => (
            <article key={i.key} id={i.key} className={`a-item ${i.who} ${i.tone ?? ''}`}>
              {i.who === 'penjaga' && i.kicker && <div className="a-kicker">{i.kicker}</div>}
              {i.text && <div className="a-text">{i.text}</div>}
              {i.card}
              {i.chips && i.chips.length > 0 && (
                <div className="a-chips">
                  {i.chips.map((ch) => (
                    <button key={ch.label} className={`a-chip ${ch.primary ? 'primary' : ''} ${ch.tone ?? ''}`} onClick={ch.onClick}>
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

