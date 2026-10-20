// The real backend (VITE_BACKEND=api): the Core API's /api/v1, reached through the Vite dev proxy, which adds the
// homeowner token server-side so the browser never holds one. Same shapes as the stand-in (src/api/types.ts).
//
// The view is a small store for useSyncExternalStore: loaded from GET /api/v1/view, refreshed when the Core API
// publishes a change on its event stream (GET /api/v1/events, SSE) and every REFRESH_MS (today's usage moves on
// its own), and kept on screen while the backend is unreachable, with a notice saying so.
import type { ChatMessage, Notice, Vendor, VendorCategory } from './types';
import type { View } from '../standin/agent';
import { toSpecFocus } from './spec-ids';

const BASE = '/api/v1';
const REFRESH_MS = 30_000;
const RETRY_MS = [1_000, 2_000, 5_000, 10_000, 30_000];
const TOURED_KEY = 'penjaga.toured'; // remembered per browser, so the first render already knows it
const CATEGORIES: VendorCategory[] = ['plumber', 'electrician', 'aircon_service'];

const rememberedTour = (): boolean => {
  try { return localStorage.getItem(TOURED_KEY) === '1'; } catch { return false; }
};
const rememberTour = (on: boolean) => {
  try { if (on) localStorage.setItem(TOURED_KEY, '1'); } catch { /* storage unavailable */ }
};

function statusNotice(id: string, title: string, body: string): Notice {
  const now = Date.now();
  return { id, type: 'general', title, issuer: 'Penjaga', area: 'This home', starts_at: now, ends_at: now + 3600e3, body };
}
const CONNECTING = statusNotice('penjaga-connecting', 'Connecting to your Penjaga hub', 'Penjaga is loading your home. This takes a moment.');
const OFFLINE = statusNotice('penjaga-offline', "Can't reach your Penjaga hub", "Showing what Penjaga last knew. It reconnects on its own; nothing is lost while it's away.");

let view: View = {
  cases: [],
  notices: [CONNECTING],
  wellbeing: null,
  home: { status: 'all_quiet', checked_at: 0, today: { water_l: 0, water_typical_l: 0, water_series: [], elec_kwh: 0, elec_typical_kwh: 0, elec_series: [] } },
  maintenance: [],
  passport: [],
  consent: { L1: true, L2: true, L3: true, L4: true, L5: true },
  toured: rememberedTour(),
};
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

let names = new Map<string, string>(); // contract ID → name, from GET /api/v1/house
let consentVersions: Record<string, string> = {};
const vendorCache = new Map<VendorCategory, Vendor[]>();
let started = false;
let failures = 0;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${await r.text()}`);
  return (r.status === 204 ? undefined : await r.json()) as T;
}

// The Core API has no "typical" until the house has a day of history (null). The screens treat 0 as "no comparison".
function adapt(v: View): View {
  const t = v.home.today as unknown as Record<string, unknown>;
  const today = { ...v.home.today, water_typical_l: Number(t.water_typical_l ?? 0), elec_typical_kwh: Number(t.elec_typical_kwh ?? 0) };
  // 3D focus: the house record's IDs become the 3D model's (src/api/spec-ids.ts).
  const cases = v.cases.map((c) => ({
    ...c,
    focus: toSpecFocus(c.focus),
    actions: c.actions.map((a) => (a.focus ? { ...a, focus: toSpecFocus(a.focus) } : a)),
  }));
  return { ...v, cases, home: { ...v.home, today } };
}

async function load(): Promise<void> {
  clearTimeout(refreshTimer);
  try {
    const next = adapt(await call<View>('GET', '/view'));
    if (next.home.checked_at) houseMs = Math.max(houseMs ?? 0, next.home.checked_at);
    if (next.toured) rememberTour(true);
    view = next;
    failures = 0;
    void loadVendors().catch(() => { /* keep the last slots */ });
    emit();
    refreshTimer = setTimeout(load, REFRESH_MS);
  } catch (err) {
    console.warn('[penjaga] view unavailable', err);
    view = { ...view, notices: [OFFLINE, ...view.notices.filter((n) => n.id !== OFFLINE.id && n.id !== CONNECTING.id)] };
    emit();
    refreshTimer = setTimeout(load, RETRY_MS[Math.min(failures++, RETRY_MS.length - 1)]);
  }
}

let soon: ReturnType<typeof setTimeout> | undefined;
const loadSoon = () => { clearTimeout(soon); soon = setTimeout(load, 250); };

// Vendors' visit slots are relative to house time, which runs fast in the demo: they are refreshed with every
// view load so the times on screen are still on offer when the owner picks one.
async function loadVendors(): Promise<void> {
  for (const category of CATEGORIES) {
    const doc = await call<{ vendors: Vendor[] }>('GET', `/vendors?category=${category}`);
    vendorCache.set(category, doc.vendors);
  }
}

async function loadReference(): Promise<void> {
  try {
    const house = await call<Record<string, unknown>>('GET', '/house');
    const found = new Map<string, string>();
    const walk = (node: unknown) => {
      if (Array.isArray(node)) node.forEach(walk);
      else if (node && typeof node === 'object') {
        const o = node as Record<string, unknown>;
        if (typeof o.id === 'string' && typeof o.name === 'string') found.set(o.id, o.name);
        Object.values(o).forEach(walk);
      }
    };
    walk(house);
    names = found;
    const consent = await call<{ layer: string; text_version: string }[]>('GET', '/consent');
    consentVersions = Object.fromEntries(consent.map((c) => [c.layer, c.text_version]));
    await loadVendors();
    emit();
  } catch (err) {
    console.warn('[penjaga] reference data unavailable; retrying', err);
    setTimeout(loadReference, 10_000);
  }
}

function start(): void {
  if (started || typeof window === 'undefined') return;
  started = true;
  void load();
  void loadReference();
  // EventSource reconnects by itself and resumes with Last-Event-ID. Any change (a case, consent, a new run)
  // just reloads the view, which is the single source of truth.
  const events = new EventSource(`${BASE}/events`);
  for (const kind of ['case', 'consent', 'episode']) events.addEventListener(kind, loadSoon);
  events.onopen = loadSoon;
}

export function subscribe(fn: () => void): () => void {
  start();
  listeners.add(fn);
  return () => listeners.delete(fn);
}
export const snapshot = (): View => view;

// ---------------- actions ----------------

export function dismiss(caseId: string, reason: string): void {
  call('POST', `/cases/${encodeURIComponent(caseId)}/dismiss`, { reason })
    .catch((err) => console.warn('[penjaga] dismiss refused', err)) // urgent and safety cases can't be dismissed
    .finally(loadSoon);
}

export function setConsent(layer: string, on: boolean): void {
  const before = view.consent;
  view = { ...view, consent: { ...view.consent, [layer]: on } };
  emit();
  const decide = async () => {
    if (!consentVersions[layer]) await loadReference();
    await call('PUT', `/consent/${layer}`, { enabled: on, text_version: consentVersions[layer] ?? 'v1' });
  };
  decide()
    .catch((err) => { console.warn('[penjaga] consent not saved', err); view = { ...view, consent: before }; emit(); })
    .finally(loadSoon);
}

export function markToured(): void {
  rememberTour(true);
  view = { ...view, toured: true };
  emit();
  call('POST', '/toured').catch((err) => console.warn('[penjaga] tour flag not saved', err));
}

/** The contract's name for an element, or undefined if the ID isn't a contract ID (the 3D spec uses its own). */
export const contractName = (id: string): string | undefined => names.get(id);

/** Vendors from the Core API's directory (prefetched; undefined until loaded). */
export const cachedVendors = (category: VendorCategory): Vendor[] | undefined => vendorCache.get(category);

/** The house's sensors right now (GET /api/v1/live); null when a sensor sent nothing recently. */
export interface LiveReadings {
  water_lpm: number | null; power_kw: number | null;
  pressure_bar?: number | null; tank_pct?: number | null; fridge_w?: number | null; outdoor_c?: number | null;
}
export async function live(): Promise<LiveReadings> {
  const r = await call<LiveReadings & { at: number }>('GET', '/live');
  if (r.at) houseMs = r.at;
  return r;
}

// The house's own clock (the simulator's house time in the demo), as last heard from the Core API: the view's
// checked_at and the live line's reading time (polled every few seconds). Screens use it instead of the computer's
// clock, so "today", "ended" and the order of messages follow the house.
let houseMs: number | undefined;
export const houseNow = (): number => houseMs ?? Date.now();

// ---------------- confirmed actions (step 7) ----------------
// The screens show the owner exactly what goes out before they tap (the management draft, the booking summary,
// the valve to close), so a tap is the confirmation: preview, then confirm with the single-use token.

async function confirmed(caseId: string, kind: 'ticket' | 'booking' | 'check', params: Record<string, unknown>, path: string) {
  const { confirmation } = await call<{ confirmation: string }>('POST', `/cases/${encodeURIComponent(caseId)}/actions/${kind}/preview`, { params });
  return call('POST', `/cases/${encodeURIComponent(caseId)}/${path}`, { ...params, confirmation });
}

export function sendToManagement(caseId: string, note: string): void {
  confirmed(caseId, 'ticket', { note, contact: 'in_app' }, 'tickets')
    .catch((err) => console.warn('[penjaga] report not sent', err))
    .finally(loadSoon);
}

export function book(caseId: string, vendorId: string, slot: number): void {
  confirmed(caseId, 'booking', { vendor_id: vendorId, slot }, 'bookings')
    .catch(async (err) => {
      // The slot can pass in house time between showing it and the tap: take the vendor's nearest slot from then on.
      if (!String(err).includes('offered slots')) throw err;
      await loadVendors();
      const vendor = [...vendorCache.values()].flat().find((v) => v.id === vendorId);
      const next = vendor?.next_slots.find((t) => t >= slot) ?? vendor?.next_slots[0];
      if (next === undefined) throw err;
      return confirmed(caseId, 'booking', { vendor_id: vendorId, slot: next }, 'bookings');
    })
    .catch((err) => console.warn('[penjaga] booking not made', err))
    .finally(loadSoon);
}

/** The owner did a maintenance task: it moves to its next date and goes into the Home Passport. */
export function markMaintenanceDone(itemId: string): void {
  call('POST', `/maintenance/${encodeURIComponent(itemId)}/done`)
    .catch((err) => console.warn('[penjaga] reminder not marked done', err))
    .finally(loadSoon);
}

/** Starts a guided check; the step's result arrives with the view once the meter has been watched. */
export function runGuidedCheck(caseId: string, fixtureId: string): void {
  const step = view.cases.find((c) => c.id === caseId)?.guided?.steps.find((s) => s.fixture_id === fixtureId);
  confirmed(caseId, 'check', { fixture_id: fixtureId }, 'checks')
    .then(() => (step?.circuit_id ? actInSimulator({ switch_off_circuits: [step.circuit_id] }) : step?.valve_id ? actInSimulator({ close_valves: [step.valve_id] }) : undefined))
    .catch((err) => console.warn('[penjaga] check not started', err))
    .finally(loadSoon);
}

// Demo bridge (dev server only): there is no real owner to turn the valve or flip the breaker, so when the owner
// confirms a check the simulated house does it, through the Lab's operator route (POST /sim/v1/actions via the dev
// proxy). It lasts longer than the check (house time). The Core API itself never calls
// the simulator; in a real home the owner does it by hand.
const ACTION_HELD_S = 40 * 60; // longer than a valve check (20 min settle + 10 min watch)
async function actInSimulator(action: { close_valves?: string[]; switch_off_circuits?: string[] }, heldS = ACTION_HELD_S): Promise<void> {
  const r = await fetch('/lab/sim/v1/actions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...action, duration_s: heldS }),
  });
  if (!r.ok) console.warn('[penjaga] the simulator did not do it', action, r.status, await r.text());
}

/** The same demo bridge for an emergency: the owner says they closed the main stop valve, so the simulated house
 * closes it (until a plumber would have come: 6 house hours), and the meter shows whether it worked. */
export function ownerDid(action: { close_valves?: string[]; switch_off_circuits?: string[] }): void {
  actInSimulator(action, 6 * 3600).catch((err) => console.warn('[penjaga] the simulator did not do it', err));
}

// ---------------- "Ask Penjaga" (step 6) ----------------
// POST /api/v1/chat answers over SSE: a "thinking" comment, `delta` events with the text, then `done` with the whole
// message (buttons included). The screen shows a thinking line until it resolves; it never rejects, so a lost
// connection reads as a plain sentence instead of an error.

let conversationId: string | undefined;

export async function ask(text: string, ctx: { caseId?: string } = {}): Promise<ChatMessage> {
  const fallback = (t: string): ChatMessage => ({ id: `m${Date.now()}`, role: 'penjaga', text: t, t: Date.now() });
  try {
    const r = await fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, conversation_id: conversationId, case_id: ctx.caseId }),
    });
    if (!r.ok) return fallback("I couldn't answer that just now. Try again in a moment.");
    const raw = await r.text();
    const done = raw.split('\n\n').find((block) => block.startsWith('event: done'));
    const data = done?.split('\n').find((line) => line.startsWith('data: '))?.slice(6);
    if (!data) return fallback("I couldn't answer that just now. Try again in a moment.");
    const reply = JSON.parse(data) as { conversation_id: string; message: ChatMessage };
    conversationId = reply.conversation_id;
    const chips = reply.message.chips?.map((c) => (c.focus ? { ...c, focus: toSpecFocus(c.focus) } : c));
    return { ...reply.message, chips };
  } catch {
    return fallback("I can't reach your home's service right now. Your cases are still shown; try again in a moment.");
  }
}
