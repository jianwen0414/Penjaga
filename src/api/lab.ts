// The Lab on the real backend (VITE_BACKEND=api): faults go to the simulator (M2), notices and job completion to
// the Core API's operator routes, and "what detection sees" comes from detection (M5). All through the dev proxy,
// which adds the operator and management tokens (vite.config.js). Ground truth stays in the simulator; the
// homeowner app still learns about a fault only through detection on readings.

export interface SimFault {
  fault_id: string;
  spec: { fault_type: string; location_id?: string | null };
  state: string;
  start: string;
  end: string | null;
}

export interface Scenario {
  type: string; // the simulator's fault type (GET /sim/v1/faults/catalogue)
  label: string;
  group: 'Water' | 'Electrical safety' | 'Appliances' | 'Supply';
  magnitude: number;
  unit: string;
  where: [string, string][]; // [contract ID, name]; empty for supply-level faults
  durationH?: number; // supply faults end on their own
  when: string; // how long until detection is sure, in house time
}

// Places are chosen to exist in the 3D model where possible, so "Show in the house" lights them up.
export const SCENARIOS: Scenario[] = [
  { type: 'W2', group: 'Water', label: 'Running toilet cistern', magnitude: 0.5, unit: 'L/min', when: 'after 2 nights',
    where: [['fix_bath2_wc_cistern', 'Bathroom 2'], ['fix_bath_master_wc_cistern', 'Master bath'], ['fix_bath3_wc_cistern', 'Bathroom 3'], ['fix_bath4_wc_cistern', 'Bathroom 4']] },
  { type: 'W1', group: 'Water', label: 'Dripping tap', magnitude: 0.06, unit: 'L/min', when: 'after 2 nights',
    where: [['fix_bath2_basin_tap', 'Bathroom 2 basin'], ['fix_bath2_shower', 'Bathroom 2 shower'], ['fix_kitchen_sink_tap', 'Kitchen sink']] },
  { type: 'W4', group: 'Water', label: 'Hidden pipe leak (in a wall)', magnitude: 0.8, unit: 'L/min', when: 'after 2 nights',
    where: [['pipe_bath2_cold_supply', 'Bathroom 2 supply pipe'], ['pipe_kitchen_direct', 'Kitchen pipe']] },
  { type: 'W3', group: 'Water', label: 'Roof tank overflowing', magnitude: 6, unit: 'L/min', when: 'within hours',
    where: [['valve_roof_tank_float', 'Roof tank float valve']] },
  { type: 'W5', group: 'Water', label: 'Burst pipe (urgent)', magnitude: 15, unit: 'L/min', when: 'within an hour',
    where: [['pipe_bath2_cold_supply', 'Bathroom 2 supply pipe'], ['pipe_rising_main', 'Rising main']] },
  { type: 'E2', group: 'Electrical safety', label: 'Earth leakage rising', magnitude: 1.5, unit: 'mA', when: 'within a day',
    where: [['circ_sockets_living', 'Living room sockets'], ['circ_sockets_kitchen', 'Kitchen sockets'], ['circ_sockets_yard', 'Yard sockets']] },
  { type: 'E1', group: 'Electrical safety', label: 'Circuit overloaded', magnitude: 25, unit: 'A', when: 'within hours',
    where: [['circ_sockets_kitchen', 'Kitchen sockets'], ['circ_sockets_living', 'Living room sockets']] },
  { type: 'E3', group: 'Electrical safety', label: 'Breaker keeps tripping (urgent)', magnitude: 4, unit: '1/day', when: 'within a day',
    where: [['circ_sockets_kitchen', 'Kitchen sockets'], ['circ_sockets_living', 'Living room sockets']] },
  { type: 'A1', group: 'Appliances', label: 'Aircon filter clogging', magnitude: 3, unit: '%/week', when: 'after some days',
    where: [['app_ac_living', 'Living room aircon'], ['app_ac_master', 'Master bedroom aircon'], ['app_ac_bed2', 'Bedroom 2 aircon']] },
  { type: 'A2', group: 'Appliances', label: 'Aircon low on refrigerant', magnitude: 25, unit: '%', when: 'after some days',
    where: [['app_ac_living', 'Living room aircon'], ['app_ac_master', 'Master bedroom aircon']] },
  { type: 'A3', group: 'Appliances', label: 'Fridge door seal leaking', magnitude: 0.3, unit: '1', when: 'after some days',
    where: [['app_fridge_kitchen', 'Fridge']] },
  { type: 'A4', group: 'Appliances', label: 'Fridge compressor wearing out', magnitude: 20, unit: '%', when: 'after some days',
    where: [['app_fridge_kitchen', 'Fridge']] },
  { type: 'U2', group: 'Supply', label: 'Water supply cut (6 hours)', magnitude: 1, unit: '1', durationH: 6, when: 'within minutes', where: [] },
  { type: 'U3', group: 'Supply', label: 'Power cut (3 hours)', magnitude: 1, unit: '1', durationH: 3, when: 'within minutes', where: [] },
];

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (r.status === 204 ? undefined : await r.json()) as T;
}

export const faults = () => call<SimFault[]>('GET', '/lab/sim/v1/faults');
export const isLive = (f: SimFault) => f.state !== 'cleared' && f.state !== 'ended';

/** Start a fault; one of each kind at a time, so a second press moves it. */
export async function inject(s: Scenario, location: string | null): Promise<void> {
  await clearType(s.type);
  await call('POST', '/lab/sim/v1/faults', {
    fault_type: s.type,
    location_id: location,
    magnitude: { value: s.magnitude, unit: s.unit },
    progression: { kind: 'constant' },
    ...(s.durationH ? { duration_s: s.durationH * 3600 } : {}),
  });
}

export const clearFault = (id: string) => call('DELETE', `/lab/sim/v1/faults/${id}`);

export async function clearType(type: string): Promise<void> {
  const live = (await faults()).filter((f) => f.spec.fault_type === type && isLive(f));
  await Promise.all(live.map((f) => clearFault(f.fault_id)));
}

export async function clearAll(): Promise<void> {
  const live = (await faults()).filter(isLive);
  await Promise.all(live.map((f) => clearFault(f.fault_id)));
}

/** A notice for this street from the Core API's presets, started half an hour ago. */
export const postNotice = (preset: 'water_works' | 'power_outage') =>
  call('POST', '/lab/api/v1/notices/seed', { preset, starts_in_h: -0.5 });

/** Withdraw every published notice (as management). */
export async function clearNotices(): Promise<void> {
  const list = await call<{ id: string; status: string }[]>('GET', '/lab/mgmt/v1/manage/notices');
  await Promise.all(list.filter((n) => n.status === 'published').map((n) => call('POST', `/lab/mgmt/v1/manage/notices/${n.id}/withdraw`)));
}

/** The vendor finishes every open booking on cases of a layer (L1 plumber, L2 electrician, L3 aircon service). */
export async function completeJobs(layer: 'L1' | 'L2' | 'L3'): Promise<number> {
  const cases = await call<{ layer: string; booking?: { id?: string; status: string } }[]>('GET', '/lab/api/v1/cases');
  const open = cases.filter((c) => c.layer === layer && c.booking?.id && c.booking.status !== 'completed');
  await Promise.all(open.map((c) => call('POST', `/lab/api/v1/bookings/${c.booking!.id}/complete`)));
  return open.length;
}

export const findings = () => call<{ type: string; severity: string; status: string; object_ids: string[] }[]>('GET', '/lab/detect/v1/findings');

// ---------------- normal life: things a household does that must NOT raise an alarm ----------------
// The simulator applies these to whole house days, from the next house midnight (POST /sim/v1/anomalies).

export interface NormalEvent { type: string; label: string; days: number; expect: string }
export const NORMAL_EVENTS: NormalEvent[] = [
  { type: 'guests', label: 'Guests stay over', days: 3, expect: 'more water and power, no leak alarm' },
  { type: 'holiday_absence', label: 'Family away on holiday', days: 4, expect: 'the house goes quiet, no alarm' },
  { type: 'festive_cooking', label: 'Festive cooking (open house)', days: 2, expect: 'heavy kitchen load, no overload alarm' },
  { type: 'night_bucket_fill', label: 'Filling buckets at night', days: 3, expect: 'night water use, no leak alarm' },
  { type: 'heatwave', label: 'Heatwave', days: 4, expect: 'aircons work harder, no fault alarm' },
];

interface SimStatus { published_until: string | null }

/** Start a normal-life event from the next house midnight (the simulator only changes days not yet sent). */
export async function startEvent(e: NormalEvent): Promise<string> {
  const { published_until } = await call<SimStatus>('GET', '/lab/sim/v1/status');
  if (!published_until) throw new Error('the simulator has not started');
  const day = new Date(Date.parse(published_until) + 8 * 3600_000); // house days are Malaysian days (+08:00)
  const next = `${new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate() + 1)).toISOString().slice(0, 10)}T00:00:00+08:00`;
  await call('POST', '/lab/sim/v1/anomalies', { anomaly_type: e.type, start_at: next, duration_s: e.days * 86400 });
  return `${e.label}: from the next house midnight, for ${e.days} days. Penjaga should stay quiet (${e.expect}).`;
}

export interface TruthRecord { kind: 'fault' | 'legit_anomaly'; label: string; start: string; end: string | null }
/** What the simulator really did, up to the house time already sent (ground truth, operator only). */
export const truth = () => call<TruthRecord[]>('GET', '/lab/sim/v1/truth');

// ---------------- how well detection works: Jianwen's evaluation (M11) ----------------

export interface FaultTypeScore { n: number; recall: number | null; ttd_hours_median?: number | null }
export interface LayerScore {
  recall: number | null; recall_located: number | null; precision: number | null;
  false_alarms_per_house_month: number | null; faults: number; detected: number;
  by_fault_type?: Record<string, FaultTypeScore> | null;
}
export interface EvalRun {
  run_id: string; suite: string; split: 'validation' | 'test'; status: string; created_at: string; finished_at?: string | null;
  episodes?: number | null; detectors: string[];
  results?: Record<string, { version: string; layers: Record<string, LayerScore> }> | null;
}
export const evalRuns = () => call<EvalRun[]>('GET', '/lab/eval/v1/runs');
