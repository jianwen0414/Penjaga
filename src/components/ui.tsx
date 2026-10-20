import { useEffect, useId, type ReactNode } from 'react';
import type { Series, Severity, Case } from '../api/types';

// ---------- icons (1.6px strokes, drawn for this app) ----------
const P: Record<string, ReactNode> = {
  home: <path d="M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-4.5v-5.5h-5V21H5a1 1 0 0 1-1-1v-8.5Z" />,
  cases: <><rect x="4" y="5" width="16" height="15" rx="2.5" /><path d="M8 10h8M8 14h5" /></>,
  house3d: <><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></>,
  notices: <><path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" /><path d="M10 19a2 2 0 0 0 4 0" /></>,
  bell: <><path d="M6 8.5a6 6 0 0 1 12 0c0 6.5 2.5 8.5 2.5 8.5h-17S6 15 6 8.5Z" /><path d="M10.2 20.5a2 2 0 0 0 3.6 0" /></>,
  ask: <><path d="M5 18.5V7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H8.5L5 18.5Z" /><path d="M9 10.5h.01M12 10.5h.01M15 10.5h.01" /></>,
  back: <path d="M15 5 8 12l7 7" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  water: <path d="M12 3.5s6 6.4 6 10.5a6 6 0 0 1-12 0c0-4.1 6-10.5 6-10.5Z" />,
  bolt: <path d="M13 3 5 13.5h6L10 21l8-10.5h-6L13 3Z" />,
  shield: <><path d="M12 3 5 6v5.5c0 4.3 3 7.9 7 9.5 4-1.6 7-5.2 7-9.5V6l-7-3Z" /><path d="m9 12 2 2 4-4" /></>,
  pin: <><path d="M12 21s7-6.2 7-11.5a7 7 0 0 0-14 0C5 14.8 12 21 12 21Z" /><circle cx="12" cy="9.5" r="2.5" /></>,
  replay: <><path d="M4 12a8 8 0 1 0 2.5-5.8" /><path d="M4 4v4h4" /></>,
  layers: <><path d="m12 4 9 5-9 5-9-5 9-5Z" /><path d="m3 14 9 5 9-5" /></>,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="2.8" /></>,
  drill: <><path d="M4 9h9l2 2v2l-2 2H4V9Z" /><path d="M15 12h5M8 15v4h3" /></>,
  phone: <path d="M6 3.5h3l1.5 4-2 1.5a11 11 0 0 0 6.5 6.5l1.5-2 4 1.5v3a2 2 0 0 1-2 2A16.5 16.5 0 0 1 4 5.5a2 2 0 0 1 2-2Z" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  scan: <><path d="M4 8.5V5.5a1.5 1.5 0 0 1 1.5-1.5h3M15.5 4h3A1.5 1.5 0 0 1 20 5.5v3M20 15.5v3a1.5 1.5 0 0 1-1.5 1.5h-3M8.5 20h-3A1.5 1.5 0 0 1 4 18.5v-3" /><path d="M4 12h16" /></>,
  calendar: <><rect x="4" y="5.5" width="16" height="14.5" rx="2.5" /><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" /></>,
  send: <path d="M4 12 20 4l-6 16-3-7-7-1Z" />,
  wrench: <path d="M14.5 4.5a4.5 4.5 0 0 0-5.6 5.6L4 15l2 2 2 2 4.9-4.9a4.5 4.5 0 0 0 5.6-5.6l-2.7 2.7-2.6-.7-.7-2.6 2.7-2.7Z" />,
  book: <><path d="M5 4.5h10a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3V4.5Z" /><path d="M5 17a3 3 0 0 1 3-3h10" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h.01" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></>,
};
export function Icon({ name, size = 20, title }: { name: keyof typeof P | string; size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden={title ? undefined : true} role={title ? 'img' : undefined}>
      {title && <title>{title}</title>}
      {P[name]}
    </svg>
  );
}

export const SEV_LABEL: Record<Severity, string> = { info: 'Info', heads_up: 'Heads-up', act_soon: 'Act soon', urgent: 'Urgent' };
export function Sev({ c }: { c: Pick<Case, 'severity' | 'status'> }) {
  if (c.status === 'resolved') return <span className="sev resolved">Resolved</span>;
  return <span className={`sev ${c.severity}`}>{SEV_LABEL[c.severity]}</span>;
}

// ---------- charts: honest axes, units, thresholds and baselines marked ----------
export function Chart({ s, height = 170, title }: { s: Series; height?: number; title: string }) {
  const id = useId();
  const W = 600, H = height, L = 38, R = 12, T = 12, B = 24;
  const pts = s.points;
  if (!pts.length) return null;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const vmax = Math.max(...pts.map((p) => p.v), ...(s.lines ?? []).map((l) => l.v)) * 1.12 || 1;
  const x = (t: number) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const y = (v: number) => T + (1 - v / vmax) * (H - T - B);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
  const area = `${path}L${x(t1)},${y(0)}L${x(t0)},${y(0)}Z`;
  const ticks = [0, vmax / 2, vmax].map((v) => +v.toPrecision(2));
  const days = pts.filter((p, i) => i === 0 || new Date(p.t).getDate() !== new Date(pts[i - 1].t).getDate());
  const tone = { muted: 'var(--muted)', warn: 'var(--heads)', danger: 'var(--urgent)' };
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={id}>
      <title id={id}>{title}</title>
      {(s.bands ?? []).map((b, i) => b.to > t0 && b.from < t1 && (
        <g key={i}>
          <rect x={x(Math.max(b.from, t0))} y={T} width={Math.max(2, x(Math.min(b.to, t1)) - x(Math.max(b.from, t0)))} height={H - T - B} fill="var(--info-soft)" />
          {i === (s.bands?.length ?? 0) - 1 && <text x={x(Math.max(b.from, t0)) + 4} y={T + 12}>{b.label}</text>}
        </g>
      ))}
      {ticks.map((v) => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--line)" />
          <text x={L - 6} y={y(v) + 4} textAnchor="end">{v}</text>
        </g>
      ))}
      <text x={L - 6} y={T - 2} textAnchor="end">{s.unit}</text>
      <path d={area} fill="var(--water)" opacity={s.unit === 'mA' ? 0 : 0.12} />
      <path d={path} fill="none" stroke={s.unit === 'mA' ? 'var(--power)' : 'var(--water)'} strokeWidth={2} strokeLinejoin="round" />
      {s.unit === 'mA' && pts.map((p) => <circle key={p.t} cx={x(p.t)} cy={y(p.v)} r={3.5} fill="var(--power)" />)}
      {(s.lines ?? []).map((l) => (
        <g key={l.label}>
          <line x1={L} x2={W - R} y1={y(l.v)} y2={y(l.v)} stroke={tone[l.tone]} strokeDasharray="5 4" strokeWidth={1.3} />
          <text x={W - R} y={y(l.v) - 5} textAnchor="end" style={{ fill: tone[l.tone] }}>{l.label}</text>
        </g>
      ))}
      {(s.markers ?? []).map((m) => (
        <g key={m.t}><text x={x(m.t)} y={H - B - 6} textAnchor="middle" style={{ fill: 'var(--info)' }}>☂</text></g>
      ))}
      {days.map((p) => <text key={p.t} x={x(p.t)} y={H - 6}>{new Date(p.t).toLocaleDateString('en-MY', { weekday: 'short' })}</text>)}
    </svg>
  );
}

export function Sparkline({ values, typical, color }: { values: number[]; typical?: number[]; color: string }) {
  const W = 200, H = 40;
  const n = Math.max(values.length, typical?.length ?? 0, 2);
  const max = Math.max(...values, ...(typical ?? []), 0.01);
  const line = (v: number[]) => v.map((p, i) => `${i ? 'L' : 'M'}${((i / (n - 1)) * W).toFixed(1)},${(H - 2 - (p / max) * (H - 6)).toFixed(1)}`).join('');
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden>
      {typical && <path d={line(typical)} fill="none" stroke="var(--line-2)" strokeWidth={1.5} strokeDasharray="3 3" />}
      <path d={line(values)} fill="none" stroke={color} strokeWidth={2} />
    </svg>
  );
}

// ---------- sheet: bottom sheet on phones, side panel on desktop ----------
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    addEventListener('keydown', k);
    return () => removeEventListener('keydown', k);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div className="grab" />
        <div className="sheet-h"><h2>{title}</h2><button className="round" onClick={onClose} aria-label="Close"><Icon name="close" size={18} /></button></div>
        {children}
      </div>
    </>
  );
}

export const fmtTime = (t: number) => new Date(t).toLocaleTimeString('en-MY', { hour: 'numeric', minute: '2-digit' });
export const fmtDay = (t: number) => new Date(t).toLocaleDateString('en-MY', { weekday: 'short', day: 'numeric', month: 'short' });
export const fmtWhen = (t: number) => {
  const d = Date.now() - t;
  if (d < 60e3) return 'just now';
  if (d < 3600e3) return `${Math.round(d / 60e3)} min ago`;
  if (d < 86400e3) return `${Math.round(d / 3600e3)} h ago`;
  return fmtDay(t);
};
