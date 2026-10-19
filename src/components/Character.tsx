// Penjaga as a character: the little house from public/assets/icons, drawn inline so its parts can move (eyes blink,
// the chimney lamp glows, thought bubbles rise, the sparkle twinkles, the z's drift). Same shapes and colours as the
// SVG files; "concerned" (urgent) has no file, so it follows character-sheet.png: red lamp, worried brows, a frown.
import '../styles/character.css';

export type Mood = 'calm' | 'alert' | 'concerned' | 'thinking' | 'happy' | 'sleeping';

/** The face a message wears for its tone. */
export function moodFor(tone?: string): Mood {
  if (tone === 'urgent') return 'concerned';
  if (tone === 'act_soon' || tone === 'heads_up') return 'alert';
  if (tone === 'ok') return 'happy';
  return 'calm';
}

const INK = '#12352E';
const PAPER = '#F6F1E7';
const LAMP: Record<Mood, string> = {
  calm: '#5BD18B', happy: '#5BD18B', alert: '#F2A33A', concerned: '#E5484D', thinking: '#4FA3E0', sleeping: '#8A9A96',
};
const line = { fill: 'none', stroke: INK, strokeWidth: 9, strokeLinecap: 'round' as const };

function OpenEyes({ x = 0, ry = 17 }: { x?: number; ry?: number }) {
  return (
    <g className="pc-eyes">
      <ellipse cx={216 + x} cy={292 - (x ? 6 : 0)} rx="13" ry={ry} fill={INK} /><circle cx={220 + x} cy={286 - (x ? 6 : 0)} r="4" fill="#fff" />
      <ellipse cx={296 + x} cy={292 - (x ? 6 : 0)} rx="13" ry={ry} fill={INK} /><circle cx={300 + x} cy={286 - (x ? 6 : 0)} r="4" fill="#fff" />
    </g>
  );
}

function Face({ mood }: { mood: Mood }) {
  switch (mood) {
    case 'alert':
      return (
        <>
          <OpenEyes ry={21} />
          <g className="pc-brows"><path d="M198 258 Q216 248 234 258" {...line} /><path d="M278 258 Q296 248 314 258" {...line} /></g>
          <ellipse cx="256" cy="338" rx="8" ry="10" fill={INK} />
        </>
      );
    case 'concerned':
      return (
        <>
          <OpenEyes />
          <g className="pc-brows"><path d="M198 262 L232 250" {...line} /><path d="M280 250 L314 262" {...line} /></g>
          <path d="M240 342 Q256 328 272 342" {...line} />
        </>
      );
    case 'thinking':
      return (
        <>
          <g className="pc-look"><OpenEyes x={6} ry={15} /></g>
          <path d="M244 334 Q258 340 270 330" {...line} />
          <circle className="pc-b1" cx="400" cy="214" r="9" fill={PAPER} />
          <circle className="pc-b2" cx="424" cy="182" r="13" fill={PAPER} />
        </>
      );
    case 'happy':
      return (
        <>
          <path d="M202 296 Q216 278 230 296" {...line} /><path d="M282 296 Q296 278 310 296" {...line} />
          <path d="M234 322 Q256 354 278 322 Z" fill={INK} />
          <path className="pc-spark" d="M406 176 L412 192 L428 198 L412 204 L406 220 L400 204 L384 198 L400 192 Z" fill="#F2A33A" />
        </>
      );
    case 'sleeping':
      return (
        <>
          <path d="M202 292 Q216 302 230 292" {...line} /><path d="M282 292 Q296 302 310 292" {...line} />
          <path d="M248 334 Q256 338 264 334" {...line} />
          <text className="pc-z1" x="392" y="214" fontFamily="Helvetica, Arial, sans-serif" fontWeight="bold" fontSize="40" fill={PAPER}>z</text>
          <text className="pc-z2" x="420" y="180" fontFamily="Helvetica, Arial, sans-serif" fontWeight="bold" fontSize="28" fill={PAPER} opacity="0.8">z</text>
        </>
      );
    default:
      return (
        <>
          <OpenEyes />
          <path d="M240 330 Q256 344 272 330" {...line} />
        </>
      );
  }
}

export default function Character({ mood, size = 28, live = false, className = '', title }: {
  mood: Mood; size?: number; live?: boolean; className?: string; title?: string;
}) {
  const halo = mood === 'alert' || mood === 'concerned';
  return (
    <svg
      className={`pc ${mood} ${live ? 'live' : ''} ${className}`}
      viewBox="0 0 512 512" width={size} height={size}
      role={title ? 'img' : undefined} aria-hidden={title ? undefined : true} aria-label={title}
    >
      <circle cx="256" cy="256" r="256" fill="#0E5C4D" />
      <g className="pc-house">
        <rect x="316" y="150" width="26" height="62" rx="5" fill="#A44F2C" />
        <circle className="pc-halo" cx="329" cy="130" r="20" fill={LAMP[mood]} opacity={halo ? 0.35 : 0} />
        <circle className="pc-lamp" cx="329" cy="132" r="12" fill={LAMP[mood]} />
        <rect x="146" y="214" width="220" height="182" rx="38" fill={PAPER} />
        <path d="M256 124 L388 232 L124 232 Z" fill="#C8643B" stroke="#C8643B" strokeWidth="26" strokeLinejoin="round" />
        <ellipse cx="196" cy="326" rx="15" ry="8" fill="#F2A6A0" opacity="0.75" />
        <ellipse cx="316" cy="326" rx="15" ry="8" fill="#F2A6A0" opacity="0.75" />
        <Face mood={mood} />
      </g>
    </svg>
  );
}
