// Where the owner says they are ("I'm in the kitchen", "upstairs in bedroom two", "dekat dapur"), as a room of the 3D
// house. Deterministic: names and everyday words for each room (English and common Malay), with room numbers in
// words or digits, and the slips speech recognition makes ("bathroom to", "bedroom tree"). A word that could be
// several rooms ("the bathroom") comes back as a question with the choices; "I'm not home" is its own answer.
import spec from '../../house.spec.json';

export type Place =
  | { kind: 'room'; room: string; floor: 'GF' | 'FF'; name: string }
  | { kind: 'ambiguous'; options: string[]; what: string }
  | { kind: 'away' }
  | { kind: 'unknown' };

type Room = { id: string; floor: 'GF' | 'FF'; name: string };
const ROOMS = (spec as unknown as { rooms: Room[] }).rooms;
const roomById = new Map(ROOMS.map((r) => [r.id, r]));

// The names people use, most specific first within each room. Malay: ruang (room/area), bilik (room), bilik air (bathroom),
// dapur (kitchen), tangga (stairs), atas/bawah (upstairs/downstairs).
const ALIASES: Record<string, string[]> = {
  living: ['living room', 'living hall', 'living area', 'living', 'lounge', 'sitting room', 'tv room', 'sofa', 'ruang tamu', 'hall downstairs', 'the hall'],
  dining: ['dining room', 'dining area', 'dining table', 'dining', 'meja makan', 'ruang makan'],
  kitchen: ['wet kitchen', 'dry kitchen', 'kitchen', 'dapur', 'cooking'],
  foyer: ['foyer', 'front door', 'main door', 'entrance', 'entryway', 'shoe rack', 'pintu depan', 'pintu utama'],
  stair: ['bottom of the stairs', 'staircase', 'stairs', 'stairway', 'tangga'],
  bed4: ['flexi suite bedroom', 'flexi suite', 'flexi room', 'flexi', 'guest room', 'guest bedroom', 'downstairs bedroom', 'ground floor bedroom', 'bedroom downstairs', 'bedroom 4', 'mak s room', 'maks room', 'mum s room', 'mother s room', 'grandma s room', 'bilik tetamu', 'bilik bawah', 'bilik 4'],
  bath4: ['flexi suite bathroom', 'flexi bathroom', 'guest bathroom', 'guest toilet', 'downstairs bathroom', 'downstairs toilet', 'ground floor bathroom', 'ground floor toilet', 'bathroom downstairs', 'toilet downstairs', 'bathroom 4', 'toilet 4', 'bilik air bawah', 'tandas bawah'],
  yard: ['back yard', 'backyard', 'yard', 'laundry area', 'laundry', 'washing machine', 'utility area', 'drying area', 'back door', 'ampaian', 'belakang rumah'],
  porch: ['car porch', 'porch', 'garage', 'driveway', 'front gate', 'gate', 'front garden', 'garden', 'water meter', 'luar rumah', 'depan rumah'],
  terrace: ['front terrace', 'terrace', 'veranda', 'verandah', 'patio', 'beranda'],
  master: ['master bedroom', 'master room', 'main bedroom', 'bilik utama', 'bilik tidur utama', 'balcony'],
  bath_master: ['master bathroom', 'master bath', 'master toilet', 'en suite', 'ensuite', 'attached bathroom', 'bilik air utama'],
  family: ['family area', 'family room', 'family hall', 'upstairs hall', 'upstairs living', 'study area', 'study', 'ruang keluarga'],
  corridor: ['corridor', 'hallway', 'passage', 'lorong'],
  landing: ['top of the stairs', 'landing'],
  bed3: ['bedroom 3', 'third bedroom', 'bilik 3', 'bilik tiga'],
  bed2: ['bedroom 2', 'second bedroom', 'bilik 2', 'bilik dua'],
  bath3: ['bathroom 3', 'toilet 3', 'third bathroom', 'bilik air 3'],
  bath2: ['bathroom 2', 'toilet 2', 'second bathroom', 'bilik air 2'],
};

// One word that several rooms answer to: ask which.
const GENERIC: { words: string[]; options: string[]; what: string }[] = [
  { words: ['bathroom', 'toilet', 'washroom', 'restroom', 'loo', 'shower', 'bilik air', 'tandas'], options: ['bath_master', 'bath2', 'bath3', 'bath4'], what: 'bathroom' },
  { words: ['bedroom', 'room', 'bilik', 'bilik tidur'], options: ['master', 'bed2', 'bed3', 'bed4'], what: 'bedroom' },
  { words: ['upstairs', 'first floor', 'tingkat atas', 'atas'], options: ['master', 'family', 'bed2', 'bed3'], what: 'room upstairs' },
  { words: ['downstairs', 'ground floor', 'tingkat bawah', 'bawah'], options: ['living', 'dining', 'kitchen', 'bed4'], what: 'room downstairs' },
];

const AWAY = ['not home', 'not at home', 'not in the house', 'away from home', 'at work', 'at the office', 'in the office', 'overseas', 'on holiday', 'travelling', 'traveling', 'outstation', 'on the way', 'driving', 'out of the house', 'tak ada di rumah', 'tiada di rumah', 'bukan di rumah', 'kat luar', 'di luar', 'outside the house', 'at the mall', 'at school'];

const NUMBERS: Record<string, string> = { one: '1', two: '2', three: '3', four: '4', satu: '1', dua: '2', tiga: '3', empat: '4', first: '1', second: '2', third: '3', fourth: '4' };
// what recognition hears instead of a number after "room": "bathroom to", "bedroom too", "bedroom tree", "toilet for"
const HEARD_AS: Record<string, string> = { to: '2', too: '2', tu: '2', tree: '3', free: '3', for: '4', fore: '4', won: '1' };

export function normalise(said: string): string {
  let t = ` ${said.toLowerCase().replace(/[’']/g, ' ').replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim()} `;
  t = t.replace(/\b(one|two|three|four|satu|dua|tiga|empat)\b/g, (w) => NUMBERS[w]);
  // "bedroom to" → "bedroom 2" (only straight after a room word, where a number is expected)
  t = t.replace(/\b(bedroom|bathroom|toilet|room|bilik|bilik air)\s+(to|too|tu|tree|free|for|fore|won)\b/g, (_, a: string, b: string) => `${a} ${HEARD_AS[b]}`);
  // "the second bedroom" is handled by its alias; "room number 2" → "room 2"
  t = t.replace(/\bnumber\s+(\d)\b/g, '$1');
  return t.replace(/\s+/g, ' ');
}

const levenshtein = (a: string, b: string): number => {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
};

/** Does `phrase` occur in `text` as whole words, allowing one typo in words of six letters or more? */
function occurs(text: string, phrase: string): boolean {
  if (text.includes(` ${phrase} `)) return true;
  const want = phrase.split(' ');
  if (!want.some((w) => w.length >= 6)) return false;
  const words = text.trim().split(' ');
  outer: for (let i = 0; i + want.length <= words.length; i++) {
    for (let k = 0; k < want.length; k++) {
      const a = words[i + k], b = want[k];
      if (a === b) continue;
      if (b.length >= 6 && Math.abs(a.length - b.length) <= 1 && levenshtein(a, b) <= 1) continue;
      continue outer;
    }
    return true;
  }
  return false;
}

/** The room the owner says they're in, a question to ask back, "away", or unknown. */
export function placeFrom(said: string): Place {
  const t = normalise(said);
  if (!t.trim()) return { kind: 'unknown' };
  // the longest (most specific) matching name wins: "master bathroom" over "bathroom", "bedroom 2" over "bedroom"
  let best: { room: string; len: number } | null = null;
  for (const [room, names] of Object.entries(ALIASES)) {
    for (const name of names) {
      if (name.length > (best?.len ?? 0) && occurs(t, name)) best = { room, len: name.length };
    }
  }
  // "I'm not home" is a clear answer on its own, whatever else is mentioned ("not home, I left the kitchen tap on")
  if (AWAY.some((p) => occurs(t, p))) return { kind: 'away' };
  if (best) {
    const r = roomById.get(best.room);
    if (r) return { kind: 'room', room: r.id, floor: r.floor, name: r.name };
  }
  for (const g of GENERIC) if (g.words.some((w) => occurs(t, w))) return { kind: 'ambiguous', options: g.options, what: g.what };
  return { kind: 'unknown' };
}

export const roomName = (id: string): string => roomById.get(id)?.name ?? id;
