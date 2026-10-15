// Detection stand-in (M5). Works ONLY from readings exposed by the simulator — no fault labels.
// These are simple, explainable rules (the PRD's baselines); the trained models from the Data + ML workstream
// replace them behind the same Finding shape. Confidence values here are simulated, not measured.
import { nightMinFlow, residualDaily, pressureNow, suiteLastActivity, TIME, local } from './sim';

export interface Finding {
  key: string;
  layer: 'L1' | 'L2' | 'L5' | 'wellbeing';
  type: string;
  confidence: number;
  first_seen: number;
  evidence: Record<string, number | string | boolean>;
  candidates?: { object_id: string; p: number }[];
}

const CISTERNS = ['fix_bath_guest_wc_cistern', 'fix_bath2_wc_cistern', 'fix_bath1_wc_cistern', 'fix_bath_master_wc_cistern'];

export function detect(now = Date.now()): Finding[] {
  const out: Finding[] = [];

  // L1 night-flow: water moving in the household's quiet window on most recent nights.
  const nights = nightMinFlow(7, now);
  const recent = nights.slice(-3);
  const wet = recent.filter((n) => n.v >= 0.05);
  if (wet.length >= 2) {
    const flows = wet.map((n) => n.v);
    const lpm = flows.reduce((a, b) => a + b, 0) / flows.length;
    const onset = nights.find((n) => n.v >= 0.05)!.t;
    out.push({
      key: `L1:night_flow:${onset}`, layer: 'L1', type: 'continuous_night_flow',
      confidence: Math.min(0.9, 0.78 + 0.04 * wet.length), first_seen: onset,
      evidence: { night_min_flow_lpm: +lpm.toFixed(3), nights_observed: wet.length, litres_per_day: Math.round(lpm * 1440), pattern: 'periodic_refill' },
      // A periodic-refill signature points at cisterns; one meter can't say which, so all four are candidates.
      candidates: CISTERNS.map((id) => ({ object_id: id, p: 0.2 })).concat([{ object_id: 'fix_bath2_basin_tap', p: 0.05 }]),
    });
  }

  // L2 safety floor: residual current trending towards the RCD's 30 mA rating.
  const days = residualDaily(7, now);
  const last = days[days.length - 1].v, first = days[0].v;
  if (last >= 9 && last - first >= 4) {
    const rain = days.filter((d) => d.rain).map((d) => d.v), dry = days.filter((d) => !d.rain).map((d) => d.v);
    const avg = (a: number[]) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
    out.push({
      key: `L2:earth_leakage:${days.find((d) => d.v > first + 1.5)?.t ?? days[0].t}`, layer: 'L2', type: 'earth_leakage_rising',
      confidence: 0.8, first_seen: days.find((d) => d.v > first + 1.5)?.t ?? days[0].t,
      evidence: { residual_ma: last, residual_ma_week_start: first, rcd_rating_ma: 30, rain_avg_ma: +avg(rain).toFixed(1), dry_avg_ma: +avg(dry).toFixed(1) },
      // Leakage that rises with rain points at circuits exposed to weather.
      candidates: [{ object_id: 'circ_sockets_outdoor', p: 0.7 }, { object_id: 'circ_wh_bath_guest', p: 0.12 }, { object_id: 'circ_sockets_gf_rear', p: 0.08 }],
    });
  }

  // L5: supply pressure lost at the inlet.
  if (pressureNow(now) < 0.5) {
    out.push({ key: 'L5:pressure_drop', layer: 'L5', type: 'supply_pressure_drop', confidence: 0.95, first_seen: now, evidence: { pressure_bar: pressureNow(now) } });
  }

  // Living-alone check: no water/power use in the ground-floor suite for 10+ hours by mid-morning.
  const quietFor = now - suiteLastActivity(now);
  if (local(now).h >= 9 && quietFor >= 10 * TIME.HOUR) {
    out.push({ key: `wellbeing:suite:${TIME.startOfDay(now)}`, layer: 'wellbeing', type: 'suite_quiet', confidence: 0.9, first_seen: suiteLastActivity(now), evidence: { quiet_hours: Math.floor(quietFor / TIME.HOUR) } });
  }
  return out;
}
