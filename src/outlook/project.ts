// "What if they wait?": a case's own measurements (GET /api/v1/outlook, services/api outlook.py) carried forward to a
// moment in the future. Used by the homeowner app (Penjaga plays the future in the house when asked) and by the Lab
// (the presenter's slider, mirrored to the app). Nothing here is invented: litres a day times days, the case's own ringgit a
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
  if (x < 330) return `${n1(x / 30)} months`;
  const years = Math.round((x / 365) * 10) / 10;
  return Number.isInteger(years) ? `${years} year${years === 1 ? '' : 's'}` : `${years} years`;
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

/** "What happens if I wait?" and the ways people say it ("what if I leave it", "if we don't fix it"); not every
 * question with "wait" in it ("what time should I wait for the plumber"). */
export const asksAboutWaiting = (q: string): boolean =>
  /\bwhat happens if\b|\bif (i|we)\b.*\b(wait|leave it|ignore it|don'?t fix|do nothing)\b/i.test(q);

const SEV_RANK: Record<Severity, number> = { info: 0, heads_up: 1, act_soon: 2, urgent: 3 };
/** The case to carry forward: the one asked about if it can be, else the most serious open one that can. */
export function pickOutlook(list: Outlook[], caseId?: string): Outlook | null {
  const ready = list.filter((o) => o.projectable && o.horizon && o.kind);
  return ready.find((o) => o.case_id === caseId) ?? [...ready].sort((a, b) => SEV_RANK[b.severity] - SEV_RANK[a.severity])[0] ?? null;
}

const litres = (v: number) => `${n0(v)} litres`;
const m3 = (v: number) => `${n0(v / 1000)} cubic metres`;
/** What Penjaga says while the future plays: the case's own numbers at the moments that matter, nothing more. */
export function narrate(o: Outlook): string {
  if (!o.projectable || !o.kind) return o.why_not ?? '';
  if (o.kind === 'water' && o.water) {
    const lpd = o.water.litres_per_day;
    const rmAt = (d: number) => (o.cost?.rm_per_month != null ? rm((o.cost.rm_per_month * d) / 30) : null);
    if ((o.horizon?.max ?? 365) <= 31) {
      const cost = rmAt(14);
      return `If it keeps running at this rate, that's ${litres(lpd)} a day: ${litres(lpd * 7)} in a week and ${litres(lpd * 14)} in two weeks${cost ? `, about ${cost} on the water bill` : ''}. Watch it pile up beside your house.`;
    }
    const month = rmAt(30), year = rmAt(365);
    return `If nothing changes, this loses about ${litres(lpd)} a day: ${litres(lpd * 30)} in a month and about ${m3(lpd * 365)} in a year${month && year ? `, about ${month} a month and ${year} a year on the water bill` : ''}. Watch it pile up beside your house.`;
  }
  if (o.kind === 'energy' && o.energy) {
    const month = project(o, 30).figures, year = project(o, 365).figures;
    const cost = month[1][0] !== 'n/a' ? `, about ${month[1][0]} a month and ${year[1][0]} a year on the power bill` : '';
    const growing = o.energy.gap_trend_pct_per_week ? ' The gap is growing, so it costs a little more each week.' : '';
    return `If nothing changes, it uses about ${month[0][0]} extra in a month and ${year[0][0]} in a year${cost}.${growing}`;
  }
  if (o.kind === 'leakage' && o.leakage) {
    const L = o.leakage;
    const parts: string[] = [];
    if (L.days_to_limit != null && L.days_to_limit > 0) parts.push(`reaches ${n0(L.limit_ma)} milliamps in about ${when(L.days_to_limit, 'day')}, when your safety switch may trip at any moment`);
    if (L.days_to_rated != null) parts.push(`${n0(L.rated_ma)} in about ${when(L.days_to_rated, 'day')}, when it trips and cuts the power to those circuits`);
    return parts.length ? `At the rate it's rising, the leakage ${parts.join(', and ')}.` : (o.why_not ?? '');
  }
  if (o.kind === 'tank' && o.tank) return `At the rate the house is using it, your roof tank runs dry in about ${when(o.tank.hours_left, 'hour')}.`;
  return '';
}
