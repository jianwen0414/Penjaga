import { describe, expect, test } from 'vitest';
import { asksAboutWaiting, energyBy, milestones, narrate, pickOutlook, project, when, type Outlook } from './project';

describe('how far ahead, in words', () => {
  test.each([
    [0.5, 'day', '12 h'], [3, 'day', '3.0 days'], [21, 'day', '3.0 weeks'], [90, 'day', '3.0 months'],
    [365, 'day', '1 year'], [730, 'day', '2 years'], [500, 'day', '1.4 years'], [6, 'hour', '6.0 h'],
  ] as const)('%s %s is %s', (x, unit, want) => expect(when(x, unit)).toBe(want));
});

describe('asking what happens if I wait', () => {
  test.each([
    ['What happens if I wait?', true], ['what if I wait', true], ['What if I leave it?', true],
    ["what if we don't fix it", true], ['and if I ignore it?', true], ['What happens if I do nothing', true],
    ['What time should I wait for the plumber?', false], ['How much is this costing me?', false], ["What's wrong at home?", false],
  ])('%s: %s', (q, yes) => expect(asksAboutWaiting(q)).toBe(yes));
});

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

describe('what Penjaga says while the future plays', () => {
  test('a slow leak: a day, a month, a year, and the bill', () => {
    expect(narrate(leak)).toBe('If nothing changes, this loses about 720 litres a day: 21,600 litres in a month and about 263 cubic metres in a year, about RM30.00 a month and RM365 a year on the water bill. Watch it pile up beside your house.');
  });
  test('a burst: a day, a week, two weeks', () => {
    const burst: Outlook = { ...leak, water: { litres_per_day: 9043 }, cost: { rm_per_month: 960 }, horizon: { unit: 'day', max: 14 } };
    expect(narrate(burst)).toBe("If it keeps running at this rate, that's 9,043 litres a day: 63,301 litres in a week and 126,602 litres in two weeks, about RM448 on the water bill. Watch it pile up beside your house.");
  });
  test('no ringgit figure is left out, not made up', () => {
    expect(narrate({ ...leak, cost: { rm_per_month: null } })).not.toMatch(/RM/);
  });
  test('rising leakage: when it may trip and when it trips', () => {
    const o: Outlook = { ...base, layer: 'L2', kind: 'leakage', leakage: { now_ma: 9, trend_ma_per_day: 0.3, limit_ma: 15, rated_ma: 30, days_to_limit: 20, days_to_rated: 70 }, horizon: { unit: 'day', max: 98 } };
    expect(narrate(o)).toBe("At the rate it's rising, the leakage reaches 15 milliamps in about 2.9 weeks, when your safety switch may trip at any moment, and 30 in about 2.3 months, when it trips and cuts the power to those circuits.");
  });
  test('a case with nothing to carry forward says why', () => {
    expect(narrate({ ...leak, projectable: false, kind: null, why_not: 'This is a risk right now.' })).toBe('This is a risk right now.');
  });
  test('the case asked about, else the most serious one that can be carried forward', () => {
    const a = { ...leak, case_id: 'a', severity: 'heads_up' as const };
    const b = { ...leak, case_id: 'b', severity: 'urgent' as const };
    const c = { ...leak, case_id: 'c', severity: 'urgent' as const, projectable: false, kind: null };
    expect(pickOutlook([a, b, c])?.case_id).toBe('b');
    expect(pickOutlook([a, b, c], 'a')?.case_id).toBe('a');
    expect(pickOutlook([a, b, c], 'c')?.case_id).toBe('b'); // c can't be carried forward
    expect(pickOutlook([c])).toBeNull();
  });
});
