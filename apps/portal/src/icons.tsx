// One authored icon set: 24px grid, 1.75 stroke, round caps and joins. Monochrome; colour comes from CSS.
const PATHS = {
  inbox: 'M4 13.5 6.4 6.2A1.8 1.8 0 0 1 8.1 5h7.8a1.8 1.8 0 0 1 1.7 1.2L20 13.5M4 13.5V18a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4.5M4 13.5h4.2l1.3 2.2h5l1.3-2.2H20',
  notice: 'M4 10v4a1 1 0 0 0 1 1h2l6 4V5L7 9H5a1 1 0 0 0-1 1ZM16.5 9.2a4 4 0 0 1 0 5.6M19 7a7 7 0 0 1 0 10',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM12 3v1.5M12 19.5V21M4.6 4.6l1.1 1.1M18.3 18.3l1.1 1.1M3 12h1.5M19.5 12H21M4.6 19.4l1.1-1.1M18.3 5.7l1.1-1.1',
  moon: 'M19.5 14.6A7.5 7.5 0 0 1 9.4 4.5a7.5 7.5 0 1 0 10.1 10.1Z',
  chevron: 'm10 7 5 5-5 5',
  close: 'M7 7l10 10M17 7 7 17',
  check: 'm6.5 12.5 3.5 3.5 7.5-8',
  read: 'M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20V4a.5.5 0 0 1 0-.5ZM14 3.5V8h4M9.8 12h4.4M9.8 15.5h4.4',
  drop: 'M12 3.5s6 6.4 6 10.5a6 6 0 0 1-12 0c0-4.1 6-10.5 6-10.5Z',
  bolt: 'M13 3 5.5 13.5H12L11 21l7.5-10.5H12L13 3Z',
  reply: 'M6.5 5h11a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H11l-4.5 3.5V16a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z',
  home:'M4.5 10.5 12 4.5l7.5 6V19a.5.5 0 0 1-.5.5h-4.5v-5h-5v5H5a.5.5 0 0 1-.5-.5v-8.5Z',
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
