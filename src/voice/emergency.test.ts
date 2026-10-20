import { describe, expect, test } from 'vitest';
import type { Case } from '../api/types';
import { answerKind, isEmergency, planFor } from './emergency';

// The reviewed safety texts (services/api/src/penjaga_api/data/safety_guidance.json), as urgent cases carry them.
const BURST = ['Turn off the water supply: close the main stop valve near your water meter.', 'Call a SPAN-registered plumber to find and repair the leak.'];
const TRIPS = ["Don't keep resetting a breaker that trips again. Leave it off and unplug what is on that circuit.", 'Contact an ST-registered electrical contractor, and check their CPIC card.', "Don't open the distribution board yourself."];
const OVERLOAD = ['Unplug some of the appliances on the kitchen sockets circuit now, starting with high-power ones.', "Don't plug too many appliances into one socket."];
// the stand-in's urgent earth leakage
const STANDIN = ["Don't use the terrace outdoor socket until it has been checked.", "If the safety switch (RCD) trips, don't keep resetting it.", 'You can switch off the "Outdoor socket" breaker in the board by the front door. Don\'t open the board or the socket yourself.'];

const urgent = (layer: Case['layer'], safety: string[] | undefined, extra: Partial<Case> = {}): Case => ({
  id: 'c1', status: 'open', layer, severity: 'urgent', title: 'Water is escaping fast at your meter', location: '', summary: '',
  confidence: 1, confidence_note: '', impact: { headline: '', lines: [] }, actions: [], timeline: [], opened_at: 0, safety,
  evidence: { title: '', series: { unit: '', points: [] }, stats: [], explanation: '' },
  focus: { layer: layer === 'L2' ? 'electrical' : 'water', object_ids: ['circ_sockets_kitchen'], severity: 'urgent', label: 'Kitchen sockets' },
  ...extra,
});
const roomOf = (id: string) => (id === 'circ_sockets_kitchen' ? 'GF:kitchen' : null);

describe('an urgent case becomes a place to go and a thing to do', () => {
  test('a burst pipe: the main stop valve, by the meter, in the reviewed words', () => {
    const p = planFor(urgent('L1', BURST), roomOf)!;
    expect(p.target).toEqual({ device: 'valve_main_stop' });
    expect(p.where).toBe('next to the water meter, in the car porch');
    expect(p.action).toBe(BURST[0]);
    expect(p.after).toEqual([BURST[1]]);
    expect(p.verify).toBe('water');
    expect(p.alarm).toBe('Water is escaping fast at your meter.');
  });
  test('a breaker that keeps tripping: the switchboard in the foyer', () => {
    const p = planFor(urgent('L2', TRIPS), roomOf)!;
    expect(p.target).toEqual({ device: 'db_main' });
    expect(p.where).toBe('on the wall in the foyer, by the front door');
    expect(p.action).toBe(TRIPS[0]);
  });
  test('an overload: unplug first, in the room the circuit feeds', () => {
    const p = planFor(urgent('L2', OVERLOAD), roomOf)!;
    expect(p.target).toEqual({ room: 'kitchen' });
    expect(p.action).toBe(OVERLOAD[0]);
  });
  test('the line that says to switch off at the board is the action', () => {
    const p = planFor(urgent('L2', STANDIN), roomOf)!;
    expect(p.target).toEqual({ device: 'db_main' });
    expect(p.action).toBe(STANDIN[2]);
  });
  test('shutting a supply off is containing it, not fixing it; unplugging is not a shut-off', () => {
    expect(planFor(urgent('L1', BURST), roomOf)!.contain).toBe('main_stop_valve');
    expect(planFor(urgent('L2', TRIPS), roomOf)!.contain).toBe('breaker'); // "Leave it off"
    expect(planFor(urgent('L2', STANDIN), roomOf)!.contain).toBe('breaker'); // "switch off the ... breaker"
    expect(planFor(urgent('L2', OVERLOAD), roomOf)!.contain).toBeUndefined();
  });
  test('no reviewed guidance, or none naming a place: no route is invented', () => {
    expect(planFor(urgent('L1', undefined), roomOf)).toBeNull();
    expect(planFor(urgent('L2', ['Call an electrician.']), roomOf)).toBeNull();
  });
  test('only open urgent cases start one', () => {
    expect(isEmergency(urgent('L1', BURST))).toBe(true);
    expect(isEmergency(urgent('L1', BURST, { status: 'resolved' }))).toBe(false);
    expect(isEmergency(urgent('L1', BURST, { severity: 'act_soon' }))).toBe(false);
    expect(isEmergency(urgent('L1', BURST, { status: 'action_pending', contained: { what: 'main_stop_valve', at: 0 } }))).toBe(false);
  });
});

describe('what the owner says back', () => {
  test.each([
    ['done', 'done'], ["it's off now", 'done'], ['Close', 'done'], ['closed', 'done'], ['switch off', 'done'], ['off', 'done'], ['dah tutup', 'done'], ['okay I closed it', 'done'], ['yes', 'done'], ["I'm there", 'done'], ['sudah', 'done'],
    ["I can't find it", 'lost'], ['where is the valve', 'lost'], ['help', 'lost'], ['tak jumpa', 'lost'],
    ['not yet', 'wait'], ["it's not off", 'wait'], ["I haven't closed it", 'wait'], ['no', 'wait'], ['hold on', 'wait'], ["I'm on my way", 'wait'], ['kejap', 'wait'],
    ['the cat is hungry', 'unknown'],
  ])('%s is %s', (said, kind) => expect(answerKind(said)).toBe(kind));
});
