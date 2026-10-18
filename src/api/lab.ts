// The Lab on the real backend (VITE_BACKEND=api): faults go to the simulator (M2), notices and job completion to
// the Core API's operator routes, and "what detection sees" comes from detection (M5). All through the dev proxy,
// which adds the operator token (vite.config.js). Ground truth stays in the simulator; the homeowner app still
// learns about a fault only through detection on readings.

export interface SimFault {
  fault_id: string;
  spec: { fault_type: string; location_id?: string | null };
  state: string;
  start: string;
  end: string | null;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (r.status === 204 ? undefined : await r.json()) as T;
}

// The Lab's scenarios, as simulator faults (catalogue: GET /sim/v1/faults/catalogue).
const SCENARIOS = {
  cistern_leak: (location: string) => ({ fault_type: 'W2', location_id: location, magnitude: { value: 0.5, unit: 'L/min' }, progression: { kind: 'constant' } }),
  earth_leakage: (location: string) => ({ fault_type: 'E2', location_id: location, magnitude: { value: 1.5, unit: 'mA' }, progression: { kind: 'constant' } }),
  supply_drop: () => ({ fault_type: 'U2', location_id: null, magnitude: { value: 1, unit: '1' }, progression: { kind: 'constant' }, duration_s: 6 * 3600 }),
} as const;
export type Scenario = keyof typeof SCENARIOS;
const TYPE: Record<Scenario, string> = { cistern_leak: 'W2', earth_leakage: 'E2', supply_drop: 'U2' };

export const faults = () => call<SimFault[]>('GET', '/lab/sim/v1/faults');

export async function inject(scenario: Scenario, location = ''): Promise<void> {
  await clear(scenario); // one fault of each kind at a time, as in the stand-in Lab
  await call('POST', '/lab/sim/v1/faults', SCENARIOS[scenario](location));
}

export async function clear(scenario: Scenario): Promise<void> {
  const live = (await faults()).filter((f) => f.spec.fault_type === TYPE[scenario] && f.state !== 'cleared');
  await Promise.all(live.map((f) => call('DELETE', `/lab/sim/v1/faults/${f.fault_id}`)));
}

/** A scheduled water disruption notice for this street, started half an hour ago (adapted from a past notice). */
export const postNotice = () => call('POST', '/lab/api/v1/notices/seed', { preset: 'water_works', starts_in_h: -0.5 });

/** Withdraw every published notice. */
export async function clearNotices(): Promise<void> {
  const list = await call<{ id: string; status: string }[]>('GET', '/lab/mgmt/v1/manage/notices');
  await Promise.all(list.filter((n) => n.status === 'published').map((n) => call('POST', `/lab/mgmt/v1/manage/notices/${n.id}/withdraw`)));
}

/** The vendor finishes every open booking on cases of this kind (plumber: water; electrician: electrical). */
export async function completeJobs(trade: 'plumber' | 'electrician'): Promise<number> {
  const layer = trade === 'plumber' ? 'L1' : 'L2';
  const cases = await call<{ layer: string; booking?: { id?: string; status: string } }[]>('GET', '/lab/api/v1/cases');
  const open = cases.filter((c) => c.layer === layer && c.booking?.id && c.booking.status !== 'completed');
  await Promise.all(open.map((c) => call('POST', `/lab/api/v1/bookings/${c.booking!.id}/complete`)));
  return open.length;
}

export const findings = () => call<unknown[]>('GET', '/lab/detect/v1/findings');
