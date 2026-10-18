// The Core API names house parts with the house record's IDs (the data contract); the 3D model (house.spec.json)
// uses the same names but leaves some parts out or keeps them in a coarser form. Before a focus command reaches
// the 3D view, each ID is turned into the 3D model's ID for the same part, or, when the model has no such part,
// the closest place that explains it (a breaker or safety switch: the distribution board; a mains pipe or stop
// valve: the water meter).
// IDs the model already has are left alone, so this falls away as the model gains parts.
import spec from '../../house.spec.json';
import type { FocusCommand } from './types';

const ids = new Set<string>();
(function walk(node: unknown) {
  if (Array.isArray(node)) node.forEach(walk);
  else if (node && typeof node === 'object') {
    const id = (node as { id?: unknown }).id;
    if (typeof id === 'string') ids.add(id);
    Object.values(node).forEach(walk);
  }
})(spec);

// The 3D model now uses the house record's names (rooms renamed 2026-10-19). What is still different:
// parts the model keeps under another name, and parts it represents only by their circuit or room.
const SAME: Record<string, string> = {
  app_fridge_kitchen: 'furn_GF_kitchen_fridge', // the model's fridge is furniture
  circ_sockets_yard: 'circ_sockets_gf_rear', // the model's rear ground-floor sockets serve the yard
  circ_sockets_kitchen_wet: 'circ_sockets_kitchen', // the model has one kitchen socket circuit
  room_hall_1f: 'family', // the first-floor family hall
};

function same(id: string): string | undefined {
  for (const candidate of [id, SAME[id]]) if (candidate && ids.has(candidate)) return candidate;
  if (id.startsWith('mcb_')) return same(`circ_${id.slice(4)}`); // a breaker: its circuit
  if (id.startsWith('rcd_wh_')) return same(`circ_wh_${id.slice(7)}`); // a heater's safety switch: its circuit
  if (id.startsWith('room_')) return same(id === 'room_bed_master' ? 'master' : id.slice(5)); // rooms: no prefix
  return undefined;
}

const ELECTRICAL = /^(mcb|rcd|circ|db|sock)_/;
const WATER_MAINS = /^(pipe_(supply|rising)|valve_main|meter)_?/;

/** The 3D model's IDs for a focus, with the closest stand-in for parts the model doesn't have. */
export function toSpecIds(objectIds: string[]): string[] {
  const out: string[] = [];
  for (const id of objectIds) {
    const found = same(id) ?? (ELECTRICAL.test(id) ? 'db_main' : WATER_MAINS.test(id) ? 'meter_water_main' : undefined);
    if (found && ids.has(found) && !out.includes(found)) out.push(found);
  }
  return out;
}

export function toSpecFocus<T extends FocusCommand | undefined>(focus: T): T {
  if (!focus) return focus;
  const mapped = toSpecIds(focus.object_ids);
  return { ...focus, object_ids: mapped.length ? mapped : focus.object_ids };
}
