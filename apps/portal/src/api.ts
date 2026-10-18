// The Core API's management routes, through the dev proxy (which adds the management token).
export type TicketStatus = 'received' | 'scheduled' | 'in_progress' | 'resolved';

export interface Ticket {
  id: string;
  case_id: string;
  status: TicketStatus;
  created: string; // house time
  updated: string;
  title: string;
  summary: string;
  location: string;
  severity: 'info' | 'heads_up' | 'act_soon' | 'urgent';
  evidence: { label: string; value: string }[];
  note: string;
  contact: 'in_app' | 'phone_call' | 'whatsapp';
  may_be_defect?: string;
  recipient: string;
  messages: { actor: string; text: string; t: string }[];
  history?: { status: TicketStatus; t: string }[]; // status changes, oldest first
}

// House time from the Core API, shown as the house's local time.
export function when(t: string): string {
  const d = new Date(t);
  const day = d.toLocaleDateString('en-MY', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Asia/Kuala_Lumpur' });
  const time = d.toLocaleTimeString('en-MY', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kuala_Lumpur' });
  return `${day}, ${time}`;
}

const BASE = '/api/v1/manage';

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const found = (await r.json()).detail;
      // FastAPI's validation errors are a list; keep their messages without the "Value error," prefix
      if (Array.isArray(found)) detail = found.map((e: { msg?: string }) => (e.msg ?? '').replace(/^Value error, /, '')).join('; ');
      else if (found) detail = String(found);
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return (await r.json()) as T;
}

export const listTickets = () => call<Ticket[]>('GET', '/tickets');
export const setStatus = (id: string, status: TicketStatus) => call<Ticket>('POST', `/tickets/${id}/status`, { status });
export const reply = (id: string, text: string) => call<Ticket>('POST', `/tickets/${id}/replies`, { text });

/** Calls ``onChange`` whenever the Core API publishes a ticket event (new ticket, status, reply), and
 * ``onLive`` when the live connection opens or drops (EventSource reconnects on its own). */
export function watchTickets(onChange: () => void, onLive?: (live: boolean) => void): () => void {
  const events = new EventSource('/api/v1/events');
  events.addEventListener('ticket', onChange);
  events.onopen = () => { onLive?.(true); onChange(); };
  events.onerror = () => onLive?.(false);
  return () => events.close();
}

/** A short reference staff can read out on the phone: the ticket ID's last six characters. */
export const ref = (id: string) => `#${id.slice(-6).toUpperCase()}`;

// ---------- notices to homes

export type NoticeType = 'water_disruption' | 'planned_power_outage' | 'works' | 'general';

/** A notice as staff confirm it (times are ISO 8601 with +08:00). */
export interface NoticeDraft {
  type: NoticeType;
  utility: 'water' | 'electricity' | 'other';
  scheduled: boolean;
  title: string;
  reason: string;
  areas: string[];
  district: string;
  starts_at: string;
  ends_at: string;
  expected_recovery: string | null;
  recovery_note: string;
  advice: string[];
  alternative_supply: string | null;
  contact: string | null;
  reference: string | null;
}

export interface Notice extends NoticeDraft {
  id: string;
  status: 'published' | 'withdrawn';
  posted_at: string;
  reaches_house: boolean;
}

export interface ReadNotice { draft: NoticeDraft; issues: string[]; model: string | null }

export const readNotice = (text: string) => call<ReadNotice>('POST', '/notices/parse', { text });
export const publishNotice = (draft: NoticeDraft) => call<Notice & { issues: string[] }>('POST', '/notices', draft);
export const listNotices = () => call<Notice[]>('GET', '/notices');
export const withdrawNotice = (id: string) => call<Notice>('POST', `/notices/${id}/withdraw`);

export function watchNotices(onChange: () => void): () => void {
  const events = new EventSource('/api/v1/events');
  events.addEventListener('notice', onChange);
  return () => events.close();
}
