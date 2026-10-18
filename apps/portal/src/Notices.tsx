import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { listNotices, publishNotice, readNotice, watchNotices, when, withdrawNotice, type Notice, type NoticeDraft, type NoticeType } from './api';
import { Icon, type IconName } from './icons';

const TYPES: { id: NoticeType; label: string; utility: NoticeDraft['utility']; icon: IconName }[] = [
  { id: 'water_disruption', label: 'Water supply disruption', utility: 'water', icon: 'drop' },
  { id: 'planned_power_outage', label: 'Planned power outage', utility: 'electricity', icon: 'bolt' },
  { id: 'works', label: 'Works nearby (no supply cut)', utility: 'other', icon: 'notice' },
  { id: 'general', label: 'General notice', utility: 'other', icon: 'notice' },
];
const typeOf = (id: NoticeType) => TYPES.find((t) => t.id === id) ?? TYPES[3];

// The township is in Malaysia (UTC+8, no daylight saving): form fields hold house-local time.
const toLocal = (iso: string | null) => {
  if (!iso) return '';
  const d = new Date(new Date(iso).getTime() + 8 * 3600e3);
  return d.toISOString().slice(0, 16);
};
const fromLocal = (v: string) => (v ? `${v}:00+08:00` : '');
const dayParts = (iso: string) => {
  const d = new Date(iso);
  const opt = { timeZone: 'Asia/Kuala_Lumpur' } as const;
  return {
    day: d.toLocaleDateString('en-MY', { ...opt, day: 'numeric' }),
    month: d.toLocaleDateString('en-MY', { ...opt, month: 'short' }),
  };
};

const BLANK: NoticeDraft = {
  type: 'water_disruption', utility: 'water', scheduled: true, title: '', reason: '', areas: [], district: '',
  starts_at: '', ends_at: '', expected_recovery: null, recovery_note: '', advice: [], alternative_supply: null,
  contact: null, reference: null,
};

// Form state keeps list fields as text (one item per line) so typing isn't fought.
interface Form extends Omit<NoticeDraft, 'areas' | 'advice'> { areas: string; advice: string }
const toForm = (d: NoticeDraft): Form => ({ ...d, areas: d.areas.join('\n'), advice: d.advice.join('\n') });
const lines = (v: string) => v.split('\n').map((s) => s.trim()).filter(Boolean);
const toDraft = (f: Form): NoticeDraft => ({
  ...f,
  areas: lines(f.areas),
  advice: lines(f.advice),
  expected_recovery: f.expected_recovery || null,
  alternative_supply: f.alternative_supply?.trim() || null,
  contact: f.contact?.trim() || null,
  reference: f.reference?.trim() || null,
});
const bodyOf = (d: NoticeDraft) =>
  [d.reason, d.recovery_note, ...d.advice, d.alternative_supply ?? '', d.contact ?? '']
    .map((p) => p.trim().replace(/\.$/, '')).filter(Boolean).map((p) => `${p}.`).join(' ');

export default function Notices() {
  const [notices, setNotices] = useState<Notice[] | null>(null);
  const [offline, setOffline] = useState(false);
  const [text, setText] = useState('');
  const [form, setForm] = useState<Form | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [read, setRead] = useState(false); // the fields came from the AI reader
  const [busy, setBusy] = useState<'' | 'read' | 'publish'>('');
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  const load = useCallback(async () => {
    try { setNotices(await listNotices()); setOffline(false); } catch { setOffline(true); }
  }, []);
  useEffect(() => {
    void load();
    return watchNotices(() => void load());
  }, [load]);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => (f ? { ...f, [key]: value } : f));

  const onRead = async () => {
    setBusy('read'); setError(''); setDone('');
    try {
      const r = await readNotice(text);
      setForm(toForm(r.draft)); setIssues(r.issues); setRead(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '';
      setError(msg.startsWith('5')
        ? 'Penjaga couldn\'t read it just now. Try again, or fill in the fields yourself.'
        : `Penjaga couldn't read the dates and areas from that text. ${msg ? `(${msg}) ` : ''}Fill them in yourself below.`);
      setForm((f) => f ?? toForm(BLANK)); setRead(false);
    } finally { setBusy(''); }
  };

  const onPublish = async () => {
    if (!form) return;
    setBusy('publish'); setError('');
    try {
      const n = await publishNotice(toDraft(form));
      setDone(n.reaches_house
        ? `Published. Homes in ${n.areas[0] ?? n.district} see it now.`
        : 'Published, but its areas don\'t include any home Penjaga watches, so no one sees it.');
      setForm(null); setText(''); setIssues([]); setRead(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That didn\'t go through. Try again.');
    } finally { setBusy(''); }
  };

  const complete = Boolean(form && form.title.trim().length >= 3 && form.starts_at && form.ends_at);
  const stage = done && !form ? 3 : form ? 2 : 1;
  const draft = form ? toDraft(form) : null;

  return (
    <>
      <header className="pt-head">
        <div>
          <h1>Notices to homes</h1>
          <p>Planned water and power works, sent to the homes in the areas you name. Each home also sees what the works mean for its own taps, tank and appliances.</p>
        </div>
      </header>
      {offline && <div className="pt-banner" role="status">Can't reach the Penjaga service. It retries on its own.</div>}

      <div className="nt-grid">
        <section className="nt-compose" aria-label="New notice">
          <ol className="nt-stages" aria-label="Steps">
            {['Paste', 'Check', 'Publish'].map((s, i) => (
              <li key={s} data-state={i + 1 < stage || stage === 3 ? 'done' : i + 1 === stage ? 'now' : 'next'}>
                <span className="pt-step-mark pt-num">{i + 1 < stage || stage === 3 ? <Icon name="check" size={13} /> : i + 1}</span>
                {s}
              </li>
            ))}
          </ol>

          <div className="nt-step">
            <label className="nt-label" htmlFor="nt-paste">The notice, in Malay or English</label>
            <textarea id="nt-paste" className="nt-input nt-paste" value={text} onChange={(e) => setText(e.target.value)} rows={form ? 4 : 10}
              placeholder={'NOTIS GANGGUAN BEKALAN AIR BERJADUAL\nTarikh: …\nKawasan terlibat: …'} />
            <div className="nt-row">
              <button className="pt-btn primary" disabled={busy !== '' || text.trim().length < 20} onClick={onRead}>
                <Icon name="read" size={17} />
                {busy === 'read' ? 'Reading the notice…' : form && read ? 'Read it again' : 'Read the notice'}
              </button>
              {!form && <button className="pt-link" onClick={() => { setForm(toForm(BLANK)); setRead(false); setDone(''); }}>Fill in by hand</button>}
            </div>
            {done && <div className="nt-done" role="status"><Icon name="check" size={16} />{done}</div>}
          </div>

          {form && (
            <div className="nt-step nt-fields">
              <div className="nt-fields-head">
                <h2>Check before publishing</h2>
                <p>{read ? 'Penjaga\'s AI read these from the text above. Compare each one with the original.' : 'Fill in what the notice says. Leave out anything it doesn\'t give.'}</p>
              </div>
              {issues.length > 0 && <ul className="nt-issues">{issues.map((i) => <li key={i}>{i}</li>)}</ul>}
              <Field label="Kind of notice">
                <select className="nt-input" value={form.type} onChange={(e) => {
                  const t = typeOf(e.target.value as NoticeType);
                  setForm((f) => (f ? { ...f, type: t.id, utility: t.utility === 'other' ? f.utility : t.utility } : f));
                }}>
                  {TYPES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                </select>
              </Field>
              <Field label="Title homeowners see">
                <input className="nt-input" value={form.title} onChange={(e) => set('title', e.target.value)} maxLength={140} />
              </Field>
              <div className="nt-pair">
                <Field label="Starts"><input className="nt-input pt-num" type="datetime-local" value={toLocal(form.starts_at)} onChange={(e) => set('starts_at', fromLocal(e.target.value))} /></Field>
                <Field label="Ends"><input className="nt-input pt-num" type="datetime-local" value={toLocal(form.ends_at)} onChange={(e) => set('ends_at', fromLocal(e.target.value))} /></Field>
              </div>
              {form.starts_at && form.ends_at && (
                <div className="nt-when pt-num" data-bad={new Date(form.ends_at) <= new Date(form.starts_at)}>
                  {new Date(form.ends_at) <= new Date(form.starts_at)
                    ? 'The end is before the start.'
                    : `${when(form.starts_at)} to ${when(form.ends_at)}, Malaysian time`}
                </div>
              )}
              <Field label="Supply fully back by" hint="If the notice gives it. Penjaga uses it to tell planned works from a fault.">
                <input className="nt-input pt-num" type="datetime-local" value={toLocal(form.expected_recovery)} onChange={(e) => set('expected_recovery', fromLocal(e.target.value) || null)} />
              </Field>
              <Field label="Areas" hint="One per line, as the notice lists them.">
                <textarea className="nt-input" rows={3} value={form.areas} onChange={(e) => set('areas', e.target.value)} />
              </Field>
              <div className="nt-pair">
                <Field label="District"><input className="nt-input" value={form.district} onChange={(e) => set('district', e.target.value)} /></Field>
                <Field label="Reference"><input className="nt-input" value={form.reference ?? ''} onChange={(e) => set('reference', e.target.value)} /></Field>
              </div>
              <Field label="Reason"><input className="nt-input" value={form.reason} onChange={(e) => set('reason', e.target.value)} /></Field>
              <Field label="How supply returns"><textarea className="nt-input" rows={2} value={form.recovery_note} onChange={(e) => set('recovery_note', e.target.value)} /></Field>
              <Field label="Advice to residents" hint="One per line.">
                <textarea className="nt-input" rows={2} value={form.advice} onChange={(e) => set('advice', e.target.value)} />
              </Field>
              <div className="nt-pair">
                <Field label="Other supply"><input className="nt-input" value={form.alternative_supply ?? ''} onChange={(e) => set('alternative_supply', e.target.value)} /></Field>
                <Field label="Contact"><input className="nt-input" value={form.contact ?? ''} onChange={(e) => set('contact', e.target.value)} /></Field>
              </div>
              <div className="nt-row nt-publish">
                <button className="pt-btn primary" disabled={busy !== '' || !complete} onClick={onPublish}>{busy === 'publish' ? 'Publishing…' : 'Publish to homes'}</button>
                <button className="pt-btn" disabled={busy !== ''} onClick={() => { setForm(null); setIssues([]); setError(''); }}>Discard</button>
                {!complete && <span className="nt-hint">Needs a title, a start and an end.</span>}
              </div>
            </div>
          )}
          {error && <div className="nt-error" role="alert">{error}</div>}
        </section>

        <div className="nt-side">
          {draft && (
            <section className="nt-preview" aria-label="What homes see">
              <h2>What homes see</h2>
              <article className="nt-card">
                <div className="nt-card-top">
                  <span className="nt-type"><Icon name={typeOf(draft.type).icon} size={15} />{typeOf(draft.type).label}</span>
                  <span>Township management office</span>
                </div>
                <h3>{draft.title || 'Title homeowners see'}</h3>
                <div className="nt-card-when pt-num">
                  {draft.starts_at && draft.ends_at ? `${when(draft.starts_at)} to ${when(draft.ends_at)}` : 'Dates not set'}
                  {draft.areas.length > 0 && ` · ${draft.areas.join(', ')}`}
                </div>
                {bodyOf(draft) && <p>{bodyOf(draft)}</p>}
                <div className="nt-card-impact">
                  <b>What this means for your home</b>
                  Penjaga adds this for each house from its own record: which taps stop, how long the roof tank lasts, what to unplug.
                </div>
              </article>
            </section>
          )}

          <section className="nt-list" aria-label="Published notices">
            <h2>Your notices</h2>
            {notices === null && !offline && <div className="pt-row pt-skel nt-skel"><span><i /><i /></span></div>}
            {notices !== null && notices.length === 0 && (
              <div className="pt-empty"><b>No notices yet</b><span>Notices you publish show here, newest first.</span></div>
            )}
            {(notices ?? []).map((n) => <NoticeRow key={n.id} notice={n} onChanged={load} />)}
          </section>
        </div>
      </div>
    </>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="nt-field">
      <span className="nt-label">{label}</span>
      {children}
      {hint && <span className="nt-hint">{hint}</span>}
    </label>
  );
}

function NoticeRow({ notice: n, onChanged }: { notice: Notice; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const { day, month } = dayParts(n.starts_at);
  const withdrawn = n.status === 'withdrawn';
  return (
    <div className="nt-item" data-withdrawn={withdrawn}>
      <div className="nt-date pt-num" aria-hidden="true"><b>{day}</b><span>{month}</span></div>
      <div className="nt-item-main">
        <div className="pt-title">{n.title}</div>
        <div className="pt-sub pt-num">{when(n.starts_at)} to {when(n.ends_at)}</div>
        <div className="nt-tags">
          <span className="pt-pill" data-tone={withdrawn ? 'quiet' : 'safe'}>{withdrawn ? 'Withdrawn' : 'Published'}</span>
          {!n.reaches_house && <span className="pt-pill" data-tone="quiet">No Penjaga homes in these areas</span>}
        </div>
      </div>
      {!withdrawn && (
        <button className="pt-btn sm" disabled={busy} onClick={async () => {
          setBusy(true);
          try { await withdrawNotice(n.id); await onChanged(); } finally { setBusy(false); }
        }}>Withdraw</button>
      )}
    </div>
  );
}
