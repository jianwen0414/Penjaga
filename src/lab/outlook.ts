// "What if they wait?" for the Lab: a case's own measurements (GET /api/v1/outlook, services/api outlook.py) carried
// forward to a moment in the future. Nothing here is invented: litres a day times days, the case's own ringgit a
// month, the measured trend of the leakage or of an appliance's efficiency gap, the tank's own hours. Where the
// projection rests on an assumption (the rate stays the same), the basis says so.
import type { FocusCommand, Severity } from '../api/types';

export interface Outlook {
  case_id: string; title: string; location: string; layer: string; severity: Severity; status: string;
  focus: FocusCommand; finding_type: string; measured_at?: string;
  kind: 'water' | 'energy' | 'leakage' | 'tank' | null;
  projectable: boolean; why_not: string | null; basis?: string;
  water?: { litres_per_day: number };
  energy?: { kwh_per_day: number; gap_pct: number | null; gap_trend_pct_per_week: number | null };
  cost?: { rm_per_month: number | null; how?: string; note?: string | null; why_not?: string };
  leakage?: { now_ma: number; trend_ma_per_day: number; limit_ma: number; rated_ma: number; days_to_limit: number | null; days_to_rated: number | null };
  tank?: { litres: number; hours_left: number };
  horizon?: { unit: 'day' | 'hour'; max: number };
}

export interface Projection {
  /** Severity the part has reached by then (drives the 3D glow). */
  severity: Severity;
  /** One line for the 3D label. */
  label: string;
  /** Big numbers for the panel: [value, unit/what]. */
  figures: [string, string][];
  /** Water lost so far, m³ (leaks only: the cube). */
  waterM3: number | null;
}

const n0 = (v: number) => Math.round(v).toLocaleString('en-MY');
const n1 = (v: number) => v.toLocaleString('en-MY', { maximumFractionDigits: 1, minimumFractionDigits: v < 10 ? 1 : 0 });
const rm = (v: number) => `RM${v < 100 ? v.toFixed(2) : n0(v)}`;
export const when = (x: number, unit: 'day' | 'hour') => {
  if (unit === 'hour') return x < 1 ? `${Math.round(x * 60)} min` : `${n1(x)} h`;
  if (x < 1) return `${Math.round(x * 24)} h`;
  if (x < 14) return `${n1(x)} days`;
  if (x < 60) return `${n1(x / 7)} weeks`;
  return `${n1(x / 30)} months`;
};

/** Extra kWh used over `days`: constant, or along the measured growth of the efficiency gap (linear in weeks). */
export function energyBy(e: NonNullable<Outlook['energy']>, days: number): number {
  const { kwh_per_day: k, gap_pct: g, gap_trend_pct_per_week: tr } = e;
  if (!tr || !g || g <= 0) return k * days;
  // extra energy is proportional to the gap: k(t) = k × (g + tr × t/7) / g, so the total is k × (t + tr/(7g) × t²/2)
  return k * (days + (tr / (7 * g)) * (days * days) / 2);
}

/** Where the case has got to after `x` (days, or hours for a tank). */
export function project(o: Outlook, x: number): Projection {
  x = Math.max(0, x);
  if (o.kind === 'water' && o.water) {
    const litres = o.water.litres_per_day * x;
    const cost = o.cost?.rm_per_month != null ? (o.cost.rm_per_month * x) / 30 : null;
    const sev: Severity = litres >= 20_000 ? 'urgent' : litres >= 5_000 ? 'act_soon' : 'heads_up';
    return {
      severity: x === 0 ? o.severity : sev,
      label: `${n0(litres)} L lost${cost != null ? ` · ${rm(cost)}` : ''}`,
      figures: [[`${n0(litres)} L`, 'water lost'], [cost != null ? rm(cost) : 'n/a', cost != null ? 'on the water bill' : 'no ringgit figure yet']],
      waterM3: litres / 1000,
    };
  }
  if (o.kind === 'energy' && o.energy) {
    const kwh = energyBy(o.energy, x);
    const perKwh = o.cost?.rm_per_month != null && o.energy.kwh_per_day > 0 ? o.cost.rm_per_month / 30 / o.energy.kwh_per_day : null;
    const cost = perKwh != null ? kwh * perKwh : null;
    const gap = o.energy.gap_pct != null ? o.energy.gap_pct + (o.energy.gap_trend_pct_per_week ?? 0) * (x / 7) : null;
    const sev: Severity = gap != null ? (gap >= 40 ? 'urgent' : gap >= 25 ? 'act_soon' : 'heads_up') : 'heads_up';
    return {
      severity: x === 0 ? o.severity : sev,
      label: `${n1(kwh)} kWh extra${cost != null ? ` · ${rm(cost)}` : ''}${gap != null ? ` · ${n0(gap)}% less efficient` : ''}`,
      figures: [[`${n1(kwh)} kWh`, 'extra energy'], [cost != null ? rm(cost) : 'n/a', cost != null ? 'on the power bill' : 'no ringgit figure yet'], ...(gap != null ? [[`${n0(gap)}%`, 'efficiency gap'] as [string, string]] : [])],
      waterM3: null,
    };
  }
  if (o.kind === 'leakage' && o.leakage) {
    const L = o.leakage;
    const ma = L.now_ma + L.trend_ma_per_day * x;
    const trips = ma >= L.rated_ma, may = ma >= L.limit_ma;
    return {
      severity: trips ? 'urgent' : may ? 'act_soon' : 'heads_up',
      label: trips ? `${n1(ma)} mA · the safety switch trips: power off` : may ? `${n1(ma)} mA · may trip at any moment` : `${n1(ma)} mA leaking to earth`,
      figures: [[`${n1(Math.min(ma, L.rated_ma))} mA`, 'leakage to earth'], [trips ? 'Trips' : may ? 'May trip' : 'Holds', `safety switch (${n0(L.rated_ma)} mA)`]],
      waterM3: null,
    };
  }
  if (o.kind === 'tank' && o.tank) {
    const left = Math.max(0, o.tank.litres * (1 - x / o.tank.hours_left));
    const empty = left <= 0;
    return {
      severity: empty ? 'urgent' : left < o.tank.litres * 0.3 ? 'act_soon' : 'heads_up',
      label: empty ? 'Roof tank empty: no water in the taps' : `${n0(left)} L left in the roof tank`,
      figures: [[`${n0(left)} L`, 'left in the roof tank'], [empty ? 'Empty' : when(o.tank.hours_left - x, 'hour'), empty ? 'no water in the taps' : 'until it runs dry']],
      waterM3: null,
    };
  }
  return { severity: o.severity, label: o.title, figures: [], waterM3: null };
}

/** Moments worth marking on the slider: [x, what happens]. */
export function milestones(o: Outlook): [number, string][] {
  const max = o.horizon?.max ?? 365;
  const out: [number, string][] = [];
  if (o.kind === 'water' && o.water && o.water.litres_per_day > 0) {
    const lpd = o.water.litres_per_day;
    out.push([1000 / lpd, '1,000 L: a cubic metre']);
    if (o.cost?.rm_per_month) out.push([(100 / o.cost.rm_per_month) * 30, 'RM100 on the bill']);
    out.push([30, 'A month']);
    out.push([365, 'A year']);
  }
  if (o.kind === 'energy' && o.energy) {
    out.push([30, 'A month']);
    out.push([365, 'A year']);
  }
  if (o.kind === 'leakage' && o.leakage) {
    if (o.leakage.days_to_limit != null) out.push([o.leakage.days_to_limit, `${n0(o.leakage.limit_ma)} mA: may trip at any moment`]);
    if (o.leakage.days_to_rated != null) out.push([o.leakage.days_to_rated, `${n0(o.leakage.rated_ma)} mA: the safety switch trips`]);
  }
  if (o.kind === 'tank' && o.tank) out.push([o.tank.hours_left, 'Roof tank empty']);
  return out.filter(([x]) => x > 0 && x <= max).sort((a, b) => a[0] - b[0]);
}
