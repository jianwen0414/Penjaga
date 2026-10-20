// Routes come from the drawings: every passage must be a real door, opening or open-plan gap, and every room must
// be reachable from every other, so an emergency route always exists.
import { describe, expect, test } from 'vitest';
import spec from '../../house.spec.json';
import { graph, routeTo, roomSpot, deviceSpot } from './route';

const rooms = (spec as unknown as { rooms: { id: string; floor: string }[] }).rooms.map((r) => r.id);
const roomOfCell = (id: string) => graph.cells.find((c) => c.id === id)!.room;
const linked = (a: string, b: string) => graph.passages.some((p) => [roomOfCell(p.a), roomOfCell(p.b)].sort().join() === [a, b].sort().join());

describe('the walkable graph', () => {
  test('doors in the drawings connect the rooms either side', () => {
    expect(linked('bath2', 'bed2')).toBe(true);
    expect(linked('bed4', 'kitchen')).toBe(true);
    expect(linked('kitchen', 'yard')).toBe(true);
    expect(linked('master', 'bath_master')).toBe(true);
  });
  test('open-plan rooms connect where no wall stands', () => {
    expect(linked('living', 'dining')).toBe(true);
    expect(linked('dining', 'kitchen')).toBe(true);
    expect(linked('porch', 'terrace')).toBe(true);
  });
  test('walls without a door keep rooms apart', () => {
    expect(linked('bath2', 'bath3')).toBe(false);
    expect(linked('bed2', 'bed3')).toBe(false);
    expect(linked('master', 'family')).toBe(false);
  });
  test('the front door is the main door and the stair joins the floors', () => {
    const front = graph.passages.filter((p) => p.kind === 'front_door');
    expect(front.map((p) => [roomOfCell(p.a), roomOfCell(p.b)].sort())).toEqual([['foyer', 'terrace']]);
    const stair = graph.passages.filter((p) => p.kind === 'stair');
    expect(stair.map((p) => [roomOfCell(p.a), roomOfCell(p.b)])).toEqual([['stair', 'landing']]);
  });
  test('every room can reach the main stop valve and the switchboard', () => {
    for (const r of rooms) {
      for (const device of ['valve_main_stop', 'db_main']) {
        const route = routeTo(r, { device });
        expect(route, `${r} to ${device}`).not.toBeNull();
        expect(route!.rooms[0]).toBe(r);
      }
    }
  });
});

describe('a route', () => {
  test('from upstairs to the main valve goes down the stairs and out to the porch', () => {
    const r = routeTo('bed2', { device: 'valve_main_stop' })!;
    expect(r.rooms[0]).toBe('bed2');
    expect(r.rooms.at(-1)).toBe('porch');
    expect(r.rooms).toContain('landing');
    expect(r.stair).not.toBeNull();
    expect(r.stair!.up).toBe(false);
    expect(r.legs.map((l) => l.floor)).toEqual(['FF', 'GF']);
    expect(r.say).toMatch(/^From Bedroom 2, go down the stairs/);
    expect(r.say).toMatch(/car porch\.$/);
  });
  test('from the kitchen to the switchboard stays downstairs', () => {
    const r = routeTo('kitchen', { device: 'db_main' })!;
    expect(r.stair).toBeNull();
    expect(r.legs.map((l) => l.floor)).toEqual(['GF']);
    expect(r.rooms.at(-1)).toBe('foyer');
    expect(r.say).toBe('From the kitchen, go through the dining area and the living room to the foyer.');
  });
  test('ends at a spot in front of the device, inside its room', () => {
    const r = routeTo('living', { device: 'db_main' })!;
    const spot = deviceSpot('db_main')!;
    expect(r.legs.at(-1)!.points.at(-1)).toEqual(spot.at);
    expect(spot.room).toBe('foyer');
    expect(spot.at[0]).toBeLessThan(6.1 - 0.4); // not inside the wall it hangs on
  });
  test('passes each doorway square-on (a point either side of it)', () => {
    const r = routeTo('bath2', { device: 'db_main' })!;
    const ff = r.legs[0].points;
    const door = ff.findIndex(([x, y]) => Math.abs(x - 4) < 0.01 && Math.abs(y - 11) < 0.01);
    expect(door).toBeGreaterThan(0);
    expect(ff[door - 1][0]).toBeCloseTo(4, 5);
    expect(ff[door + 1][0]).toBeCloseTo(4, 5);
    expect(Math.sign(ff[door - 1][1] - 11)).toBe(-Math.sign(ff[door + 1][1] - 11));
  });
  test('in the same room there is nowhere to go', () => {
    expect(routeTo('foyer', { device: 'db_main' })!.say).toBe("It's right there in the foyer.");
  });
  test('to a room as well as to a device', () => {
    const r = routeTo('master', { room: 'kitchen' })!;
    expect(r.rooms.at(-1)).toBe('kitchen');
    expect(r.to.at).toEqual(roomSpot('kitchen')!.at);
  });
  test('unknown places have no route', () => {
    expect(routeTo('attic', { device: 'db_main' })).toBeNull();
    expect(routeTo('kitchen', { device: 'nope' })).toBeNull();
  });
});
