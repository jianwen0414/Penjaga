// When a case turns urgent, Penjaga doesn't wait to be asked: it says what's wrong, asks where the owner is, and
// walks them to the one thing that makes it safe (the main stop valve, the switchboard). This works out that
// "one thing" from the case itself, never from a script:
//   - what to do there is the case's own reviewed safety guidance, word for word (only the KB writes safety text);
//   - where it is comes from the house drawings (the device and its room, src/nav/route.ts).
// A case with no reviewed guidance, or guidance that doesn't name a place to go, gets no route: Penjaga still says
// what's wrong and shows the safety steps, it just doesn't invent a destination.
import type { Case } from '../api/types';
import { deviceSpot, the } from '../nav/route';

export interface EmergencyPlan {
  caseId: string;
  /** What Penjaga says first, then "where are you right now?". */
  alarm: string;
  /** Where to go: a device in the drawings, or a room. */
  target: { device: string } | { room: string };
  /** The destination as named in speech and on the 3D view. */
  label: string;
  /** Where it is, in words ("next to the water meter, in the car porch"). */
  where: string;
  /** The reviewed safety line that says what to do there (verbatim), and the others after it. */
  action: string;
  after: string[];
  /** What Penjaga asks once they're on their way. */
  ask: string;
  /** For the demo house only: what the simulated owner does when they say it's done (see remote.ts). */
  sim?: { close_valves?: string[] };
  /** A reading that shows it worked, watched after they say it's done. */
  verify?: 'water';
}

const ACTIVE = new Set(['open', 'monitoring', 'action_pending']);
/** Cases that should start an emergency conversation. */
export const isEmergency = (c: Case): boolean => c.severity === 'urgent' && ACTIVE.has(c.status) && !c.guided?.done;

const VALVE = /main stop valve|stop valve|water supply/i;
const BOARD = /breaker|distribution board|switchboard|\bboard\b|safety switch|\bRCC?B\b|\bRCD\b/i;
const UNPLUG = /^unplug/i;

/** What to do for an urgent case, or null when the guidance names no place to go. */
export function planFor(c: Case, roomOf: (id: string) => string | null): EmergencyPlan | null {
  const lines = c.safety ?? [];
  const pick = (re: RegExp) => lines.findIndex((l) => re.test(l));
  const alarm = `${c.title.replace(/\.$/, '')}.`;
  const rest = (i: number) => lines.filter((_, k) => k !== i);
  // water: the main stop valve, by the meter
  let i = pick(VALVE);
  if (i >= 0 && c.layer !== 'L2') {
    const spot = deviceSpot('valve_main_stop');
    if (!spot) return null;
    return {
      caseId: c.id, alarm, target: { device: 'valve_main_stop' }, label: 'Main stop valve',
      where: `next to the water meter, in ${the(spot.room)}`,
      action: lines[i], after: rest(i), ask: "Tell me when it's closed.",
      sim: { close_valves: ['valve_main_stop'] }, verify: 'water',
    };
  }
  // electrical: unplugging comes first when the guidance says so (an overloaded circuit): go to its room
  i = pick(UNPLUG);
  if (i === 0 && c.layer === 'L2') {
    const room = c.focus.object_ids.map(roomOf).find(Boolean)?.split(':')[1];
    if (room) return { caseId: c.id, alarm, target: { room }, label: c.focus.label ?? 'The circuit', where: `in ${the(room)}`, action: lines[i], after: rest(i), ask: "Tell me when you've done it." };
  }
  // electrical: the switchboard, where the breakers and the safety switch are
  // (the line that says to switch something off at the board, else the first that mentions the board at all)
  i = lines.findIndex((l) => BOARD.test(l) && /\b(switch|turn)(ed)? (it )?off\b|\bleave it off\b/i.test(l));
  if (i < 0) i = pick(BOARD);
  if (i >= 0 && c.layer === 'L2') {
    const spot = deviceSpot('db_main');
    if (!spot) return null;
    const front = spot.room === 'foyer' ? ', by the front door' : '';
    return {
      caseId: c.id, alarm, target: { device: 'db_main' }, label: 'Switchboard',
      where: `on the wall in ${the(spot.room)}${front}`,
      action: lines[i], after: rest(i), ask: "Tell me when you're there.",
    };
  }
  return null;
}

/** "Yes", "done", "it's off", "okay I closed it" (and Malay) vs "I can't find it" vs "not yet". */
export function answerKind(said: string): 'done' | 'lost' | 'wait' | 'unknown' {
  const t = ` ${said.toLowerCase().replace(/[^a-z0-9\s']/g, ' ').replace(/\s+/g, ' ').trim()} `;
  if (/\b(can ?not|can't|cannot|couldn't|could not|don't|do not|tak|tidak) (find|see|reach|open|close|turn|switch|jumpa|nampak)\b|\bwhere is it\b|\bwhere\b.*\b(valve|board|switch)\b|\bstuck\b|\bhelp\b/.test(t)) return 'lost';
  if (/\b(not yet|wait|hold on|one moment|give me a (second|minute)|on my way|going now|belum|kejap|tunggu)\b/.test(t)) return 'wait';
  if (/\b(done|did it|it's off|its off|it is off|turned (it )?off|switched (it )?off|closed( it)?|shut( it)?( off)?|off now|yes|yeah|yep|okay|ok|got it|i'm there|im there|i am there|here|sudah|dah|siap|ya)\b/.test(t)) return 'done';
  return 'unknown';
}
