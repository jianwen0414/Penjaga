// Penjaga, the home agent. One living screen: the house fills the stage, Penjaga speaks beside it and shows
// what it's talking about in the house. The owner acts by replying (or typing), never by hunting through pages.
// The conversation is rebuilt from case data on every render (so it survives reloads); only the owner's
// free-form questions and taps on the house are session-only.
import { Fragment, useCallback, useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';
import spec from '../../house.spec.json';
import { useView, api, useLive, houseNow, BACKEND, type Live } from '../api/client';
import { ViewerSlot, useEngine, type Engine } from '../app/engine';
import { Icon, Chart, Sheet, SEV_LABEL, fmtDay, fmtTime } from '../components/ui';
import Character, { moodFor, type Mood } from '../components/Character';
import LiveCharacter, { type Activity } from '../character/LiveCharacter';
import Resting from '../components/Resting';
import NotifyBell from '../components/NotifyBell';
import MiniOrb from '../components/MiniOrb';
import { useLiquidGlass } from '../components/LiquidGlass';
import type { Case, ChatMessage, FocusCommand, Severity, Vendor } from '../api/types';
import type { ElementInfo, DrillResult, InspectPhase } from '../viewer/engine';
import { canListen, listen, type ListenEnd, type Listening } from '../voice/listen';
import * as speech from '../voice/speak';
import { useVoicePrefs, voicePrefs } from '../voice/prefs';
import { placeFrom, roomName as placeName, type Place } from '../voice/place';
import { answerKind, isEmergency, planFor, type EmergencyPlan } from '../voice/emergency';
import { routeTo, the } from '../nav/route';
import LiveWords from '../voice/LiveWords';
import { EmSteps, ListeningLine, MicIcon, WaterCheck } from '../voice/ui';
import '../styles/voice.css';
import NoticesPanel from './Notices';
import SettingsPanel from './Settings';

type Chip = { label: string; icon?: string; primary?: boolean; quiet?: boolean; flip?: boolean; tone?: 'danger'; onClick: () => void }; // quiet: a minor follow-up, drawn as a link
type Item = {
  key: string; t: number; who: 'penjaga' | 'you';
  kicker?: string; tone?: Severity | 'ok' | 'calm';
  text?: ReactNode; card?: ReactNode; chips?: Chip[];
  focus?: FocusCommand; frame?: boolean;
  still?: boolean; // arrives without the slide-in (the owner's spoken words, already on screen as they spoke)
};
// An emergency in progress: the plan (where to go, what to do), and how far the owner has got.
type Em = { plan: EmergencyPlan; stage: 'ask' | 'which' | 'route' | 'done'; room?: string; askKey?: string };

// Rest after this long untouched (seconds; ?idle=10 to try it quickly).
const IDLE_MS = (Number(new URLSearchParams(location.search).get('idle')) || 75) * 1000;
const HANDLED_KEY = 'penjaga.emergency.handled';
// rooms to tap when Penjaga asks where you are (the rest come from "Somewhere else")
const QUICK_ROOMS = ['living', 'kitchen', 'dining', 'bed4', 'master', 'bed2', 'bed3', 'family'];
const ALL_ROOMS = (spec as unknown as { rooms: { id: string }[] }).rooms.map((r) => r.id);
const ROOM_FLOOR = new Map((spec as unknown as { rooms: { id: string; floor: string }[] }).rooms.map((r) => [r.id, r.floor]));
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
  const [panel, setPanel] = useState<null | 'notices' | 'settings' | 'record'>(routePanel ?? null);
  const [stage, setStage] = useState({ view: 'split', water: false, electrical: false, xray: false, drill: false });
  // The panel can fold down to its header and the latest message; the choice is remembered on this device.
  const [collapsed, setCollapsedState] = useState(() => { try { return localStorage.getItem(FOLD_KEY) === '1'; } catch { return false; } });
  const setCollapsed = (c: boolean) => { setCollapsedState(c); try { localStorage.setItem(FOLD_KEY, c ? '1' : '0'); } catch { /* ignore */ } };
  const [thinking, setThinking] = useState(false);
  // On a wide screen the panel floats over the house as liquid glass; on a phone it sits below the house, opaque.
  const [wide, setWide] = useState(() => matchMedia('(min-width: 960px)').matches);
  useEffect(() => {
    const q = matchMedia('(min-width: 960px)');
    const on = () => setWide(q.matches);
    q.addEventListener('change', on);
    return () => q.removeEventListener('change', on);
  }, []);
  const glass = useLiquidGlass({ material: 'regular', refraction: 44, bezel: 30, enabled: wide });
  const [text, setText] = useState('');
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
    // "the fridge", but "the Bathroom 2 toilet cistern"; no ", in the bathroom 2" when the name already says so
    const name = (label?: string) => {
      const l = (label || 'spot').replace(/^the /i, '');
      return /^[A-Z][a-z]+ \d/.test(l) || l.startsWith('"') ? l : l.replace(/^./, (ch) => ch.toLowerCase());
    };
    const inRoom = r.room && !(focus.label ?? '').toLowerCase().includes(r.room.toLowerCase()) ? `, in the ${r.room.toLowerCase()}` : '';
    const shot: Shot = { image: r.image, video: r.video ? URL.createObjectURL(r.video) : undefined, title: c.title, where: r.room ?? c.location };
    say({
      who: 'penjaga', tone: 'calm', kicker: '3D inspection',
      text: focus !== c.focus
        ? <><b className="a-lead">Here's the {name(focus.label)}{inRoom}.</b></>
        : <><b className="a-lead">Here it is: the {name(focus.label)}{inRoom}.</b> It's marked in red.</>,
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

  // ---------- talking: the owner's voice in (only when they tap the mic, or answering Penjaga in an emergency) ----------
  const speaker = useSyncExternalStore(speech.subscribe, speech.snapshot);
  const prefs = useVoicePrefs();
  const [hearing, setHearing] = useState<{ text: string; settled: string } | null>(null);
  const listening = useRef<Listening | null>(null);
  const micLevel = useRef(0);
  const getMic = useCallback(() => micLevel.current, []);
  // the mic level drives a few CSS variables (the ring round the mic, the owner's avatar) without re-rendering
  const setMic = (l: number) => {
    micLevel.current = l;
    document.querySelectorAll<HTMLElement>('[data-mic]').forEach((el) => el.style.setProperty('--mic', l.toFixed(3)));
  };
  const lastSpoken = useRef('');
  /** Say something aloud (and keep it, in case the browser held the sound back and the owner taps to hear it). */
  const speak = (text: string) => {
    lastSpoken.current = text;
    return speech.say(text, { browserOnly: BACKEND !== 'api' });
  };
  /** Listen once: the words appear in the chat as they're heard; resolves with what was said and why it ended. */
  const hear = async (opts: { maxMs?: number; noSpeechMs?: number; silenceMs?: number } = {}) => {
    speech.cancel();
    listening.current?.abort();
    setHearing({ text: '', settled: '' });
    const l = listen({ lang: 'en-GB', maxMs: 20_000, noSpeechMs: 8_000, silenceMs: 1_300, ...opts, onWords: (text, settled) => setHearing({ text, settled }), onLevel: setMic });
    listening.current = l;
    const r = await l.done;
    if (listening.current === l) { listening.current = null; setHearing(null); }
    return r;
  };
  const stopTalking = () => { listening.current?.abort(); listening.current = null; setHearing(null); speech.cancel(); };
  useEffect(() => () => { listening.current?.abort(); speech.cancel(); }, []);
  const micTrouble = (end: ListenEnd) => {
    if (end === 'denied') say({ who: 'penjaga', tone: 'calm', text: "I can't hear you: the microphone is blocked for this page. Allow it in your browser's address bar, or type instead." });
    else if (end === 'unavailable') say({ who: 'penjaga', tone: 'calm', text: "This browser can't listen. Try Chrome or Edge, or type your question." });
    else if (end === 'error') say({ who: 'penjaga', tone: 'calm', text: "I couldn't hear that (the speech service didn't answer). Try again, or type instead." });
  };
  /** The mic button, or tapping Penjaga: listen for a question, then answer it out loud. Tapping again stops. */
  async function talk() {
    if (hearing) { listening.current?.stop(); return; }
    if (em && (em.stage === 'ask' || em.stage === 'which' || em.stage === 'route')) { void emergencyListen(); return; }
    if (!canListen()) { micTrouble('unavailable'); return; }
    if (collapsed) setCollapsed(false);
    const r = await hear();
    if (r.text) void ask(r.text, { voice: true });
    else micTrouble(r.end);
  }

  // ---------- resting: untouched for a while, the panel steps aside and Penjaga dozes over the house ----------
  const [rest, setRest] = useState<'off' | 'on' | 'leaving'>('off');
  const [em, setEmState] = useState<Em | null>(null);
  const emRef = useRef<Em | null>(null);
  const setEm = (next: Em | null) => { emRef.current = next; setEmState(next); };
  const busy = !!(hearing || speaker.speaking || thinking || flow || hud || scanning || drawer || panel || viewer || em || text.trim());
  useEffect(() => {
    if (busy || rest !== 'off' || !engine) return;
    let timer = setTimeout(() => setRest('on'), IDLE_MS);
    const poke = () => { clearTimeout(timer); timer = setTimeout(() => setRest('on'), IDLE_MS); };
    const evs = ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart'] as const;
    evs.forEach((ev) => window.addEventListener(ev, poke, { capture: true, passive: true }));
    return () => { clearTimeout(timer); evs.forEach((ev) => window.removeEventListener(ev, poke, { capture: true })); };
  }, [busy, rest, engine]);
  useEffect(() => {
    if (!engine || rest !== 'on') return;
    // the house takes the whole stage and turns slowly
    unfocus({ reframe: false });
    engine.setInsets({ left: 0 });
    engine.frame(1.6, { tight: 0.9 });
    engine.setAutoRotate(true);
    return () => { engine.setAutoRotate(false); engine.setInsets({ left: 470 }); engine.frame(1.2); };
  }, [rest, engine]); // eslint-disable-line react-hooks/exhaustive-deps
  const wake = useCallback((andTalk = false) => {
    setRest((r) => (r === 'on' ? 'leaving' : r));
    setTimeout(() => { setRest('off'); if (andTalk) void talkRef.current(); }, 760);
  }, []);
  const talkRef = useRef(talk);
  talkRef.current = talk;

  // ---------- an emergency: Penjaga speaks first, asks where you are, and walks you to what makes it safe ----------
  const handled = useRef<Set<string>>((() => { try { return new Set(JSON.parse(localStorage.getItem(HANDLED_KEY) ?? '[]') as string[]); } catch { return new Set<string>(); } })());
  const markHandled = (id: string) => {
    handled.current.add(id);
    try { localStorage.setItem(HANDLED_KEY, JSON.stringify([...handled.current].slice(-100))); } catch { /* this session only */ }
  };
  const retire = (key?: string) => { if (key) setSession((s) => s.map((it) => (it.key === key ? { ...it, chips: undefined } : it))); };
  const sayKey = (it: Omit<Item, 'key' | 't'>) => { const key = `em${Date.now()}${Math.random()}`; setSession((s) => [...s, { key, t: houseNow(), ...it }]); return key; };
  useEffect(() => {
    if (!engine || emRef.current) return;
    const c = v.cases.find((x) => isEmergency(x) && !handled.current.has(x.id));
    if (!c) return;
    markHandled(c.id);
    const plan = planFor(c, (id) => engine.roomOf(id));
    if (plan) void startEmergency(plan); // no plan: the case's own safety steps show; no destination is invented
  }, [v.cases, engine]); // eslint-disable-line react-hooks/exhaustive-deps

  const roomChips = (rooms: string[], more: boolean): Chip[] => [
    ...rooms.map((r) => ({ label: placeName(r).replace(/^Flexi-Suite bedroom$/, 'Flexi-Suite'), onClick: () => placeTapped(r) })),
    ...(more ? [{ label: 'Somewhere else', icon: 'chevron', quiet: true, onClick: () => askWhichOf(ALL_ROOMS.filter((r) => !QUICK_ROOMS.includes(r)), 'room', false) }] : []),
    { label: "I'm not home", quiet: true, onClick: () => placeTapped('away') },
  ];
  async function startEmergency(plan: EmergencyPlan) {
    stopTalking();
    if (rest === 'on') wake(false);
    setFlow(null);
    setCollapsed(false);
    const askKey = sayKey({ who: 'penjaga', tone: 'urgent', kicker: 'Urgent · right now', text: <><b className="a-lead">{plan.alarm}</b> Where are you right now?</>, chips: roomChips(QUICK_ROOMS, true) });
    setEm({ plan, stage: 'ask', askKey });
    await speak(`${plan.alarm} Where are you right now?`);
    if (emRef.current?.plan === plan && emRef.current.stage === 'ask') void emergencyListen();
  }
  /** Listen for the owner's answer to Penjaga's question (only while the emergency is asking). */
  async function emergencyListen(tries = 0) {
    const e = emRef.current;
    if (!e || !voicePrefs().emergencyListen || !canListen()) return;
    const asking = e.stage === 'ask' || e.stage === 'which';
    const r = await hear(asking ? { maxMs: 14_000, noSpeechMs: 9_000 } : { maxMs: 40_000, noSpeechMs: 30_000, silenceMs: 1_100 });
    const now = emRef.current;
    if (!now || now.plan !== e.plan) return;
    if (!r.text) {
      if (r.end === 'denied' || r.end === 'unavailable' || r.end === 'error') micTrouble(r.end);
      else if (asking) say({ who: 'penjaga', tone: 'calm', text: "I didn't hear you. Tap where you are, or tap the mic and tell me." });
      return;
    }
    say({ who: 'you', text: r.text, still: true });
    if (now.stage === 'ask' || now.stage === 'which') placeSaid(placeFrom(r.text), tries);
    else if (now.stage === 'route') doneSaid(r.text, tries);
  }
  function placeSaid(p: Place, tries: number) {
    const e = emRef.current!;
    if (p.kind === 'room') { void goRoute(p.room); return; }
    if (p.kind === 'away') { goAway(); return; }
    if (p.kind === 'ambiguous') { askWhichOf(p.options, p.what, true); return; }
    if (tries < 1) {
      retire(e.askKey);
      const key = sayKey({ who: 'penjaga', tone: 'urgent', text: 'Sorry, which room are you in?', chips: roomChips(QUICK_ROOMS, true) });
      setEm({ ...e, askKey: key });
      void speak('Sorry, which room are you in?').then(() => { if (emRef.current?.askKey === key) void emergencyListen(tries + 1); });
    } else say({ who: 'penjaga', tone: 'calm', text: 'Tap the room you are in.' });
  }
  function askWhichOf(options: string[], what: string, listenAfter: boolean) {
    const e = emRef.current;
    if (!e) return;
    retire(e.askKey);
    const q = `Which ${what}?`;
    const key = sayKey({ who: 'penjaga', tone: 'urgent', text: q, chips: roomChips(options, false) });
    setEm({ ...e, stage: 'which', askKey: key });
    if (listenAfter) void speak(q).then(() => { if (emRef.current?.askKey === key) void emergencyListen(1); });
  }
  function placeTapped(room: string) {
    const e = emRef.current;
    if (!e || (e.stage !== 'ask' && e.stage !== 'which')) return;
    listening.current?.abort();
    say({ who: 'you', text: room === 'away' ? "I'm not home." : `I'm in ${the(room)}.` });
    if (room === 'away') goAway(); else void goRoute(room);
  }
  async function goRoute(room: string) {
    const e = emRef.current;
    if (!e || !engine) return;
    retire(e.askKey);
    const { plan } = e;
    const r = routeTo(room, plan.target);
    const there = `The ${plan.label.toLowerCase()} is ${plan.where}.`;
    if (r) engine.route(r, { from: `You · ${placeName(room)}`, to: plan.label });
    const walk = r?.say ?? '';
    const key = sayKey({
      who: 'penjaga', tone: 'urgent', kicker: 'Go now',
      text: <><b className="a-lead">{walk || there}</b>{walk ? ` ${there}` : ''}</>,
      card: <div className="a-card"><EmSteps steps={[plan.action, ...plan.after]} /></div>,
      chips: [
        { label: plan.verify === 'water' ? "Done, it's closed" : "Done", icon: 'check', primary: true, onClick: () => { listening.current?.abort(); say({ who: 'you', text: 'Done.' }); finish(); } },
        { label: "I can't find it", onClick: () => { listening.current?.abort(); say({ who: 'you', text: "I can't find it." }); void lost(); } },
        { label: 'Stop guiding', quiet: true, onClick: () => endEmergency() },
      ],
    });
    setEm({ ...e, stage: 'route', room, askKey: key });
    await speak(`${walk} ${there} ${plan.action} ${plan.ask}`);
    if (emRef.current?.askKey === key) void emergencyListen();
  }
  function doneSaid(said: string, tries: number) {
    const kind = answerKind(said);
    if (kind === 'done') finish();
    else if (kind === 'lost') void lost();
    else if (kind === 'wait' && tries < 3) void speak("Okay. Tell me when it's done.").then(() => void emergencyListen(tries + 1));
    else say({ who: 'penjaga', tone: 'calm', text: "Tap Done when it's done, or tell me." });
  }
  async function lost() {
    const e = emRef.current;
    if (!e || !engine) return;
    const focus: FocusCommand = 'device' in e.plan.target
      ? { layer: e.plan.target.device.startsWith('valve') ? 'water' : 'electrical', object_ids: [e.plan.target.device], severity: 'urgent', label: e.plan.label }
      : { layer: 'structure', object_ids: [`floor_${ROOM_FLOOR.get(e.plan.target.room)}_${e.plan.target.room}`], severity: 'urgent', label: e.plan.label };
    const line = `It's ${e.plan.where}. Here's what it looks like.`;
    say({ who: 'penjaga', tone: 'urgent', text: line });
    const run = speak(line);
    const c = caseById(e.plan.caseId);
    if (c && engine.has(focus.object_ids[0])) await inspectCase(c, focus);
    await run;
    if (emRef.current?.plan === e.plan) void emergencyListen(1);
  }
  function finish() {
    const e = emRef.current;
    if (!e) return;
    retire(e.askKey);
    engine?.clearRoute();
    if (e.plan.sim) api.ownerDid(e.plan.sim);
    const c = caseById(e.plan.caseId);
    const next = e.plan.after[0];
    const watch = e.plan.verify === 'water' && BACKEND === 'api';
    say({
      who: 'penjaga', tone: 'calm', kicker: 'Well done',
      text: <><b className="a-lead">Good.</b> {next ? `Next: ${next}` : "You've made it safe for now."}</>,
      card: watch ? <WaterCheck onStopped={() => void speak('Your water meter has stopped. No more water is coming in from the mains.')} /> : undefined,
      chips: c ? caseChips(c).filter((ch) => !ch.quiet) : undefined,
    });
    void speak(`Good. ${next ? `Next: ${next}` : ''}`);
    setEm(null);
  }
  function goAway() {
    const e = emRef.current;
    if (!e) return;
    retire(e.askKey);
    const { plan } = e;
    const text = `Okay. If anyone is at home, ask them to do this now: ${plan.action} The ${plan.label.toLowerCase()} is ${plan.where}.`;
    if ('device' in plan.target) show({ layer: plan.target.device.startsWith('valve') ? 'water' : 'electrical', object_ids: [plan.target.device], severity: 'urgent', label: plan.label });
    const c = caseById(plan.caseId);
    say({ who: 'penjaga', tone: 'urgent', text, chips: c ? caseChips(c).filter((ch) => !ch.quiet) : undefined });
    void speak(text);
    setEm(null);
  }
  function endEmergency() {
    const e = emRef.current;
    stopTalking();
    engine?.clearRoute();
    if (e) retire(e.askKey);
    setEm(null);
    say({ who: 'penjaga', tone: 'calm', text: "Okay, I've stopped guiding. The steps are still above if you need them." });
  }

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
        <span className="a-hello">{greeting()}, Master.</span>{' '}
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
      text: <><b className="a-lead">{w.title}.</b> {w.body} I only look at water and power use: there are no cameras or microphones in your home.</>,
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
  // Some items carry "just now" times (notices, the evidence opened on a tap): each keeps the time it first appeared,
  // so it stays where it came in instead of sliding below everything said since.
  const placedAt = useRef(new Map<string, number>());
  const place = (it: Item) => {
    if (!placedAt.current.has(it.key)) placedAt.current.set(it.key, it.t);
    return { ...it, t: placedAt.current.get(it.key)! };
  };
  // One timeline: case stories, notices and the owner's own questions in the order they reached the owner; the step
  // in progress (a guided check, a booking) stays at the bottom.
  const thread = [briefing, ...[...others, ...stories].map(place).concat(session).sort((a, b) => a.t - b.t), ...flowItems()];

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

  // A small thinking orb shows Penjaga working on it until the reply lands.
  // A spoken question (voice) is answered out loud as well; a typed one only on screen.
  const ask = async (q: string, opts: { voice?: boolean } = {}) => {
    if (!q.trim() || thinking) return;
    if (collapsed) setCollapsed(false);
    if (!opts.voice) speech.cancel();
    say({ who: 'you', text: q, still: opts.voice });
    setText('');
    setThinking(true);
    setTimeout(() => threadEnd.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }), 60);
    const m = await api.ask(q, { caseId: openCases[0]?.id });
    setThinking(false);
    reply(m, !!opts.voice);
  };
  // The answer has passed the guardrails before it arrives; it types itself out, then its buttons appear. Asked by
  // voice, Penjaga also says it (the words on screen are exactly what it says).
  const reply = (a: ChatMessage, aloud = false) => {
    if (aloud && voicePrefs().speak) void speak(a.text);
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
  const activity: Activity = hearing ? 'listening' : speaker.speaking ? 'speaking' : thinking ? 'thinking' : 'idle';
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
    <div className={`agent ${collapsed ? 'is-collapsed' : ''} ${rest === 'on' ? 'is-resting' : ''}`}>
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
      <section className={`agent-col ${collapsed ? 'collapsed' : ''} ${glass.layers ? 'glass' : ''}`} data-liquid-glass={glass.layers ? glass.mode : undefined} aria-label="Penjaga">
        {glass.layers}
        <header className="agent-head">
          {/* Penjaga, alive: it looks at the pointer, listens and talks here. Tap it to talk (or to stop it talking). */}
          <button className="pj-live" data-active={activity !== 'idle' && activity !== 'thinking' ? activity : undefined} onClick={() => (speaker.speaking ? speech.cancel() : void talk())}
            aria-label={speaker.speaking ? 'Stop Penjaga talking' : hearing ? 'Stop listening' : canListen() ? 'Talk to Penjaga' : 'Penjaga'}>
            <LiveCharacter mood={headMood} activity={activity} size={30} voice={speech.level} mic={getMic} track />
          </button>
          <div className="agent-title">
            <b>Penjaga</b>
            <span className="sep" aria-hidden="true">|</span>
            {speaker.speaking
              ? <span className="speaking-tag" role="status">Speaking<button onClick={() => speech.cancel()}>Stop</button></span>
              : speaker.blocked && lastSpoken.current
                ? <button className="hear-btn" style={{ marginTop: 0 }} onClick={() => { speech.unlock(); void speak(lastSpoken.current); }}><Icon name="replay" size={13} />Tap to hear Penjaga</button>
                : <button className="listening" onClick={() => setSensorsOpen((o) => !o)} aria-expanded={sensorsOpen} aria-controls="sensors"><i className={status} />Listening · water {live.water_lpm.toFixed(2)} L/min · power {live.power_kw.toFixed(1)} kW<Icon name="chevron" size={12} /></button>}
          </div>
          <button className="icon-btn fold" onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed} aria-label={collapsed ? 'Open the conversation' : 'Fold the conversation'}><Icon name="chevron" size={16} /></button>
        </header>
        {sensorsOpen && <Sensors live={live} />}

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
              const cont = n > 0 && thread[n - 1].who === i.who && !i.kicker && i.key !== liveKey; // same speaker again: no second avatar
              return (
                <article key={i.key} id={i.key} className={`a-item ${i.who} ${i.tone ?? ''} ${cont ? 'cont' : ''} ${i.still ? 'still' : ''}`}>
                  <span className={`a-avatar ${i.who === 'penjaga' ? 'pj' : 'me'}`} aria-hidden="true">{i.who === 'penjaga' ? <Character mood={moodFor(i.tone)} size={28} live={i.key === preview?.key} /> : 'A'}</span>
                  <div className="a-body">
                    {!cont && <div className="a-meta"><b className="a-name">{i.who === 'penjaga' ? 'Penjaga' : 'You'}</b>{i.kicker && <span className="a-kicker">{i.kicker}</span>}{i.key === liveKey && <span className="live3d"><i aria-hidden="true" />Live in 3D</span>}</div>}
                    {i.text && <div className="a-text">{i.text}</div>}
                    {i.card}
                    {i.chips && i.chips.length > 0 && (
                      <div className="a-chips">
                        {i.chips.map((ch) => (
                          <button key={ch.label} className={`a-chip ${ch.primary ? 'primary' : ''} ${ch.quiet ? 'quiet' : ''} ${ch.flip ? 'flip' : ''} ${ch.tone ?? ''}`} onClick={ch.onClick} aria-expanded={ch.flip !== undefined && ch.icon === 'chevron' ? ch.flip : undefined}>
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
            {thinking && (
              <div className="a-item penjaga cont thinking-row" role="status">
                <span className="a-avatar pj" aria-hidden="true" />
                <div className="a-body"><MiniOrb size={24} />Penjaga is thinking…</div>
              </div>
            )}
            {hearing && (
              // the owner's words as they speak, in their own bubble at the bottom of the conversation
              <article className="a-item you hearing" aria-label="What you're saying">
                <span className="a-avatar me mic" data-mic aria-hidden="true"><MicIcon size={14} /></span>
                <div className="a-body">
                  <div className="a-meta"><b className="a-name">You</b><span className="a-kicker">Listening</span></div>
                  <div className="a-text"><LiveWords text={hearing.text} settled={hearing.settled} listening /></div>
                </div>
              </article>
            )}
            <div ref={threadEnd} />
          </div>
        )}

        <div className="agent-compose">
          {!collapsed && <div className="suggest">{suggestions.map((s) => <button type="button" key={s} onClick={() => void ask(s)} disabled={thinking}>{s}</button>)}</div>}
          {/* the ask pill: a ribbon of colour runs round its rim (faster while typing), a soft glow under it */}
          <div className="ask-pill" data-typing={text.trim() ? '' : undefined} data-listening={hearing ? '' : undefined}>
            <div className="ask-glow" aria-hidden="true" />
            <div className="ask-surface" aria-hidden="true"><div className="ask-aurora"><i /><i /><i /><i /></div></div>
            <form className="ask-input" onSubmit={(e) => { e.preventDefault(); void ask(text); }} autoComplete="off">
              <svg className="ask-spark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M10 3.5l1.7 4.8 4.8 1.7-4.8 1.7L10 16.5l-1.7-4.8L3.5 10l4.8-1.7L10 3.5z" />
                <path d="M18 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z" />
              </svg>
              {hearing
                ? <ListeningLine level={getMic} hint={hearing.text ? 'Listening… tap the mic when you’re done' : 'Listening… say your question'} />
                : <input value={text} onChange={(e) => { setText(e.target.value); if (speaker.speaking) speech.cancel(); }} onFocus={() => { if (collapsed) setCollapsed(false); }} maxLength={300} placeholder={canListen() ? 'Ask Penjaga, or tap the mic' : 'Ask Penjaga about your home'} aria-label="Ask Penjaga" spellCheck={false} />}
              {canListen() && !text.trim() && (
                <button type="button" className="ask-mic" data-mic aria-pressed={!!hearing} aria-label={hearing ? 'Stop listening' : 'Talk to Penjaga'} disabled={thinking && !hearing} onClick={() => void talk()}>
                  <MicIcon />
                </button>
              )}
              {(!canListen() || text.trim()) && <button type="submit" className="ask-send" aria-label="Send" disabled={thinking} data-ready={text.trim() && !thinking ? '' : undefined}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 19V5" />
                  <path d="M5.5 11.5L12 5l6.5 6.5" />
                </svg>
              </button>}
            </form>
          </div>
          {!collapsed && <div className="compose-note">{hearing ? 'Your microphone is on only while you see this' : 'Penjaga acts only after you confirm'}</div>}
        </div>
      </section>

      {rest !== 'off' && (
        <Resting
          status={status}
          line={status === 'all_quiet' ? 'All quiet at home' : status === 'urgent' ? 'Something needs you now' : openCases.length === 1 ? 'One thing needs a look' : `${openCases.length} things need a look`}
          houseNow={houseNow}
          leaving={rest === 'leaving'}
          onWake={wake}
        />
      )}

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
    walk: hud.room ? `Going to the ${hud.room.toLowerCase()}` : 'Going there',
    look: hud.room ? `In the ${hud.room.toLowerCase()}` : 'Here',
    xray: "Showing what's inside the wall",
    found: `Found: ${hud.title}`,
    done: 'Picture saved in the chat',
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

// What Penjaga listens to: the house's own sensors, live. Nothing in the home watches people (no cameras, no microphones);
// the phone's microphone is used only while the owner talks to Penjaga.
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
      <p className="tiny muted">Penjaga senses the building, never the people in it: no cameras or microphones in your home. Your phone's microphone is on only while you talk to Penjaga.</p>
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

