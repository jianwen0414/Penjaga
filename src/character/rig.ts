// The brain behind the living character: every frame it turns what Penjaga is doing (its mood, listening, speaking,
// asleep) into a pose. Nothing loops on a fixed timer, which is what makes a character look mechanical:
//   - motion runs through springs, so it eases in, overshoots a touch and settles (follow-through);
//   - idle motion is smooth noise from several unrelated frequencies, so it never visibly repeats;
//   - blinks come at irregular intervals, sometimes doubled, and with big glances (as people blink);
//   - the eyes make quick saccades between resting points, or follow the pointer when it moves;
//   - breathing squashes and stretches the body around its base (volume kept), slower when asleep;
//   - moods blend over a few hundred milliseconds instead of swapping faces;
//   - the mouth follows the loudness of the voice, with a vowel shape that changes every syllable or so;
//   - events (waking up, starting to listen, a happy hop, a worried shiver, a nod on a stressed word) are impulses
//     added to the springs, so they mix with whatever else is going on.
// Under reduced motion the body holds still; the face still blinks, looks and talks (that carries meaning).

export type Mood = 'calm' | 'alert' | 'concerned' | 'thinking' | 'happy' | 'sleeping';
export type Activity = 'idle' | 'listening' | 'speaking' | 'thinking';

export interface RigInput {
  mood: Mood;
  activity: Activity;
  /** Penjaga's own voice, 0..1 (drives the mouth). */
  voice: number;
  /** The owner's voice while listening, 0..1. */
  mic: number;
  /** Where to look, -1..1 on each axis (x right, y down), or null to look around on its own. */
  look: { x: number; y: number } | null;
  reduceMotion: boolean;
}

export interface Pose {
  x: number; y: number; rot: number; sx: number; sy: number;
  gazeX: number; gazeY: number;
  eyeOpen: number; eyeWide: number; lid: number; // lid: +1 happy arcs (∩), -1 sleepy lids (∪)
  browShow: number; browLift: number; browWorry: number;
  mouthOpen: number; mouthWidth: number; mouthSmile: number; mouthRound: number; mouthSkew: number; mouthY: number;
  cheek: number;
  lamp: [number, number, number]; lampGlow: number; halo: number;
  shadow: number;
}

// ---------------------------------------------------------------- building blocks

/** A damped spring towards a moving target: frequency in Hz, damping ratio (1 = no overshoot). */
class Spring {
  v = 0;
  constructor(public x = 0, public hz = 3, public zeta = 0.7) {}
  step(target: number, dt: number, hz = this.hz, zeta = this.zeta): number {
    const w = 2 * Math.PI * hz;
    // semi-implicit Euler in small sub-steps: stable at 30 or 144 frames a second
    const n = Math.max(1, Math.ceil(dt / (1 / 240)));
    const h = dt / n;
    for (let i = 0; i < n; i++) {
      const a = w * w * (target - this.x) - 2 * zeta * w * this.v;
      this.v += a * h;
      this.x += this.v * h;
    }
    return this.x;
  }
  kick(velocity: number): void { this.v += velocity; }
}

/** Smooth noise in about -1..1 from sines whose periods share no common multiple. */
function noise(t: number, seed: number): number {
  return (
    Math.sin(t * 0.731 + seed * 1.7) * 0.5 +
    Math.sin(t * 1.379 + seed * 4.1) * 0.3 +
    Math.sin(t * 2.851 + seed * 2.3) * 0.2
  );
}

const rand = (a: number, b: number) => a + Math.random() * (b - a);
const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const mix = (a: number, b: number, k: number) => a + (b - a) * k;

// ---------------------------------------------------------------- what each mood looks like

interface Face {
  eyeWide: number; lid: number; open: number; browShow: number; browLift: number; browWorry: number;
  smile: number; width: number; mouthOpen: number; round: number; skew: number;
  cheek: number; lamp: [number, number, number]; glow: number; breathHz: number; breath: number; bob: number;
}
const LAMP: Record<Mood, [number, number, number]> = {
  calm: [91, 209, 139], happy: [91, 209, 139], alert: [242, 163, 58], concerned: [229, 72, 77], thinking: [79, 163, 224], sleeping: [138, 154, 150],
};
const FACES: Record<Mood, Face> = {
  calm: { eyeWide: 1, lid: -0.4, open: 1, browShow: 0, browLift: 0, browWorry: 0, smile: 0.55, width: 16, mouthOpen: 0, round: 0, skew: 0, cheek: 0.75, lamp: LAMP.calm, glow: 0.35, breathHz: 0.27, breath: 1, bob: 1 },
  happy: { eyeWide: 1, lid: 1, open: 0, browShow: 0, browLift: 0.2, browWorry: 0, smile: 1, width: 22, mouthOpen: 15, round: 0, skew: 0, cheek: 1, lamp: LAMP.happy, glow: 0.55, breathHz: 0.3, breath: 1.1, bob: 1.2 },
  alert: { eyeWide: 1.18, lid: -0.4, open: 1, browShow: 1, browLift: 0.6, browWorry: 0, smile: 0, width: 8, mouthOpen: 9, round: 1, skew: 0, cheek: 0.55, lamp: LAMP.alert, glow: 0.6, breathHz: 0.36, breath: 1, bob: 0.7 },
  concerned: { eyeWide: 1.05, lid: -0.4, open: 1, browShow: 1, browLift: 0.15, browWorry: 1, smile: -0.7, width: 15, mouthOpen: 0, round: 0, skew: 0, cheek: 0.4, lamp: LAMP.concerned, glow: 0.7, breathHz: 0.42, breath: 1.2, bob: 0.5 },
  thinking: { eyeWide: 0.92, lid: -0.4, open: 1, browShow: 0.5, browLift: 0.35, browWorry: -0.3, smile: 0.2, width: 13, mouthOpen: 0, round: 0, skew: 0.8, cheek: 0.6, lamp: LAMP.thinking, glow: 0.5, breathHz: 0.25, breath: 0.8, bob: 0.6 },
  sleeping: { eyeWide: 1, lid: -1, open: 0, browShow: 0, browLift: -0.2, browWorry: 0, smile: 0.25, width: 9, mouthOpen: 0, round: 0, skew: 0, cheek: 0.85, lamp: LAMP.sleeping, glow: 0.12, breathHz: 0.2, breath: 2.2, bob: 0.25 },
};

// ---------------------------------------------------------------- the rig

export type Burst = { kind: 'z' | 'bubble' | 'spark' | 'note'; born: number; seed: number };

export class Rig {
  private t = rand(0, 100);
  private seed = rand(0, 10);
  // body
  private y = new Spring(0, 2.2, 0.55);
  private rot = new Spring(0, 2.4, 0.5);
  private sy = new Spring(1, 3.4, 0.38);
  private sx = new Spring(1, 3.4, 0.38);
  // eyes
  private gx = new Spring(0, 9, 0.85);
  private gy = new Spring(0, 9, 0.85);
  private lidOpen = new Spring(1, 14, 0.9);
  private wide = new Spring(1, 6, 0.7);
  private lidShape = new Spring(-0.4, 4, 0.9);
  // face
  private brow = { show: new Spring(0, 5, 0.9), lift: new Spring(0, 7, 0.55), worry: new Spring(0, 5, 0.9) };
  private mouth = { open: new Spring(0, 11, 0.62), width: new Spring(16, 7, 0.8), smile: new Spring(0.55, 5, 0.85), round: new Spring(0, 8, 0.8), skew: new Spring(0, 4, 0.9) };
  private cheek = new Spring(0.75, 3, 1);
  private lamp: [number, number, number] = [...LAMP.calm];
  private glow = new Spring(0.35, 4, 0.9);
  private breathPhase = rand(0, Math.PI * 2);
  // scheduling
  private nextBlink = rand(1, 3);
  private blinkUntil = 0;
  private secondBlinkAt = 0;
  private nextSaccade = rand(0.5, 2);
  private gazeTarget = { x: 0, y: 0 };
  private nextVowel = 0;
  private vowel = { width: 0, round: 0 };
  private lastVoice = 0;
  private lastNod = 0;
  private nextIdleBeat = rand(6, 12);
  private prev: { mood: Mood; activity: Activity } | null = null;
  private wakeUntil = 0;
  bursts: Burst[] = [];
  private nextBurst = 0;

  /** One frame. `dt` in seconds. */
  update(dt: number, inp: RigInput): Pose {
    dt = Math.min(Math.max(dt, 0), 0.1); // a stalled tab doesn't make it jump; a frame stamped early is no step
    const t = (this.t += dt);
    const still = inp.reduceMotion;
    const face = FACES[inp.mood];
    const asleep = inp.mood === 'sleeping' && inp.activity === 'idle';
    this.events(inp, t);

    // ---- breathing: squash and stretch about the base, slower and deeper asleep
    this.breathPhase += dt * Math.PI * 2 * face.breathHz * (1 + 0.08 * noise(t * 0.3, this.seed));
    const breath = still ? 0 : Math.sin(this.breathPhase) * 0.011 * face.breath;
    const listening = inp.activity === 'listening';
    const speaking = inp.activity === 'speaking';
    const thinking = inp.activity === 'thinking' || inp.mood === 'thinking';
    const lean = listening ? 0.035 : 0; // leaning in to hear
    const sy = this.sy.step(1 + breath + lean, dt);
    const sx = this.sx.step(1 - breath * 0.55 - lean * 0.35, dt);

    // ---- drifting: a slow float and a sway that never repeats
    const bobAmp = still ? 0 : 7 * face.bob * (speaking ? 0.6 : 1);
    const y = this.y.step(noise(t * 0.55, this.seed) * bobAmp - (listening ? 4 : 0), dt);
    const swayTarget = still ? 0 : noise(t * 0.4, this.seed + 3) * 1.6 + this.gx.x * 1.6 + (asleep ? 4 + noise(t * 0.2, 7) : 0);
    const rot = this.rot.step(swayTarget, dt);

    // ---- eyes: where they look
    if (inp.look && !asleep) {
      this.gazeTarget = { x: clamp(inp.look.x, -1, 1), y: clamp(inp.look.y, -1, 1) };
      this.nextSaccade = t + rand(0.8, 2);
    } else if (t >= this.nextSaccade) {
      // looking around on its own: mostly near the middle, now and then further; speaking and listening keep
      // eye contact with the viewer, thinking looks up and away
      const contact = listening || speaking;
      const far = !contact && Math.random() < 0.25;
      this.gazeTarget = thinking && !contact
        ? { x: rand(0.45, 0.9), y: rand(-0.85, -0.45) }
        : { x: rand(-1, 1) * (contact ? 0.18 : far ? 0.9 : 0.45), y: rand(-1, 1) * (contact ? 0.12 : far ? 0.6 : 0.3) };
      this.nextSaccade = t + (contact ? rand(0.9, 2.4) : rand(0.5, 2.8));
      if (Math.abs(this.gazeTarget.x - this.gx.x) > 0.7 && Math.random() < 0.5) this.blink(t); // big glances come with a blink
    }
    const micro = still ? 0 : 0.035;
    const gazeX = this.gx.step(this.gazeTarget.x + noise(t * 7.3, 11) * micro, dt);
    const gazeY = this.gy.step(this.gazeTarget.y + noise(t * 6.1, 13) * micro, dt);

    // ---- blinking: irregular, sometimes twice; never while asleep or happy-closed
    if (t >= this.nextBlink) {
      this.blink(t);
      this.nextBlink = t + (Math.random() < 0.15 ? rand(0.25, 0.4) : rand(2.2, 6.5) * (listening ? 1.4 : 1));
    }
    if (this.secondBlinkAt && t >= this.secondBlinkAt) { this.secondBlinkAt = 0; this.blinkUntil = t + 0.11; }
    const blinking = t < this.blinkUntil;
    const waking = t < this.wakeUntil;
    let openTarget = face.open;
    if (inp.activity === 'listening' || inp.activity === 'speaking') openTarget = Math.max(openTarget, inp.mood === 'happy' && speaking ? 0 : 1);
    if (waking) openTarget = 0.35 + 0.65 * clamp((t - (this.wakeUntil - 0.9)) / 0.9); // heavy lids opening
    if (blinking && openTarget > 0.2) openTarget = 0;
    const eyeOpen = this.lidOpen.step(openTarget, dt, blinking ? 22 : 9, 0.95);
    const eyeWide = this.wide.step(face.eyeWide * (listening ? 1.12 : 1) * (1 + inp.mic * 0.06), dt);
    const lid = this.lidShape.step(blinking ? -0.5 : face.lid, dt);

    // ---- brows: listening lifts them a little; loud stressed words lift them more
    const browShow = this.brow.show.step(Math.max(face.browShow, listening ? 0.85 : 0), dt);
    const browLift = this.brow.lift.step(face.browLift + (listening ? 0.45 : 0) + (speaking ? inp.voice * 0.35 : 0), dt);
    const browWorry = this.brow.worry.step(face.browWorry, dt);

    // ---- mouth: the face's shape, opened by the voice; the vowel changes every 90-170 ms while talking
    let open = face.mouthOpen, width = face.width, smile = face.smile, round = face.round;
    let skew = thinking && !speaking ? face.skew || 0.8 : face.skew;
    if (speaking) {
      if (t >= this.nextVowel) {
        this.vowel = { width: rand(-5, 6), round: Math.random() < 0.3 ? rand(0.3, 0.8) : 0 };
        this.nextVowel = t + rand(0.09, 0.17);
      }
      const v = Math.pow(clamp(inp.voice), 0.7);
      open = Math.max(open * 0.4, 2 + v * 24);
      width = Math.max(9, face.width + this.vowel.width - v * 3 + (inp.mood === 'happy' ? 4 : 0));
      round = Math.max(round * 0.5, this.vowel.round * v);
      smile = Math.max(-0.3, smile * 0.7);
      skew = 0;
      // a nod on a stressed syllable: the voice jumps up after a quiet moment
      if (!still && inp.voice - this.lastVoice > 0.28 && t - this.lastNod > 0.6) {
        this.lastNod = t;
        this.rot.kick(rand(-45, 45));
        this.y.kick(-90);
      }
    } else if (listening) {
      open = Math.max(open, 1.5 + inp.mic * 4); // lips parted, attentive
      smile = Math.max(smile, 0.35);
      skew = 0;
    }
    if (waking) { open = Math.max(open, 18 * Math.sin(clamp((t - (this.wakeUntil - 1.4)) / 1.1) * Math.PI)); round = 0.7; } // a yawn
    this.lastVoice = inp.voice;
    const mouthOpen = Math.max(0, this.mouth.open.step(open, dt));
    const mouthWidth = this.mouth.width.step(width, dt);
    const mouthSmile = this.mouth.smile.step(smile, dt);
    const mouthRound = this.mouth.round.step(round, dt);
    const mouthSkew = this.mouth.skew.step(skew, dt);

    // ---- the chimney lamp: the mood's colour, brighter with the voice; it pulses when something is wrong
    for (let i = 0; i < 3; i++) this.lamp[i] = mix(this.lamp[i], face.lamp[i], 1 - Math.exp(-dt * 5));
    const urgentPulse = inp.mood === 'concerned' ? 0.5 + 0.5 * Math.sin(t * 6.5) : inp.mood === 'alert' ? 0.5 + 0.5 * Math.sin(t * 3.6) : 0;
    const glow = this.glow.step(face.glow + inp.voice * 0.6 + inp.mic * 0.5 + (still ? 0 : urgentPulse * 0.35), dt);
    const halo = clamp((inp.mood === 'alert' || inp.mood === 'concerned' ? 0.25 + urgentPulse * 0.5 : 0) + inp.voice * 0.4 + inp.mic * 0.4);
    const cheek = this.cheek.step(face.cheek + (speaking ? 0.1 : 0), dt);

    this.particles(t, inp, asleep, thinking);
    return {
      x: 0, y, rot, sx, sy, gazeX, gazeY, eyeOpen: clamp(eyeOpen), eyeWide, lid,
      browShow: clamp(browShow), browLift, browWorry,
      mouthOpen, mouthWidth, mouthSmile, mouthRound: clamp(mouthRound), mouthSkew, mouthY: 0,
      cheek: clamp(cheek), lamp: [this.lamp[0], this.lamp[1], this.lamp[2]], lampGlow: clamp(glow), halo,
      shadow: 1 - clamp(-y / 40, -0.3, 0.5),
    };
  }

  private blink(t: number): void {
    if (t < this.blinkUntil) return;
    this.blinkUntil = t + rand(0.1, 0.15);
    if (Math.random() < 0.12) this.secondBlinkAt = t + rand(0.22, 0.3);
  }

  // Things that happen once, on a change: waking, starting to listen, a fix (happy), something wrong (concerned).
  private events(inp: RigInput, t: number): void {
    const prev = this.prev;
    this.prev = { mood: inp.mood, activity: inp.activity };
    const still = inp.reduceMotion;
    if (!prev) return;
    const wasAsleep = prev.mood === 'sleeping' && prev.activity === 'idle';
    const isAsleep = inp.mood === 'sleeping' && inp.activity === 'idle';
    if (wasAsleep && !isAsleep) {
      // waking up: heavy lids, a yawn, a stretch upwards, then a settle
      this.wakeUntil = t + 1.5;
      if (!still) { this.sy.kick(1.6); this.sx.kick(-0.8); this.y.kick(-320); }
      this.secondBlinkAt = t + 1.0;
    }
    if (inp.activity === 'listening' && prev.activity !== 'listening') {
      // perking up: a quick squash and a lift, and a blink as attention lands
      if (!still) { this.sy.kick(-0.9); this.sx.kick(0.6); this.y.kick(-180); }
      this.blink(t);
      this.gazeTarget = { x: 0, y: 0 };
    }
    if (inp.mood === 'happy' && prev.mood !== 'happy' && !still) { this.sy.kick(-1.2); this.y.kick(-520); }
    if (inp.mood === 'concerned' && prev.mood !== 'concerned' && !still) { this.rot.kick(60); this.wide.kick(3); }
    if (inp.mood === 'alert' && prev.mood !== 'alert' && !still) { this.y.kick(-260); this.wide.kick(2); }
    if (prev.activity === 'speaking' && inp.activity !== 'speaking') this.blink(t);
    // now and then, when nothing is going on: a little personality (a hop when happy, a shiver when worried)
    if (!still && t > this.nextIdleBeat && inp.activity === 'idle') {
      this.nextIdleBeat = t + rand(7, 14);
      if (inp.mood === 'happy') { this.sy.kick(-1); this.y.kick(-450); }
      else if (inp.mood === 'concerned') this.rot.kick(rand(-70, 70));
      else if (inp.mood !== 'sleeping') { this.gazeTarget = { x: rand(-0.9, 0.9), y: rand(-0.3, 0.3) }; this.nextSaccade = t + 1.4; }
    }
  }

  // z's while asleep, thought bubbles while thinking, a sparkle when happy
  private particles(t: number, inp: RigInput, asleep: boolean, thinking: boolean): void {
    this.bursts = this.bursts.filter((b) => t - b.born < 3.2);
    if (t < this.nextBurst) return;
    if (asleep) { this.bursts.push({ kind: 'z', born: t, seed: Math.random() }); this.nextBurst = t + rand(1.3, 2.1); }
    else if (thinking && inp.activity !== 'speaking') { this.bursts.push({ kind: 'bubble', born: t, seed: Math.random() }); this.nextBurst = t + rand(0.5, 0.8); }
    else if (inp.mood === 'happy' && inp.activity === 'idle') { this.bursts.push({ kind: 'spark', born: t, seed: Math.random() }); this.nextBurst = t + rand(1.6, 3); }
    else this.nextBurst = t + 0.3;
  }

  get time(): number { return this.t; }
}
