// Stand-in backend state. The *world* (faults, notices) is what the Penjaga Lab controls; the homeowner's
// actions (guided checks, bookings, dismissals) are recorded alongside. Persisted in localStorage and synced
// between tabs (the Lab runs in its own tab, like the real operator surface).
// Ground truth (faults) is only read by sim.ts; detection sees readings, never this object.

export type FaultType = 'cistern_leak' | 'earth_leakage' | 'supply_drop' | 'quiet_suite';

export interface Fault {
  id: string;
  type: FaultType;
  location?: string; // contract ID (simulator-side ground truth)
  started_at: number;
  cleared_at?: number;
}

export interface PostedNotice {
  id: string;
  type: 'water_disruption';
  title: string;
  area: string;
  starts_at: number;
  ends_at: number;
  body: string;
  posted_at: number;
}

export interface CheckRecord { case_key: string; fixture_id: string; at: number; flow_before: number; flow_after: number }
export interface BookingRecord { case_key: string; vendor_id: string; slot: number; at: number; status: 'requested' | 'acknowledged' | 'completed'; ack_at?: number; done_at?: number }
export interface TicketRecord { case_key: string; at: number; note: string }

export interface World {
  version: 2;
  faults: Fault[];
  notices: PostedNotice[];
  checks: CheckRecord[];
  bookings: BookingRecord[];
  tickets: TicketRecord[];
  dismissed: { case_key: string; at: number; reason: string }[];
  wellbeingAck: { id: string; at: number; note: string }[];
  consent: Record<'L1' | 'L2' | 'L3' | 'L4' | 'L5', boolean>;
  toured: boolean;
}

const KEY = 'penjaga.standin.world';
const fresh = (): World => ({
  version: 2, faults: [], notices: [], checks: [], bookings: [], tickets: [], dismissed: [], wellbeingAck: [],
  consent: { L1: true, L2: true, L3: true, L4: true, L5: true }, toured: false,
});

function load(): World {
  try {
    const w = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    if (w?.version === 2) return w;
  } catch { /* storage unavailable: start fresh */ }
  return fresh();
}

let world: World = load();
const subs = new Set<() => void>();
let channel: BroadcastChannel | null = null;
try { channel = new BroadcastChannel('penjaga-standin'); } catch { channel = null; }
channel?.addEventListener('message', () => { world = load(); subs.forEach((f) => f()); });

export function getWorld(): World { return world; }
export function subscribe(f: () => void) { subs.add(f); return () => { subs.delete(f); }; }
export function update(mut: (w: World) => void) {
  const next: World = structuredClone(world);
  mut(next);
  world = next;
  try { localStorage.setItem(KEY, JSON.stringify(world)); } catch { /* ignore */ }
  channel?.postMessage('changed');
  subs.forEach((f) => f());
}
export function resetWorld(keepTour = true) {
  update((w) => { const t = w.toured; Object.assign(w, fresh()); if (keepTour) w.toured = t; });
}

// A steady clock tick so time-based state (vendor acknowledgement, notice windows) moves on its own.
setInterval(() => subs.forEach((f) => f()), 5000);
