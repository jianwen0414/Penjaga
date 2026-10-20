// Voice in: the browser's speech recognition (Web Speech API: Chrome and Edge; Safari as webkitSpeechRecognition).
// The microphone is only ever open inside one call to listen(): the owner tapped the mic (or, in an emergency,
// Penjaga asked a question and waits for the answer), and it closes on its own after a pause, a time limit, or
// stop(). Nothing is recorded or stored here: the words come back as text and the audio is never kept.
//
// While it listens, a second, local tap on the same microphone measures how loud the voice is (for the character
// and the listening ring). It never leaves the browser and stops with the recognition.

type Alternative = { transcript: string };
type Result = { isFinal: boolean; length: number; [i: number]: Alternative };
type ResultEvent = Event & { resultIndex: number; results: { length: number; [i: number]: Result } };
type ErrorEvent = Event & { error: string };
interface Recognition extends EventTarget {
  lang: string; continuous: boolean; interimResults: boolean; maxAlternatives: number;
  start(): void; stop(): void; abort(): void;
  onresult: ((e: ResultEvent) => void) | null;
  onerror: ((e: ErrorEvent) => void) | null;
  onend: (() => void) | null;
  onspeechstart: (() => void) | null;
}
type RecognitionCtor = new () => Recognition;

function ctor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** Whether this browser can listen at all (Firefox can't; the mic button is hidden there). */
export const canListen = (): boolean => ctor() !== null && typeof window !== 'undefined' && window.isSecureContext;

/** Why listening ended: the owner finished speaking, nothing was said, they or the app stopped it, or it can't. */
export type ListenEnd = 'final' | 'no-speech' | 'stopped' | 'timeout' | 'denied' | 'unavailable' | 'error';

export interface ListenOptions {
  /** BCP-47 language for recognition. Malaysian English recognises best as en-GB in Chrome; en-US as a fallback. */
  lang?: string;
  /** How long the mic may stay open at most. */
  maxMs?: number;
  /** End this long after the last words, once something has been said. */
  silenceMs?: number;
  /** End if nothing at all is said within this time. */
  noSpeechMs?: number;
  /** The words so far: everything settled plus the guess still being made, as one string. */
  onWords?: (text: string, settled: string) => void;
  /** The voice's loudness, 0..1, about 60 times a second while listening (when the browser allows it). */
  onLevel?: (level: number) => void;
  /** The browser has started hearing speech. */
  onSpeech?: () => void;
}

export interface Listening {
  /** Finish now and keep what was said. */
  stop(): void;
  /** Finish now and throw it away. */
  abort(): void;
  readonly done: Promise<{ text: string; end: ListenEnd }>;
}

const FALLBACK_LANG = 'en-US';

export function listen(opts: ListenOptions = {}): Listening {
  const { lang = 'en-GB', maxMs = 20_000, silenceMs = 1_400, noSpeechMs = 7_000, onWords, onLevel, onSpeech } = opts;
  const Ctor = ctor();
  let resolve!: (r: { text: string; end: ListenEnd }) => void;
  const done = new Promise<{ text: string; end: ListenEnd }>((r) => { resolve = r; });
  if (!Ctor || !window.isSecureContext) {
    resolve({ text: '', end: 'unavailable' });
    return { stop() {}, abort() {}, done };
  }

  let settled = '';
  let guess = '';
  let finished = false;
  let discarding = false;
  let end: ListenEnd | null = null;
  let heard = false;
  let triedFallback = false;
  let rec: Recognition;
  const timers: ReturnType<typeof setTimeout>[] = [];
  let silence: ReturnType<typeof setTimeout> | undefined;
  const meter = levelMeter(onLevel);

  const text = () => tidy(`${settled} ${guess}`);
  const finish = (why: ListenEnd) => {
    if (finished) return;
    finished = true;
    timers.forEach(clearTimeout);
    clearTimeout(silence);
    meter.stop();
    const said = discarding ? '' : text();
    // words were heard: that's an answer, however it ended; none: say why
    resolve({ text: said, end: said && why !== 'denied' ? 'final' : !said && why === 'final' ? 'no-speech' : why });
  };
  // ask the recogniser to stop; it sends its last words, then `end`
  const wrapUp = (why: ListenEnd) => {
    if (finished) return;
    end ??= why;
    try { rec.stop(); } catch { finish(why); }
    timers.push(setTimeout(() => finish(why), 1_500)); // some browsers never send `end` after stop()
  };

  const start = (language: string) => {
    rec = new Ctor();
    rec.lang = language;
    rec.continuous = true; // pauses between words don't end it; the silence timer does
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.onspeechstart = () => onSpeech?.();
    rec.onresult = (e) => {
      let fin = '', interim = '';
      for (let i = 0; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) fin += ` ${r[0].transcript}`;
        else interim += ` ${r[0].transcript}`;
      }
      settled = tidy(fin);
      guess = tidy(interim);
      if (!heard && text()) { heard = true; onSpeech?.(); }
      onWords?.(text(), settled);
      clearTimeout(silence);
      if (text()) silence = setTimeout(() => wrapUp('final'), guess ? silenceMs + 600 : silenceMs);
    };
    rec.onerror = (e) => {
      if (e.error === 'language-not-supported' && !triedFallback && language !== FALLBACK_LANG) {
        triedFallback = true;
        rec.onend = null;
        try { rec.abort(); } catch { /* already stopped */ }
        start(FALLBACK_LANG);
        return;
      }
      if (e.error === 'no-speech') end ??= 'no-speech';
      else if (e.error === 'aborted') end ??= 'stopped';
      else if (e.error === 'not-allowed' || e.error === 'service-not-allowed') end = 'denied';
      else end ??= 'error';
    };
    rec.onend = () => finish(end ?? 'final');
    try {
      rec.start();
    } catch {
      finish('error');
    }
  };
  start(lang);
  timers.push(setTimeout(() => { if (!heard) { discarding = true; wrapUp('no-speech'); } }, noSpeechMs));
  timers.push(setTimeout(() => wrapUp('timeout'), maxMs));

  return {
    stop: () => wrapUp('stopped'),
    abort: () => {
      discarding = true;
      end ??= 'stopped';
      try { rec.abort(); } catch { /* not started */ }
      finish('stopped');
    },
    done,
  };
}

/** One clean line: single spaces, a capital first letter. */
function tidy(s: string): string {
  const t = s.replace(/\s+/g, ' ').trim();
  return t ? t[0].toUpperCase() + t.slice(1) : '';
}

// The loudness of the owner's voice while listening, for the visuals only. Fails quietly (no meter) if the browser
// won't share the microphone a second time.
function levelMeter(onLevel?: (level: number) => void): { stop(): void } {
  if (!onLevel || !navigator.mediaDevices?.getUserMedia) return { stop() {} };
  let stopped = false;
  let raf = 0;
  let stream: MediaStream | null = null;
  let ctx: AudioContext | null = null;
  navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    .then((s) => {
      if (stopped) { s.getTracks().forEach((t) => t.stop()); return; }
      stream = s;
      ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(s);
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      let smooth = 0;
      const tick = () => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        const rms = Math.sqrt(sum / buf.length);
        const level = Math.min(1, Math.max(0, (20 * Math.log10(rms + 1e-6) + 58) / 42)); // -58 dB..-16 dB → 0..1
        smooth += (level - smooth) * (level > smooth ? 0.5 : 0.12); // quick attack, slow release
        onLevel(smooth);
        raf = requestAnimationFrame(tick);
      };
      tick();
    })
    .catch(() => { /* no meter: the visuals just don't react to loudness */ });
  return {
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close().catch(() => {});
      onLevel(0);
    },
  };
}
