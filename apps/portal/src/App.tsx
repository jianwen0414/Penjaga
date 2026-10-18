import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { listNotices, listTickets, ref, reply, setStatus, watchNotices, watchTickets, when, type Notice, type Ticket, type TicketStatus } from './api';
import { Icon } from './icons';
import Notices from './Notices';

const STEPS: { id: TicketStatus; label: string; action?: string }[] = [
  { id: 'received', label: 'Received', action: 'Schedule a visit' },
  { id: 'scheduled', label: 'Visit booked', action: 'Start work' },
  { id: 'in_progress', label: 'In progress', action: 'Mark resolved' },
  { id: 'resolved', label: 'Resolved' },
];
const STATUS: Record<TicketStatus, { label: string; tone: string }> = {
  received: { label: 'New', tone: 'info' },
  scheduled: { label: 'Visit booked', tone: 'info' },
  in_progress: { label: 'In progress', tone: 'warn' },
  resolved: { label: 'Resolved', tone: 'safe' },
};
const SEVERITY: Record<Ticket['severity'], { label: string; tone: string }> = {
  urgent: { label: 'Urgent', tone: 'alert' },
  act_soon: { label: 'Act soon', tone: 'warn' },
  heads_up: { label: 'Heads-up', tone: 'info' },
  info: { label: 'For information', tone: 'quiet' },
};
const CONTACT: Record<Ticket['contact'], { short: string; long: string }> = {
  in_app: { short: 'In the app', long: 'Reply in the app' },
  phone_call: { short: 'Phone call', long: 'Prefers a phone call' },
  whatsapp: { short: 'WhatsApp', long: 'Prefers WhatsApp' },
};
type Filter = 'open' | TicketStatus | 'all';
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'open', label: 'Open' },
  { id: 'received', label: 'New' },
  { id: 'scheduled', label: 'Visit booked' },
  { id: 'in_progress', label: 'In progress' },
  { id: 'resolved', label: 'Resolved' },
  { id: 'all', label: 'All' },
];
const EMPTY: Record<Filter, string> = {
  open: 'Reports homeowners send from Penjaga arrive here, with where the problem is and the evidence.',
  all: 'Reports homeowners send from Penjaga arrive here, with where the problem is and the evidence.',
  received: 'New reports wait here until you book a visit.',
  scheduled: 'Reports with a visit booked show here.',
  in_progress: 'Reports you are working on show here.',
  resolved: 'Reports you mark resolved show here.',
};

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => {
    const asked = new URLSearchParams(location.search).get('theme');
    if (asked === 'dark' || asked === 'light') return asked;
    try { return localStorage.getItem('penjaga.portal.theme') ?? 'light'; } catch { return 'light'; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('penjaga.portal.theme', theme); } catch { /* storage unavailable */ }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))];
}

type Page = 'reports' | 'notices';

export default function App() {
  // ?page=notices opens the notices page; ?ticket=<id> opens a report. The URL follows both so it can be shared.
  const [page, setPage] = useState<Page>(() => (new URLSearchParams(location.search).get('page') === 'notices' ? 'notices' : 'reports'));
  const [openId, setOpenId] = useState<string | null>(() => new URLSearchParams(location.search).get('ticket'));
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [offline, setOffline] = useState(false);
  const [live, setLive] = useState(false);
  const [filter, setFilter] = useState<Filter>('open');
  const [theme, toggleTheme] = useTheme();

  useEffect(() => {
    const url = new URL(location.href);
    if (openId) url.searchParams.set('ticket', openId);
    else url.searchParams.delete('ticket');
    if (page === 'notices') url.searchParams.set('page', 'notices');
    else url.searchParams.delete('page');
    history.replaceState(null, '', url);
  }, [openId, page]);

  const load = useCallback(async () => {
    try { setTickets(await listTickets()); setOffline(false); } catch { setOffline(true); }
  }, []);
  useEffect(() => {
    void load();
    const stop = watchTickets(() => void load(), setLive);
    const retry = setInterval(() => void load(), 30_000);
    return () => { stop(); clearInterval(retry); };
  }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenId(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const counts = useMemo(() => {
    const all = tickets ?? [];
    const by = (s: TicketStatus) => all.filter((t) => t.status === s).length;
    return {
      open: all.filter((t) => t.status !== 'resolved').length, all: all.length,
      received: by('received'), scheduled: by('scheduled'), in_progress: by('in_progress'), resolved: by('resolved'),
    } satisfies Record<Filter, number>;
  }, [tickets]);
  const shown = useMemo(() => {
    const all = tickets ?? [];
    if (filter === 'all') return all;
    if (filter === 'open') return all.filter((t) => t.status !== 'resolved');
    return all.filter((t) => t.status === filter);
  }, [tickets, filter]);
  const opened = page === 'reports' ? (tickets ?? []).find((t) => t.id === openId) ?? null : null;

  // Live notices for the side column (the notices page manages them).
  const [notices, setNotices] = useState<Notice[] | null>(null);
  const loadNotices = useCallback(async () => { try { setNotices(await listNotices()); } catch { /* the inbox still works */ } }, []);
  useEffect(() => { void loadNotices(); return watchNotices(() => void loadNotices()); }, [loadNotices]);
  const liveNotices = (notices ?? []).filter((n) => n.status === 'published' && n.reaches_house);

  return (
    <div className="pt-shell">
      <aside className="pt-rail">
        <div className="pt-rail-panel">
          <div className="pt-brand">
            <span className="pt-mark"><Icon name="home" size={17} /></span>
            <span>
              <b>Penjaga</b>
              <small>Township management</small>
            </span>
          </div>
          <nav className="pt-nav" aria-label="Portal">
            <button aria-current={page === 'reports' ? 'page' : undefined} onClick={() => setPage('reports')}>
              <Icon name="inbox" /> <span>Reports from homes</span>
              {counts.open > 0 && <span className="pt-count pt-num">{counts.open}</span>}
            </button>
            <button aria-current={page === 'notices' ? 'page' : undefined} onClick={() => { setPage('notices'); setOpenId(null); }}>
              <Icon name="notice" /> <span>Notices to homes</span>
            </button>
          </nav>
          <div className="pt-rail-foot">
            <span className="pt-avatar" aria-hidden="true">NA</span>
            <span className="pt-staff">
              <b>Nurul Aina</b>
              <small>Customer service</small>
            </span>
            <button className="pt-icon-btn" onClick={toggleTheme} aria-label={theme === 'dark' ? 'Use the light theme' : 'Use the dark theme'} title={theme === 'dark' ? 'Light theme' : 'Dark theme'}>
              <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={17} />
            </button>
          </div>
        </div>
      </aside>

      <main className="pt-main">
        {page === 'notices' ? <Notices /> : (
          <>
            <header className="pt-head">
              <div>
                <h1>Reports from homes</h1>
                <p>Problems homeowners chose to send you from Penjaga, with where it is and the evidence. Each update reaches their phone straight away.</p>
              </div>
              <span className="pt-live" data-live={live && !offline}>{live && !offline ? 'Live' : 'Reconnecting'}</span>
            </header>
            {offline && <div className="pt-banner" role="status">Can't reach the Penjaga service. Showing the last list; it retries on its own.</div>}
            <div className="pt-board">
            <div className="pt-board-main">
            <Brief tickets={tickets} onOpen={setOpenId} />
            <div className="pt-tabs" role="tablist" aria-label="Filter by status">
              {FILTERS.map((f) => (
                <button key={f.id} role="tab" aria-selected={filter === f.id} onClick={() => setFilter(f.id)}>
                  <b className="pt-num">{tickets === null ? ' ' : counts[f.id]}</b>
                  <span>{f.label}</span>
                </button>
              ))}
            </div>
            <section className="pt-list" aria-label="Reports" data-ready={tickets !== null}>
              <div className="pt-row pt-row-head">
                <span />
                <span>Report</span>
                <span>Status</span>
                <span>Contact by</span>
                <span className="pt-right">Received</span>
                <span />
              </div>
              {tickets === null && !offline && [0, 1, 2].map((i) => (
                <div className="pt-row pt-skel" key={i}><span /><span><i /><i /></span><span><i /></span><span><i /></span><span><i /></span><span /></div>
              ))}
              {tickets !== null && shown.length === 0 && (
                <div className="pt-empty">
                  <b>{filter === 'open' || filter === 'all' ? 'No reports yet' : `No ${FILTERS.find((f) => f.id === filter)?.label.toLowerCase()} reports`}</b>
                  <span>{EMPTY[filter]}</span>
                </div>
              )}
              {shown.map((t, i) => (
                <button key={t.id} className="pt-row pt-row-btn" data-open={t.id === openId} onClick={() => setOpenId(t.id)} style={{ '--i': Math.min(i, 10) } as CSSProperties}>
                  <span className="pt-dot" data-tone={SEVERITY[t.severity].tone} title={SEVERITY[t.severity].label} />
                  <span className="pt-cell-main">
                    <span className="pt-title">{t.title}</span>
                    <span className="pt-sub">
                      <span className="pt-ref pt-num">{ref(t.id)}</span>
                      <span data-tone={SEVERITY[t.severity].tone} className="pt-sev">{SEVERITY[t.severity].label}</span>
                      <span>{t.note ? `“${t.note}”` : t.location}</span>
                    </span>
                  </span>
                  <span className="pt-pill" data-tone={STATUS[t.status].tone}>{STATUS[t.status].label}</span>
                  <span className="pt-muted">{CONTACT[t.contact].short}</span>
                  <span className="pt-time pt-num">{when(t.created)}</span>
                  <Icon name="chevron" size={16} className="pt-go" />
                </button>
              ))}
            </section>
            </div>
            <aside className="pt-board-side" aria-label="Activity and notices">
              <Activity tickets={tickets} onOpen={setOpenId} />
              <LiveNotices notices={liveNotices} loading={notices === null} onManage={() => { setPage('notices'); setOpenId(null); }} />
            </aside>
            </div>
          </>
        )}
      </main>

      <TicketPanel ticket={opened} onClose={() => setOpenId(null)} onChanged={load} />
    </div>
  );
}

/** The situation in one sentence and the one thing to do next; the counts live in the tabs below. */
function Brief({ tickets, onOpen }: { tickets: Ticket[] | null; onOpen: (id: string) => void }) {
  const open = (tickets ?? []).filter((t) => t.status !== 'resolved');
  const urgent = open.filter((t) => t.severity === 'urgent');
  const waiting = open.filter((t) => t.status === 'received');
  const byAge = (list: Ticket[]) => [...list].sort((a, b) => a.created.localeCompare(b.created))[0];
  const next = byAge(urgent) ?? byAge(waiting) ?? byAge(open);
  let line = 'Nothing is waiting on you.';
  let action = '';
  if (urgent.length) {
    line = urgent.length === 1 ? 'One urgent report needs you.' : `${urgent.length} urgent reports need you.`;
    action = urgent.length === 1 ? 'Open it' : 'Open the oldest';
  } else if (waiting.length) {
    line = waiting.length === 1 ? 'One new report is waiting for a visit.' : `${waiting.length} new reports are waiting for a visit.`;
    action = 'Book the first visit';
  } else if (open.length) {
    line = 'Every open report has a visit booked or work under way.';
    action = 'Open the oldest';
  }
  return (
    <section className="pt-brief" aria-label="Today">
      <div>
        <h2>{tickets === null ? 'Loading reports…' : line}</h2>
        <p className="pt-num">
          {next ? `Waiting longest: ${ref(next.id)}, in since ${when(next.created)}.` : 'New reports from homes arrive here as homeowners send them.'}
        </p>
      </div>
      {next && action && (
        <button className="pt-btn primary" onClick={() => onOpen(next.id)}>
          {action}<Icon name="chevron" size={16} />
        </button>
      )}
    </section>
  );
}

const MOVED: Partial<Record<TicketStatus, string>> = {
  scheduled: 'Visit booked for',
  in_progress: 'Work started on',
  resolved: 'Resolved',
};

/** What the office did lately: visits booked, work started, reports resolved and replies. New reports are
 * the list itself, so they aren't repeated here. */
function Activity({ tickets, onOpen }: { tickets: Ticket[] | null; onOpen: (id: string) => void }) {
  const items = (tickets ?? []).flatMap((t) => [
    ...(t.history ?? []).map((h, i) => ({
      key: `${t.id}:s${i}`, at: h.t, id: t.id, kind: h.status, head: `${MOVED[h.status] ?? 'Updated'} ${ref(t.id)}`, text: t.title,
    })),
    ...t.messages.map((m, i) => ({
      key: `${t.id}:m${i}`, at: m.t, id: t.id, kind: 'reply', head: `You replied on ${ref(t.id)}`, text: `“${m.text}”`,
    })),
  ]).sort((a, b) => b.at.localeCompare(a.at)).slice(0, 6);
  return (
    <section className="pt-side-block">
      <h2>Latest activity</h2>
      {tickets === null && <div className="pt-skel pt-feed-skel"><i /><i /></div>}
      {tickets !== null && items.length === 0 && <p className="pt-side-empty">Visits you book, work you start and your replies show here.</p>}
      <ol className="pt-feed">
        {items.map((e) => (
          <li key={e.key}>
            <button onClick={() => onOpen(e.id)}>
              <span className="pt-feed-mark" data-kind={e.kind}><Icon name={e.kind === 'reply' ? 'reply' : e.kind === 'resolved' ? 'check' : 'chevron'} size={14} /></span>
              <span className="pt-feed-text">
                <b>{e.head}</b>
                <span>{e.text}</span>
                <small className="pt-num">{when(e.at)}</small>
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

function LiveNotices({ notices, loading, onManage }: { notices: Notice[]; loading: boolean; onManage: () => void }) {
  return (
    <section className="pt-side-block">
      <h2>Notices live</h2>
      {loading && <div className="pt-skel pt-feed-skel"><i /><i /></div>}
      {!loading && notices.length === 0 && <p className="pt-side-empty">No notices are reaching homes right now.</p>}
      <ul className="pt-mini-notices">
        {notices.slice(0, 3).map((n) => {
          const d = new Date(n.starts_at);
          const opt = { timeZone: 'Asia/Kuala_Lumpur' } as const;
          return (
            <li key={n.id}>
              <span className="nt-date pt-num" aria-hidden="true"><b>{d.toLocaleDateString('en-MY', { ...opt, day: 'numeric' })}</b><span>{d.toLocaleDateString('en-MY', { ...opt, month: 'short' })}</span></span>
              <span className="pt-feed-text"><b>{n.title}</b><small className="pt-num">{when(n.starts_at)} to {when(n.ends_at)}</small></span>
            </li>
          );
        })}
      </ul>
      <button className="pt-link" onClick={onManage}>Manage notices</button>
    </section>
  );
}

function Stepper({ status }: { status: TicketStatus }) {
  const at = STEPS.findIndex((s) => s.id === status);
  return (
    <ol className="pt-steps" aria-label="Progress">
      {STEPS.map((s, i) => (
        <li key={s.id} data-state={i < at || status === 'resolved' ? 'done' : i === at ? 'now' : 'next'}>
          <span className="pt-step-mark pt-num">{i < at || status === 'resolved' ? <Icon name="check" size={13} /> : i + 1}</span>
          <span className="pt-step-label">{s.label}</span>
        </li>
      ))}
    </ol>
  );
}

function TicketPanel({ ticket, onClose, onChanged }: { ticket: Ticket | null; onClose: () => void; onChanged: () => Promise<void> }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { setText(''); setError(''); body.current?.scrollTo({ top: 0 }); }, [ticket?.id]);
  const step = ticket ? STEPS.find((s) => s.id === ticket.status) : undefined;
  const next = ticket ? STEPS[STEPS.findIndex((s) => s.id === ticket.status) + 1] : undefined;

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await work();
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That didn\'t go through. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="pt-panel" data-open={ticket !== null} aria-hidden={ticket === null} inert={ticket === null} aria-label="Report">
      {ticket && (
        <>
          <div className="pt-panel-head">
            <div className="pt-panel-top">
              <span className="pt-ref pt-num">{ref(ticket.id)}</span>
              <span className="pt-pill" data-tone={SEVERITY[ticket.severity].tone}>{SEVERITY[ticket.severity].label}</span>
              <button className="pt-x" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>
            </div>
            <h2>{ticket.title}</h2>
            <p>{ticket.location}</p>
            <Stepper status={ticket.status} />
          </div>
          <div className="pt-panel-body" ref={body}>
            <div className="pt-facts pt-num">
              <span>Received {when(ticket.created)}</span>
              <span>{CONTACT[ticket.contact].long}</span>
            </div>
            {ticket.note && (
              <blockquote className="pt-quote">
                <span>From the homeowner</span>
                {ticket.note}
              </blockquote>
            )}
            {ticket.may_be_defect && <div className="pt-defect"><b>Possible defect.</b> {ticket.may_be_defect}</div>}
            <section className="pt-block">
              <h3>What Penjaga found</h3>
              <p>{ticket.summary}</p>
            </section>
            {ticket.evidence.length > 0 && (
              <section className="pt-block">
                <h3>Evidence</h3>
                <dl className="pt-stats">
                  {ticket.evidence.map((s) => (
                    <div className="pt-stat" key={s.label}><dt>{s.label}</dt><dd className="pt-num">{s.value}</dd></div>
                  ))}
                </dl>
              </section>
            )}
            {ticket.messages.length > 0 && (
              <section className="pt-block">
                <h3>Your replies</h3>
                <ol className="pt-thread">
                  {ticket.messages.map((m, i) => (
                    <li className="pt-msg" key={i}>
                      <span className="pt-avatar sm" aria-hidden="true">NA</span>
                      <div><small className="pt-num">{when(m.t)}</small>{m.text}</div>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </div>
          <div className="pt-actions">
            {step?.action && next && (
              <button className="pt-btn primary" disabled={busy} onClick={() => run(() => setStatus(ticket.id, next.id))}>
                {step.action}
              </button>
            )}
            <div className="pt-reply">
              <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="Write a reply the homeowner will see" aria-label="Reply to the homeowner" />
              <button className="pt-btn" disabled={busy || !text.trim()} onClick={() => run(async () => { await reply(ticket.id, text.trim()); setText(''); })}>Send reply</button>
            </div>
            <div className="pt-help" role={error ? 'alert' : undefined}>{error || 'Status changes and replies appear on the homeowner\'s case straight away.'}</div>
          </div>
        </>
      )}
    </aside>
  );
}
