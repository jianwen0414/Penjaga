// Walking routes through the house, worked out from the same spec the 3D model is built from (house.spec.json), so a
// route can only go where the drawings say a person can: through doors, sliding doors and open-plan gaps, and up
// or down the stair. Nothing about the layout is written here.
//
// The house is cut into cells (each room's floor pieces). Two cells on the same floor connect where their edges
// touch and no wall stands there (open plan), or where a wall on that edge has a door or opening. The stair joins the
// ground floor's stair cell to the first floor's landing. Dijkstra over the passages gives the shortest walk; the
// route steps through each doorway square-on, and says it in words ("go through the dining area to the foyer, then
// out the front door").
import spec from '../../house.spec.json';

export type Floor = 'GF' | 'FF';
export type Pt = [number, number]; // spec metres: x left→right from the street, y front→rear
type Rect = [number, number, number, number]; // x0, x1, y0, y1
interface Cell { id: string; room: string; floor: Floor; rect: Rect }
interface Passage { a: string; b: string; at: Pt; normal: Pt; kind: 'door' | 'front_door' | 'slider' | 'open' | 'stair'; width: number }
interface WallSpec { id: string; floor: Floor; a: Pt; b: Pt; t: number; openings: { id: string; kind: string; from: number; to: number; main?: boolean }[] }
interface Stair { x1: number; x2: number; y1: number; y2: number; z1: number; z2: number }

/** A route as the 3D view draws it: per-floor runs of points (z = the floor's own level), and where the stair is. */
export interface Route {
  from: { room: string; floor: Floor; at: Pt };
  to: { room: string; floor: Floor; at: Pt; id?: string };
  legs: { floor: Floor; points: Pt[] }[];
  /** The stair climb, if the route changes floor: foot (GF) to top (FF), in spec metres with heights. */
  stair: { foot: [number, number, number]; top: [number, number, number]; up: boolean } | null;
  rooms: string[]; // rooms passed through, in order, start and end included
  metres: number;
  /** Directions in words. */
  say: string;
}

const S = spec as unknown as {
  rooms: { id: string; floor: Floor; name: string; center: [number, number, number] }[];
  floorFinishes: { id: string; floor: Floor; room: string; rect: Rect }[];
  walls: WallSpec[];
  solids: { id: string; kind: string; stair?: Stair }[];
  services: { devices: { id: string; floor: Floor; room: string; at: [number, number, number] }[] };
  floors: Record<Floor, { z: number }>;
};
const ROOM = new Map(S.rooms.map((r) => [r.id, r]));
const EPS = 0.06; // edges within this distance are the same line (walls are drawn on their centre lines)
const MIN_GAP = 0.35; // narrower than this is not a way through
const PASSABLE = new Set(['door', 'slider', 'open']);

// ---------------------------------------------------------------- the graph, built once from the spec

const cells: Cell[] = S.floorFinishes.map((f) => ({ id: f.id, room: f.room, floor: f.floor, rect: f.rect }));
const cellById = new Map(cells.map((c) => [c.id, c]));

type Span = [number, number];
/** The parts of [s0, s1] not covered by `cover`. */
function uncovered([s0, s1]: Span, cover: Span[]): Span[] {
  const sorted = cover.map(([a, b]) => [Math.max(a, s0), Math.min(b, s1)] as Span).filter(([a, b]) => b > a).sort((p, q) => p[0] - q[0]);
  const out: Span[] = [];
  let at = s0;
  for (const [a, b] of sorted) { if (a > at) out.push([at, a]); at = Math.max(at, b); }
  if (at < s1) out.push([at, s1]);
  return out;
}

/** Walls lying on an axis-aligned line (x = c when `vertical`, else y = c), as spans along the line, with openings. */
function wallsOn(floor: Floor, vertical: boolean, c: number) {
  return S.walls.filter((w) => w.floor === floor).flatMap((w) => {
    const [ax, ay] = w.a, [bx, by] = w.b;
    const isVertical = Math.abs(ax - bx) < 1e-6, isHorizontal = Math.abs(ay - by) < 1e-6;
    if (vertical ? !isVertical || Math.abs(ax - c) > EPS : !isHorizontal || Math.abs(ay - c) > EPS) return [];
    const s0 = vertical ? Math.min(ay, by) : Math.min(ax, bx), s1 = vertical ? Math.max(ay, by) : Math.max(ax, bx);
    // openings are given as positions along the wall's own axis (absolute coordinates, like the wall ends)
    return [{ span: [s0 - w.t / 2, s1 + w.t / 2] as Span, openings: w.openings.filter((o) => PASSABLE.has(o.kind)).map((o) => ({ span: [Math.min(o.from, o.to), Math.max(o.from, o.to)] as Span, main: !!o.main, kind: o.kind })) }];
  });
}

const passages: Passage[] = [];
for (let i = 0; i < cells.length; i++) {
  for (let j = i + 1; j < cells.length; j++) {
    const A = cells[i], B = cells[j];
    if (A.floor !== B.floor) continue;
    const [ax0, ax1, ay0, ay1] = A.rect, [bx0, bx1, by0, by1] = B.rect;
    for (const vertical of [true, false]) {
      // a shared edge: A's right edge is B's left (or the other way), with overlap along the edge
      const pairs: [number, number][] = vertical ? [[ax1, bx0], [bx1, ax0]] : [[ay1, by0], [by1, ay0]];
      for (const [e1, e2] of pairs) {
        if (Math.abs(e1 - e2) > EPS) continue;
        const c = (e1 + e2) / 2;
        const span: Span = vertical ? [Math.max(ay0, by0), Math.min(ay1, by1)] : [Math.max(ax0, bx0), Math.min(ax1, bx1)];
        if (span[1] - span[0] < MIN_GAP) continue;
        const ws = wallsOn(A.floor, vertical, c);
        const normal: Pt = vertical ? [1, 0] : [0, 1];
        const at = (s: number): Pt => (vertical ? [c, s] : [s, c]);
        // open plan: no wall along part of the shared edge
        for (const [g0, g1] of uncovered(span, ws.map((w) => w.span))) {
          if (g1 - g0 >= MIN_GAP) passages.push({ a: A.id, b: B.id, at: at((g0 + g1) / 2), normal, kind: 'open', width: g1 - g0 });
        }
        // doors and openings in the walls on that edge
        for (const w of ws) for (const o of w.openings) {
          const [o0, o1] = [Math.max(o.span[0], span[0]), Math.min(o.span[1], span[1])];
          if (o1 - o0 >= MIN_GAP) passages.push({ a: A.id, b: B.id, at: at((o0 + o1) / 2), normal, kind: o.main ? 'front_door' : o.kind === 'slider' ? 'slider' : o.kind === 'open' ? 'open' : 'door', width: o1 - o0 });
        }
      }
    }
  }
}

// the stair: its foot is in the ground floor's stair cell, its top on the first floor's landing
const STAIR = S.solids.find((s) => s.kind === 'stair' && s.stair)?.stair ?? null;
const cellAt = (floor: Floor, [x, y]: Pt, room?: string): Cell | undefined => {
  const hits = cells.filter((c) => c.floor === floor && (!room || c.room === room) && x >= c.rect[0] - EPS && x <= c.rect[1] + EPS && y >= c.rect[2] - EPS && y <= c.rect[3] + EPS);
  return hits.sort((p, q) => area(p.rect) - area(q.rect))[0]; // the most specific piece
};
const area = (r: Rect) => (r[1] - r[0]) * (r[3] - r[2]);
let stairLink: { foot: Pt; top: Pt; footCell: string; topCell: string } | null = null;
if (STAIR) {
  const x = (STAIR.x1 + STAIR.x2) / 2;
  const foot: Pt = [x, STAIR.y1 + 0.05];
  const top: Pt = [x, STAIR.y2 + 0.2];
  const footCell = cellAt('GF', foot), topCell = cellAt('FF', top);
  if (footCell && topCell) {
    stairLink = { foot, top, footCell: footCell.id, topCell: topCell.id };
    passages.push({ a: footCell.id, b: topCell.id, at: foot, normal: [0, 1], kind: 'stair', width: STAIR.x2 - STAIR.x1 });
  }
}

/** The cells and passages, for checks and for drawing (read-only). */
export const graph = { cells: cells as readonly Cell[], passages: passages as readonly Passage[] };

// ---------------------------------------------------------------- shortest walk

const dist = (a: Pt, b: Pt) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const STAIR_COST = 6; // a flight of 18 steps counts as about 6 m of walking

/** A sensible place to stand in a room: its main piece's centre, nudged off a centre that's on an edge. */
export function roomSpot(room: string): { floor: Floor; at: Pt; cell: string } | null {
  const pieces = cells.filter((c) => c.room === room).sort((p, q) => area(q.rect) - area(p.rect));
  const main = pieces[0];
  if (!main) return null;
  const r = ROOM.get(room);
  const centre: Pt = r && cellAt(main.floor, [r.center[0], r.center[1]], room)?.id === main.id ? [r.center[0], r.center[1]] : [(main.rect[0] + main.rect[1]) / 2, (main.rect[2] + main.rect[3]) / 2];
  return { floor: main.floor, at: centre, cell: main.id };
}

/** Where to stand to reach a device: its floor position, stepped into its room if it's on a wall. */
export function deviceSpot(id: string): { room: string; floor: Floor; at: Pt; cell: string } | null {
  const d = S.services.devices.find((x) => x.id === id);
  if (!d) return null;
  const floor = d.floor;
  let cell = cellAt(floor, [d.at[0], d.at[1]], d.room) ?? cellAt(floor, [d.at[0], d.at[1]]);
  if (!cell) cell = cells.filter((c) => c.room === d.room && c.floor === floor)[0];
  if (!cell) return null;
  const [x0, x1, y0, y1] = cell.rect;
  const inset = 0.45; // stand in front of it, not inside the wall
  const at: Pt = [Math.min(x1 - inset, Math.max(x0 + inset, d.at[0])), Math.min(y1 - inset, Math.max(y0 + inset, d.at[1]))];
  return { room: cell.room, floor, at, cell: cell.id };
}

interface Node { id: string; cell: string; at: Pt; passage?: Passage }

/** The shortest walk from a room to a target (a device ID or a room), or null if the drawings have no way there. */
export function routeTo(fromRoom: string, target: { device: string } | { room: string }): Route | null {
  const start = roomSpot(fromRoom);
  const goal = 'device' in target ? deviceSpot(target.device) : (() => { const s = roomSpot(target.room); return s && { ...s, room: target.room }; })();
  if (!start || !goal) return null;
  // nodes: the start, the goal, and both sides of every passage
  const nodes: Node[] = [{ id: 'start', cell: start.cell, at: start.at }, { id: 'goal', cell: goal.cell, at: goal.at }];
  passages.forEach((p, k) => {
    if (p.kind === 'stair' && stairLink) {
      nodes.push({ id: `p${k}a`, cell: p.a, at: stairLink.foot, passage: p }, { id: `p${k}b`, cell: p.b, at: stairLink.top, passage: p });
    } else {
      nodes.push({ id: `p${k}a`, cell: p.a, at: p.at, passage: p }, { id: `p${k}b`, cell: p.b, at: p.at, passage: p });
    }
  });
  const byCell = new Map<string, Node[]>();
  nodes.forEach((n) => byCell.set(n.cell, [...(byCell.get(n.cell) ?? []), n]));
  const edges = new Map<string, { to: string; w: number }[]>();
  const link = (a: Node, b: Node, w: number) => { edges.set(a.id, [...(edges.get(a.id) ?? []), { to: b.id, w }]); };
  for (const group of byCell.values()) for (const a of group) for (const b of group) if (a !== b) link(a, b, dist(a.at, b.at));
  passages.forEach((p, k) => {
    const a = nodes.find((n) => n.id === `p${k}a`)!, b = nodes.find((n) => n.id === `p${k}b`)!;
    const w = p.kind === 'stair' ? STAIR_COST : 0;
    link(a, b, w); link(b, a, w);
  });
  // Dijkstra (a few hundred nodes at most)
  const best = new Map<string, number>([['start', 0]]);
  const prev = new Map<string, string>();
  const open = new Set(['start']);
  while (open.size) {
    let cur = '', d = Infinity;
    for (const id of open) { const v = best.get(id)!; if (v < d) { d = v; cur = id; } }
    open.delete(cur);
    if (cur === 'goal') break;
    for (const e of edges.get(cur) ?? []) {
      const nd = d + e.w;
      if (nd < (best.get(e.to) ?? Infinity)) { best.set(e.to, nd); prev.set(e.to, cur); open.add(e.to); }
    }
  }
  if (!best.has('goal')) return null;
  const path: Node[] = [];
  for (let id: string | undefined = 'goal'; id; id = prev.get(id)) path.unshift(nodes.find((n) => n.id === id)!);
  return build(fromRoom, start, goal, 'device' in target ? target.device : undefined, path, best.get('goal')!);
}

// Points to draw: through each doorway square-on (a step before and after it), corners eased by the 3D view.
function build(fromRoom: string, start: { floor: Floor; at: Pt }, goal: { room: string; floor: Floor; at: Pt }, deviceId: string | undefined, path: Node[], metres: number): Route {
  const legs: Route['legs'] = [];
  let stair: Route['stair'] = null;
  const rooms: string[] = [];
  const doors: { kind: Passage['kind']; into: string }[] = [];
  const push = (floor: Floor, p: Pt) => {
    let leg = legs[legs.length - 1];
    if (!leg || leg.floor !== floor) { leg = { floor, points: [] }; legs.push(leg); }
    const last = leg.points[leg.points.length - 1];
    if (!last || dist(last, p) > 0.05) leg.points.push(p);
  };
  for (let i = 0; i < path.length; i++) {
    const n = path[i];
    const cell = cellById.get(n.cell)!;
    if (rooms[rooms.length - 1] !== cell.room) rooms.push(cell.room);
    const next = path[i + 1];
    if (n.passage && next?.passage === n.passage) {
      const p = n.passage;
      if (p.kind === 'stair' && STAIR) {
        const up = cell.floor === 'GF';
        const foot: [number, number, number] = [stairLink!.foot[0], STAIR.y1, STAIR.z1];
        const top: [number, number, number] = [stairLink!.top[0], STAIR.y2, STAIR.z2];
        stair = { foot, top, up };
        push(cell.floor, n.at);
        doors.push({ kind: 'stair', into: cellById.get(next.cell)!.room });
        continue;
      }
      // a step before the doorway, the doorway, a step after: on the side each node is on
      const side = (q: Pt) => Math.sign((q[0] - p.at[0]) * p.normal[0] + (q[1] - p.at[1]) * p.normal[1]) || 1;
      const c = cellById.get(n.cell)!;
      const centre: Pt = [(c.rect[0] + c.rect[1]) / 2, (c.rect[2] + c.rect[3]) / 2];
      const s = side(centre);
      const step = Math.min(0.45, p.kind === 'open' ? 0.3 : 0.45);
      push(c.floor, [p.at[0] + p.normal[0] * step * s, p.at[1] + p.normal[1] * step * s]);
      push(c.floor, p.at);
      push(c.floor, [p.at[0] - p.normal[0] * step * s, p.at[1] - p.normal[1] * step * s]);
      doors.push({ kind: p.kind, into: cellById.get(next.cell)!.room });
      i++; // the other side of the passage is already drawn
      const after = cellById.get(next.cell)!;
      if (rooms[rooms.length - 1] !== after.room) rooms.push(after.room);
      continue;
    }
    push(cell.floor, n.at);
  }
  // the climb starts where the ground-floor leg ends and the first-floor leg begins; legs stay per floor
  return { from: { room: fromRoom, floor: start.floor, at: start.at }, to: { room: goal.room, floor: goal.floor, at: goal.at, id: deviceId }, legs, stair, rooms, metres, say: directions(rooms, doors, fromRoom, goal.room) };
}

// ---------------------------------------------------------------- in words

// the words people use for each room (the spec's names are labels: "Living", "Dining", "Car Porch")
const SPOKEN: Record<string, string> = { living: 'living room', dining: 'dining area', stair: 'staircase', porch: 'car porch', family: 'family area' };
const spoken = (room: string) => {
  const n = SPOKEN[room] ?? ROOM.get(room)?.name ?? room;
  return /^(Bedroom|Bathroom) \d|^Flexi-Suite/.test(n) ? n : n.toLowerCase();
};
/** "the kitchen", "Bedroom 2", "the Flexi-Suite bedroom": how a room is said in a sentence. */
export const the = (room: string) => (/^(Bedroom|Bathroom) \d/.test(spoken(room)) ? spoken(room) : `the ${spoken(room)}`);
// rooms you pass through without them being worth naming (unless the walk ends there)
const PASS_THROUGH = new Set(['corridor', 'landing', 'stair', 'terrace']);

/** "From Bedroom 2, go down the stairs to the foyer, then out the front door to the car porch." */
function directions(rooms: string[], doors: { kind: Passage['kind']; into: string }[], from: string, to: string): string {
  if (from === to || rooms.length <= 1) return `It's right there in ${the(to)}.`;
  const parts: string[] = [];
  let via: string[] = [];
  // the walk since the last landmark (stairs, front door): "through A and B to C"
  const walk = (end?: string) => {
    const stops = via.filter((r) => !PASS_THROUGH.has(r) || r === end);
    const last = end ?? stops[stops.length - 1];
    const through = stops.filter((r) => r !== last);
    if (last) parts.push(`${through.length ? `through ${list(through.map(the))} ` : ''}to ${the(last)}`);
    via = [];
  };
  for (const d of doors) {
    if (d.kind === 'stair') { walk(); parts.push(`${ROOM.get(d.into)?.floor === 'GF' ? 'down' : 'up'} the stairs`); }
    else if (d.kind === 'front_door' || (d.kind === 'slider' && OUTSIDE.has(d.into))) {
      walk();
      const door = d.kind === 'front_door' ? 'the front door' : 'the sliding doors';
      parts.push(OUTSIDE.has(d.into) ? `out ${door}` : `in through ${door}`);
    }
    via.push(d.into);
  }
  walk(to);
  // "down the stairs" + "to the foyer" → "down the stairs to the foyer"
  const merged: string[] = [];
  for (const part of parts) {
    const prev = merged[merged.length - 1];
    if (prev && /(stairs|door|doors)$/.test(prev) && part.startsWith('to ')) merged[merged.length - 1] = `${prev} ${part}`;
    else merged.push(part);
  }
  return `From ${the(from)}, go ${merged.join(', then ')}.`;
}
const OUTSIDE = new Set(['terrace', 'porch', 'yard']);
const list = (xs: string[]) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
