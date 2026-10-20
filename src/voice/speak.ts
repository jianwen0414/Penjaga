// Voice out: Penjaga says a message aloud. Google Cloud Text-to-Speech through the Core API (POST /api/v1/voice/speech,
// the key stays on the backend), one or two sentences per request so the first plays while the next is made. The
// audio runs through an analyser so the character's mouth follows the voice. When the Core API has no voice (no key,
// the stand-in backend, offline), the browser's own voice says it instead, and the mouth follows its word timings.
//
// One speaker for the app: a new message interrupts the last one. Browsers only play sound after the person has
// interacted with the page; until then say() resolves 'blocked' and the app shows the words instead.

type Outcome = 'done' | 'cancelled' | 'blocked' | 'failed';
type State = { speaking: boolean; engine: 'cloud' | 'browser' | null; blocked: boolean };

const ENDPOINT = '/api/v1/voice/speech';
const MAX_CHUNK = 260; // characters per request: a sentence or two

const listeners = new Set<() => void>();
let state: State = { speaking: false, engine: null, blocked: false };
const set = (next: Partial<State>) => { state = { ...state, ...next }; listeners.forEach((f) => f()); };
export const subscribe = (f: () => void) => { listeners.add(f); return () => { listeners.delete(f); }; };
export const snapshot = (): State => state;

// ---------------------------------------------------------------- audio graph

let ctx: AudioContext | null = null;
let analyser: AnalyserNode | null = null;
let out: GainNode | null = null;
function audio(): AudioContext | null {
  if (ctx) return ctx;
  try {
    ctx = new AudioContext();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.2;
    out = ctx.createGain();
    out.connect(analyser);
    analyser.connect(ctx.destination);
  } catch {
    ctx = null;
  }
  return ctx;
}

/** Browsers hold sound back until the page has been used; call on any tap or key so Penjaga can speak later,
 * including when it starts the conversation itself (an emergency). */
export function unlock(): void {
  const c = audio();
  if (c && c.state !== 'running') void c.resume().then(() => { if (state.blocked) set({ blocked: false }); }).catch(() => {});
  // the browser voice needs the same gesture once: an empty utterance does it
  if (typeof speechSynthesis !== 'undefined' && !unlockedBrowserVoice) {
    unlockedBrowserVoice = true;
    try { const u = new SpeechSynthesisUtterance(''); u.volume = 0; speechSynthesis.speak(u); } catch { /* no voice */ }
  }
}
let unlockedBrowserVoice = false;
if (typeof window !== 'undefined') {
  const once = () => unlock();
  for (const ev of ['pointerdown', 'keydown', 'touchend'] as const) window.addEventListener(ev, once, { capture: true, passive: true });
}

// ---------------------------------------------------------------- loudness for the mouth

const buf = new Float32Array(1024);
let browserLevel = 0;
let browserPulseAt = 0;
let smoothed = 0;
let lastRead = 0;
/** How loud Penjaga's voice is right now, 0..1, smoothed like a jaw would follow it. Cheap: call every frame. */
export function level(): number {
  const now = performance.now();
  if (now - lastRead < 8) return smoothed;
  lastRead = now;
  let raw = 0;
  if (state.engine === 'cloud' && analyser) {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    raw = Math.min(1, Math.max(0, (20 * Math.log10(rms + 1e-6) + 50) / 36)); // -50 dB..-14 dB → 0..1
  } else if (state.engine === 'browser' && state.speaking) {
    // the browser voice gives no audio to measure: each word opens the mouth, which then settles, with a little
    // syllable flutter on top so it never looks mechanical
    const since = (now - browserPulseAt) / 1000;
    const syll = 0.5 + 0.5 * Math.sin(now / 62) * Math.sin(now / 97);
    raw = Math.max(0, browserLevel * Math.exp(-since * 5.5)) * (0.55 + 0.45 * syll);
  }
  smoothed += (raw - smoothed) * (raw > smoothed ? 0.55 : 0.2);
  return smoothed;
}

// ---------------------------------------------------------------- what to say

/** Text as it should be heard: no markdown, emoji or bracketed asides; long text in sentence-sized pieces. */
export function chunks(text: string): string[] {
  const clean = text
    .replace(/\*\*|__|`/g, '')
    .replace(/\[(.*?)\]\((.*?)\)/g, '$1')
    .replace(/\p{Extended_Pictographic}/gu, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!clean) return [];
  // sentence ends are punctuation followed by a space, so decimals (0.5 L) and times (9.30) stay whole
  const sentences = clean.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
  const out: string[] = [];
  for (const s of sentences) {
    const pieces = s.length <= MAX_CHUNK ? [s] : splitLong(s);
    for (const p of pieces) {
      const last = out[out.length - 1];
      // join very short sentences ("Okay.") to the next, but keep the first one short so speech starts fast
      if (last && out.length > 1 && last.length + p.length + 1 <= MAX_CHUNK && last.length < 60) out[out.length - 1] = `${last} ${p}`;
      else out.push(p);
    }
  }
  return out;
}
function splitLong(s: string): string[] {
  const out: string[] = [];
  let rest = s;
  while (rest.length > MAX_CHUNK) {
    const cut = Math.max(rest.lastIndexOf(', ', MAX_CHUNK), rest.lastIndexOf('; ', MAX_CHUNK), rest.lastIndexOf(' ', MAX_CHUNK));
    const at = cut > 40 ? cut + 1 : MAX_CHUNK;
    out.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) out.push(rest);
  return out;
}

// ---------------------------------------------------------------- speaking

let cloud: 'unknown' | 'yes' | 'no' = 'unknown';
let cloudRetryAt = 0;
let run = 0;
let current: { stop(): void } | null = null;

async function fetchAudio(text: string, signal: AbortSignal): Promise<ArrayBuffer> {
  const r = await fetch(ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }), signal });
  if (!r.ok) throw Object.assign(new Error(`speech ${r.status}`), { status: r.status });
  return r.arrayBuffer();
}

/** Stop talking now. */
export function cancel(): void {
  run++;
  current?.stop();
  current = null;
  if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel();
  if (state.speaking) set({ speaking: false, engine: null });
}

export interface SayOptions {
  /** Use the browser voice even if the cloud voice is available (e.g. no backend). */
  browserOnly?: boolean;
  onStart?: () => void;
}

/** Say `text` aloud; resolves when it has been said (or why not). Interrupts anything still being said. */
export async function say(text: string, opts: SayOptions = {}): Promise<Outcome> {
  cancel();
  const me = ++run;
  const parts = chunks(text);
  if (!parts.length) return 'done';
  const useCloud = !opts.browserOnly && (cloud !== 'no' || performance.now() > cloudRetryAt);
  if (useCloud) {
    const r = await sayCloud(parts, me, opts);
    if (r !== 'failed') return r;
    if (me !== run) return 'cancelled';
  }
  return sayBrowser(parts, me, opts);
}

async function sayCloud(parts: string[], me: number, opts: SayOptions): Promise<Outcome> {
  const c = audio();
  if (!c || !out) return 'failed';
  const running = () => c.state === 'running'; // read fresh: resume() changes it
  if (!running()) {
    try { await Promise.race([c.resume(), new Promise((_, no) => setTimeout(no, 400))]); } catch { /* still suspended */ }
    if (!running()) { set({ blocked: true }); return 'blocked'; }
  }
  const abort = new AbortController();
  let source: AudioBufferSourceNode | null = null;
  current = { stop: () => { abort.abort(); try { source?.stop(); } catch { /* not started */ } } };
  // fetch two ahead: the next sentence is ready by the time this one ends
  const pending = parts.map(() => null as Promise<AudioBuffer> | null);
  const load = (i: number) => {
    if (i >= parts.length || pending[i]) return;
    pending[i] = fetchAudio(parts[i], abort.signal).then((b) => c.decodeAudioData(b));
    pending[i]!.catch(() => {}); // handled where awaited
  };
  load(0); load(1);
  try {
    for (let i = 0; i < parts.length; i++) {
      load(i + 1); load(i + 2);
      const buffer = await pending[i]!;
      if (me !== run) return 'cancelled';
      cloud = 'yes';
      if (i === 0) { set({ speaking: true, engine: 'cloud', blocked: false }); opts.onStart?.(); }
      await new Promise<void>((resolve) => {
        source = c.createBufferSource();
        source.buffer = buffer;
        source.connect(out!);
        source.onended = () => resolve();
        source.start();
      });
      if (me !== run) return 'cancelled';
    }
    set({ speaking: false, engine: null });
    return 'done';
  } catch (err) {
    if (me !== run) return 'cancelled';
    set({ speaking: false, engine: null });
    const status = (err as { status?: number }).status;
    // no voice on the backend (503, the stand-in's 404): use the browser voice for a while, then try again
    cloud = 'no';
    cloudRetryAt = performance.now() + (status === 503 || status === 404 ? 60_000 : 15_000);
    console.info('[penjaga] cloud voice unavailable, using the browser voice', status ?? err);
    return 'failed';
  } finally {
    if (me === run) current = null;
  }
}

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = speechSynthesis.getVoices();
  const score = (v: SpeechSynthesisVoice) =>
    (v.lang === 'en-GB' ? 6 : v.lang === 'en-AU' || v.lang === 'en-IN' ? 4 : v.lang.startsWith('en') ? 3 : 0)
    + (/natural|neural|online/i.test(v.name) ? 4 : 0)
    + (/female|libby|sonia|aria|jenny|hazel|susan|serena|google uk english female/i.test(v.name) ? 2 : 0);
  return voices.filter((v) => v.lang.startsWith('en')).sort((a, b) => score(b) - score(a))[0] ?? null;
}

function sayBrowser(parts: string[], me: number, opts: SayOptions): Promise<Outcome> {
  if (typeof speechSynthesis === 'undefined') return Promise.resolve('failed');
  return new Promise((resolve) => {
    const voice = pickVoice();
    let i = 0, started = false;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    const next = () => {
      clearTimeout(watchdog);
      if (me !== run) { resolve('cancelled'); return; }
      if (i >= parts.length) { set({ speaking: false, engine: null }); resolve('done'); return; }
      const said = parts[i];
      // Browser voices sometimes never report the end (a known Chrome bug with long text), or never start (no voice
      // installed): never let a conversation wait on them for longer than the sentence could take
      const words = said.split(/\s+/).length;
      watchdog = setTimeout(() => {
        if (me !== run) return;
        if (!started) { speechSynthesis.cancel(); set({ speaking: false, engine: null }); resolve('failed'); return; }
        next();
      }, started ? 2500 + words * 520 : 3000); // ~115 words a minute plus a margin; a voice that hasn't started in 3 s won't
      const u = new SpeechSynthesisUtterance(parts[i++]);
      if (voice) { u.voice = voice; u.lang = voice.lang; } else u.lang = 'en-GB';
      u.rate = 1.0;
      u.onstart = () => {
        if (!started) { started = true; set({ speaking: true, engine: 'browser', blocked: false }); opts.onStart?.(); }
        browserLevel = 0.9; browserPulseAt = performance.now();
      };
      u.onboundary = (e) => { if (e.name === 'word') { browserLevel = 0.6 + Math.random() * 0.4; browserPulseAt = performance.now(); } };
      u.onend = () => next();
      u.onerror = (e) => {
        if (me !== run || e.error === 'interrupted' || e.error === 'canceled') { resolve('cancelled'); return; }
        set({ speaking: false, engine: null, blocked: e.error === 'not-allowed' });
        resolve(e.error === 'not-allowed' ? 'blocked' : 'failed');
      };
      speechSynthesis.speak(u);
    };
    current = { stop: () => speechSynthesis.cancel() };
    next();
  });
}
