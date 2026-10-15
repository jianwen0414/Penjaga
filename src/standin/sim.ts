// House simulator (stand-in for M2). Produces readings for one household — Aiman, Mei Ling, their child and
// Aiman's mother in the ground-floor suite — plus whatever faults the Lab has injected.
// This is the ONLY module that reads fault ground truth. Detection (detect.ts) sees readings only.
// Magnitudes are representative (a running cistern ≈ 0.1–0.2 L/min; household ≈ 210 L/person/day) and
// will be replaced by the calibrated simulator from the Data + ML workstream.
import { getWorld, type World, type Fault } from './world';

const HOUR = 3600e3, DAY = 24 * HOUR;

function rng(seed: number) {
  let a = seed >>> 0;
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
const dayKey = (t: number) => Math.floor(t / DAY);

// Litres per hour-of-day for this household (≈ 840 L/day, 4 people). Pre-dawn ablutions at 5–6am are normal.
const WATER_PROFILE = [2, 0, 0, 0, 0, 25, 90, 80, 30, 15, 15, 20, 25, 15, 10, 15, 25, 40, 70, 85, 60, 40, 20, 8].map((v) => v * 1.217);
// kWh per hour-of-day (aircon in bedrooms overnight is normal in Malaysia).
const ELEC_PROFILE = [1.1, 1.1, 1.0, 1.0, 1.0, 0.9, 0.8, 0.9, 0.5, 0.35, 0.35, 0.4, 0.5, 0.45, 0.4, 0.4, 0.5, 0.7, 1.0, 1.2, 1.3, 1.3, 1.3, 1.2];

const active = (w: World, type: Fault['type'], t: number) =>
  w.faults.find((f) => f.type === type && f.started_at <= t && (!f.cleared_at || f.cleared_at > t));

const LEAK_LPM = 0.125; // ≈ 180 L/day

export const local = (t: number) => { const d = new Date(t); return { h: d.getHours(), m: d.getMinutes() }; };
const startOfHour = (t: number) => { const d = new Date(t); d.setMinutes(0, 0, 0); return d.getTime(); };
const startOfDay = (t: number) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };

// Water meter: average flow (L/min) for the hour starting at t.
export function waterFlowAt(t: number, w: World = getWorld()): number {
  const r = rng(Math.floor(t / HOUR) * 7919);
  const { h } = local(t);
  let lpm = (WATER_PROFILE[h] * (0.75 + 0.5 * r())) / 60;
  if (h >= 1 && h < 5) lpm = r() < 0.08 ? 0.02 : 0; // a glass of water, a toilet flush — rare
  if (active(w, 'cistern_leak', t)) lpm += LEAK_LPM * (0.9 + 0.2 * r());
  if (active(w, 'supply_drop', t)) lpm = 0; // no supply at the meter
  return +lpm.toFixed(3);
}

export function waterFlowSeries(hours: number, now = Date.now(), w: World = getWorld()) {
  const end = startOfHour(now);
  return Array.from({ length: hours }, (_, i) => { const t = end - (hours - 1 - i) * HOUR; return { t, v: waterFlowAt(t, w) }; });
}

// Minimum flow in the 1am–5am window for each of the last n nights (what a night-flow detector looks at).
export function nightMinFlow(nights: number, now = Date.now(), w: World = getWorld()) {
  const today = startOfDay(now);
  return Array.from({ length: nights }, (_, i) => {
    const night = today - (nights - 1 - i) * DAY;
    if (night + 5 * HOUR > now) return null; // tonight hasn't happened yet
    let min = Infinity;
    for (let h = 1; h < 5; h++) min = Math.min(min, waterFlowAt(night + h * HOUR, w));
    return { t: night, v: min };
  }).filter(Boolean) as { t: number; v: number }[];
}

// Flow measured right now with one fixture's isolation valve closed (guided check).
export function flowWithValveClosed(fixtureId: string, now = Date.now(), w: World = getWorld()) {
  const leak = active(w, 'cistern_leak', now);
  const r = rng(now % 100000);
  if (!leak) return +(0.004 * r()).toFixed(3);
  return leak.location === fixtureId ? +(0.003 * r()).toFixed(3) : +(LEAK_LPM * (0.95 + 0.1 * r())).toFixed(3);
}
export function flowNow(now = Date.now(), w: World = getWorld()) {
  const leak = active(w, 'cistern_leak', now);
  return leak ? +(LEAK_LPM * (0.95 + 0.1 * rng(now % 77777)())).toFixed(3) : 0;
}

// Electricity: hourly kWh for the whole house.
export function elecAt(t: number): number {
  const r = rng(Math.floor(t / HOUR) * 104729);
  return +(ELEC_PROFILE[local(t).h] * (0.8 + 0.4 * r())).toFixed(2);
}

// Weather context (stand-in for Open-Meteo history): afternoon rain on some days.
export function rainedOn(dayStart: number): boolean {
  return rng(dayKey(dayStart) * 31337)() < 0.55;
}

// RCD residual current (daily max, mA). Earth-leakage fault: grows over days and jumps on rainy days.
export function residualDaily(days: number, now = Date.now(), w: World = getWorld()) {
  const today = startOfDay(now);
  return Array.from({ length: days }, (_, i) => {
    const d = today - (days - 1 - i) * DAY;
    const r = rng(dayKey(d) * 92821);
    let mA = 2.6 + r() * 1.0;
    const f = active(w, 'earth_leakage', Math.min(d + DAY - 1, now));
    if (f) {
      const age = Math.max(0, (d - startOfDay(f.started_at)) / DAY);
      mA += 2.2 + age * 2.6 + (rainedOn(d) ? 3.2 : 0);
    }
    return { t: d, v: +Math.min(mA, 26).toFixed(1), rain: rainedOn(d) };
  });
}

// Supply pressure at the inlet (bar).
export function pressureNow(now = Date.now(), w: World = getWorld()) {
  return active(w, 'supply_drop', now) ? 0.1 : 1.8;
}

// Roof tank: estimated level and how long it lasts at this household's usual rate, hour by hour.
export function tankOutlook(now = Date.now()) {
  let litres = 820; // ≈ 82% of a 1,000 L tank when supply stopped
  let t = startOfHour(now);
  for (let i = 0; i < 48; i++) {
    litres -= WATER_PROFILE[local(t).h];
    if (litres <= 0) return { litres_now: 820, empty_at: t };
    t += HOUR;
  }
  return { litres_now: 820, empty_at: null as number | null };
}

// Ground-floor suite activity (guest bath water + suite circuits). Used for the living-alone check.
export function suiteLastActivity(now = Date.now(), w: World = getWorld()) {
  const today = startOfDay(now);
  if (active(w, 'quiet_suite', now)) return today - 2 * HOUR; // nothing since 10pm last night
  // Normal days: activity through the day (latest within the last ~half hour), quiet overnight until ~6:40am.
  const wake = today + 6 * HOUR + 40 * 60e3;
  if (now < wake) return today - 2 * HOUR;
  return now - (Math.floor(now / 60e3) % 25) * 60e3;
}

export function todayUsage(now = Date.now(), w: World = getWorld()) {
  const day = startOfDay(now);
  const hours = Math.max(1, Math.ceil((now - day) / HOUR));
  const water = Array.from({ length: hours }, (_, h) => waterFlowAt(day + h * HOUR, w) * 60);
  const elec = Array.from({ length: hours }, (_, h) => elecAt(day + h * HOUR));
  const typicalWater = WATER_PROFILE.slice(0, hours).reduce((a, b) => a + b, 0);
  const typicalElec = ELEC_PROFILE.slice(0, hours).reduce((a, b) => a + b, 0);
  return {
    water_l: Math.round(water.reduce((a, b) => a + b, 0)), water_typical_l: Math.round(typicalWater), water_series: water,
    elec_kwh: +elec.reduce((a, b) => a + b, 0).toFixed(1), elec_typical_kwh: +typicalElec.toFixed(1), elec_series: elec,
  };
}

// The household's normal monthly water use (for tariff band maths), from the profile.
export const BASELINE_M3_PER_MONTH = +((WATER_PROFILE.reduce((a, b) => a + b, 0) * 30) / 1000).toFixed(1);
export const TIME = { HOUR, DAY, startOfDay, startOfHour };
