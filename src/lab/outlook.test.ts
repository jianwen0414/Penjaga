import { describe, expect, test } from 'vitest';
import { energyBy, milestones, project, type Outlook } from './outlook';

const base = { case_id: 'case_1', title: 'Water running', location: '', layer: 'L1', severity: 'heads_up' as const, status: 'open', focus: { layer: 'water' as const, object_ids: ['fix_bath2_wc_cistern'], severity: 'heads_up' as const }, finding_type: 'running_cistern_suspected', projectable: true, why_not: null };
const leak: Outlook = { ...base, kind: 'water', water: { litres_per_day: 720 }, cost: { rm_per_month: 30 }, horizon: { unit: 'day', max: 365 } };

describe('a leak carried forward', () => {
  test('litres and ringgit grow with the days, at the measured rate', () => {
    const p = project(leak, 30);
    expect(p.waterM3).toBeCloseTo(21.6);
    expect(p.figures[0][0]).toBe('21,600 L');
    expect(p.figures[1][0]).toBe('RM30.00'); // the case's own RM a month, for one month
    expect(p.severity).toBe('urgent');
  });
  test('now is where the case is', () => {
    const p = project(leak, 0);
    expect(p.waterM3).toBe(0);
    expect(p.severity).toBe('heads_up');
  });
  test('no ringgit figure is shown as such, never made up', () => {
    const p = project({ ...leak, cost: { rm_per_month: null, why_not: 'Not enough history yet.' } }, 10);
    expect(p.figures[1]).toEqual(['n/a', 'no ringgit figure yet']);
    expect(p.label).not.toMatch(/RM/);
  });
  test('milestones: a cubic metre, RM100, a month, a year', () => {
    const m = milestones(leak);
    expect(m[0][0]).toBeCloseTo(1000 / 720);
    expect(m.map(([, w]) => w)).toEqual(['1,000 L: a cubic metre', 'A month', 'RM100 on the bill', 'A year']);
  });
});

describe('an appliance carried forward', () => {
  test('constant extra energy without a measured trend', () => {
    expect(energyBy({ kwh_per_day: 0.6, gap_pct: 20, gap_trend_pct_per_week: null }, 10)).toBeCloseTo(6);
  });
  test('along the measured growth of the gap', () => {
    // gap 20% growing 2%/week: after 7 days the extra is 0.6 × 22/20 a day; the total is the area under that line
    expect(energyBy({ kwh_per_day: 0.6, gap_pct: 20, gap_trend_pct_per_week: 2 }, 7)).toBeCloseTo(0.6 * 7 * 1.05);
  });
});

describe('rising leakage carried forward', () => {
  const o: Outlook = { ...base, layer: 'L2', kind: 'leakage', leakage: { now_ma: 9, trend_ma_per_day: 0.3, limit_ma: 15, rated_ma: 30, days_to_limit: 20, days_to_rated: 70 }, horizon: { unit: 'day', max: 98 } };
  test('holds, may trip, trips: at the levels of the switch', () => {
    expect(project(o, 10).severity).toBe('heads_up');
    expect(project(o, 25).severity).toBe('act_soon');
    expect(project(o, 25).label).toMatch(/may trip/);
    expect(project(o, 75).severity).toBe('urgent');
    expect(project(o, 75).figures[0][0]).toBe('30 mA'); // it trips at its rating; past that the power is off
  });
  test('its milestones are the switch levels', () => {
    expect(milestones(o)).toEqual([[20, '15 mA: may trip at any moment'], [70, '30 mA: the safety switch trips']]);
  });
});

describe('a supply cut carried forward', () => {
  const o: Outlook = { ...base, layer: 'L5', kind: 'tank', tank: { litres: 900, hours_left: 6 }, horizon: { unit: 'hour', max: 8 } };
  test('the tank drains to empty', () => {
    expect(project(o, 3).figures[0][0]).toBe('450 L');
    expect(project(o, 7).label).toMatch(/empty/);
    expect(milestones(o)).toEqual([[6, 'Roof tank empty']]);
  });
});
