// The Lab on the real backend: an operator console for the simulated house. Every control says what it is doing
// (a spinner while the request runs, no double clicks), what is running shows as a lit tile you can tap to stop,
// and each action ends in a banner saying what happens next and roughly when, at the simulator's current speed.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import * as lab from '../api/lab';
import { Icon } from '../components/ui';
import '../styles/lab.css';
import LabOutlook from './LabOutlook';

type Tone = 'water' | 'power' | 'appliance' | 'supply' | 'life';
const GROUPS: { id: lab.Scenario['group']; tone: Tone; icon: string; blurb: string }[] = [
  { id: 'Water', tone: 'water', icon: 'water', blurb: 'Leaks the night-flow check and the burst rule look for' },
  { id: 'Electrical safety', tone: 'power', icon: 'bolt', blurb: 'The hero layer: overloads, trips, earth leakage' },
  { id: 'Appliances', tone: 'appliance', icon: 'settings', blurb: 'Slow drift in the fridge and aircons' },
  { id: 'Supply', tone: 'supply', icon: 'notices', blurb: 'Mains cuts, and the notices that explain them' },
];
// house hours until detection is sure, for each "when" in the scenario list
const WHEN_H: Record<string, number> = { 'within minutes': 0.25, 'within an hour': 1, 'within hours': 6, 'within a day': 24, 'after 2 nights': 48, 'after some days': 96 };
const placeName = (id?: string | null) => lab.SCENARIOS.flatMap((s) => s.where).find(([w]) => w === id)?.[1] ?? id ?? '';
const scenarioOf = (type: string) => lab.SCENARIOS.find((s) => s.type === type);
const houseFmt = (ms: number) => new Date(ms).toLocaleString('en-MY', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kuala_Lumpur' });

type Banner = { tone: 'ok' | 'err' | 'info'; title: string; detail?: string; key: number };

export default function RealLab() {
  const [faults, setFaults] = useState<lab.SimFault[]>([]);
  const [findings, setFindings] = useState<Awaited<ReturnType<typeof lab.findings>>>([]);
  const [events, setEvents] = useState<lab.TruthRecord[]>([]);
  const [planned, setPlanned] = useState<Record<string, string>>({}); // normal-life events started here, not yet under way: type -> record id
  const [notices, setNotices] = useState<Awaited<ReturnType<typeof lab.notices>>>([]);
  const [runs, setRuns] = useState<lab.EvalRun[] | null>(null);
  const [reach, setReach] = useState(true);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [banner, setBanner] = useState<Banner | null>(null);
  const [house, setHouse] = useState<{ ms: number; speed: number | null } | null>(null);
  const lastTick = useRef<{ house: number; wall: number } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [f, d, st] = await Promise.all([lab.faults(), lab.findings(), lab.simStatus()]);
      const nowMs = st.published_until ? Date.parse(st.published_until) : 0;
      setFaults(f.filter((x) => lab.isRunning(x, nowMs))); // a stopped one drops out at once
      setFindings(d);
      setReach(true);
      if (st.published_until) {
        const ms = Date.parse(st.published_until), wall = Date.now(), prev = lastTick.current;
        const speed = prev && wall - prev.wall > 500 ? (ms - prev.house) / (wall - prev.wall) : null;
        lastTick.current = { house: ms, wall };
        // smoothed, so the display doesn't jitter between polls
        setHouse((h) => ({ ms, speed: speed && speed > 0 ? (h?.speed ? h.speed * 0.7 + speed * 0.3 : speed) : h?.speed ?? null }));
      }
    } catch {
      setReach(false);
    }
    // extras: if these fail, the rest of the Lab still works
    lab.truth().then((t) => setEvents(t.filter((r) => r.kind === 'legit_anomaly'))).catch(() => {});
    lab.notices().then((n) => setNotices(n.filter((x) => x.status === 'published'))).catch(() => {});
    lab.evalRuns().then(setRuns).catch(() => setRuns(null));
  }, []);
  useEffect(() => { void refresh(); const t = setInterval(() => void refresh(), 3000); return () => clearInterval(t); }, [refresh]);
  useEffect(() => { if (!banner) return; const t = setTimeout(() => setBanner(null), 7000); return () => clearTimeout(t); }, [banner]);

  const shownSpeed = house?.speed ? Math.max(1, Math.round(house.speed / 50) * 50) : null;
  const realTime = (houseH: number) => {
    const sp = house?.speed;
    if (!sp) return '';
    const min = (houseH * 3600) / sp / 60;
    return min < 1 ? 'under a minute' : `about ${Math.round(min)} min`;
  };
  // one request per control at a time; the banner says how it went
  const act = (key: string, work: () => Promise<Banner | void>) => async () => {
    if (busy.has(key)) return;
    setBusy((b) => new Set(b).add(key));
    try {
      const b = await work();
      if (b) setBanner(b);
      await refresh();
    } catch (e) {
      setBanner({ tone: 'err', title: "That didn't work", detail: e instanceof Error ? e.message.slice(0, 160) : String(e), key: Date.now() });
    } finally {
      setBusy((b) => { const n = new Set(b); n.delete(key); return n; });
    }
  };
  const liveOf = (type: string) => faults.find((f) => f.spec.fault_type === type);
  const happening = (type: string) => events.filter((r) => r.label === type && Date.parse(r.start) <= (house?.ms ?? 0) && (!r.end || Date.parse(r.end) > (house?.ms ?? 0))).sort((a, b) => (b.end ?? '').localeCompare(a.end ?? ''))[0];
  // a planned event becomes a happening one once its midnight has been sent
  useEffect(() => { setPlanned((p) => Object.fromEntries(Object.entries(p).filter(([t]) => !happening(t)))); }, [events]); // eslint-disable-line react-hooks/exhaustive-deps
  // every record of a type still to come or under way (several, if it was started more than once)
  const openRecords = (type: string) => events.filter((r) => r.label === type && (!r.end || Date.parse(r.end) > (house?.ms ?? 0))).map((r) => r.record_id);
  const startFault = (sc: lab.Scenario, where: string | null, name: string) => act(`${sc.type}:${where}`, async () => {
    const running = liveOf(sc.type);
    if (running && (running.spec.location_id ?? null) === where) {
      await lab.clearType(sc.type);
      return { tone: 'info', title: `Stopped: ${sc.label}${where ? ` in ${name}` : ''}`, detail: 'Detection clears the case once the readings are back to normal.', key: Date.now() };
    }
    await lab.inject(sc, where);
    const eta = realTime(WHEN_H[sc.when] ?? 24);
    return { tone: 'ok', title: `Started: ${sc.label}${where ? ` in ${name}` : ''}`, detail: `The homeowner app gets a case ${sc.when}${eta ? ` (${eta} at ×${shownSpeed})` : ''}.`, key: Date.now() };
  });

  const runningCount = (g: string) => faults.filter((f) => scenarioOf(f.spec.fault_type)?.group === g).length;
  const noticeLive = (u: 'water' | 'power') => notices.some((n) => n.utility === u);

  return (
    <div className="lab">
      {banner && (
        <div key={banner.key} className={`lab-banner ${banner.tone}`} role="status">
          <span className="lab-banner-icon"><Icon name={banner.tone === 'err' ? 'close' : banner.tone === 'ok' ? 'check' : 'info'} size={15} /></span>
          <span><b>{banner.title}</b>{banner.detail && <span>{banner.detail}</span>}</span>
          <button onClick={() => setBanner(null)} aria-label="Dismiss"><Icon name="close" size={14} /></button>
        </div>
      )}

      <header className="lab-head">
        <div>
          <div className="lab-eyebrow">Operator only</div>
          <h1>Penjaga Lab</h1>
          <p>Start something in the simulated house. Detection only sees the readings it causes, so a case appears in the homeowner app once detection is sure.</p>
        </div>
        <div className="lab-head-side">
          <span className={`lab-clock ${reach ? 'on' : 'off'}`}>
            <i />
            {reach ? (house ? <>House time <b>{houseFmt(house.ms)}</b>{shownSpeed ? <> · ×{shownSpeed}</> : null}</> : 'Connecting…') : "Can't reach the simulator. Is the backend running?"}
          </span>
          <a className="lab-btn ghost" href="/" target="_blank" rel="noreferrer">Open homeowner app <Icon name="chevron" size={13} /></a>
        </div>
      </header>

      <div className="lab-layout">
        <main className="lab-main">
          <LabOutlook />
          <div className="lab-groups">
          {GROUPS.map((g) => (
            <section key={g.id} className={`lab-card tone-${g.tone}`}>
              <SectionHead icon={g.icon} title={g.id} blurb={g.blurb} running={runningCount(g.id)} />
              {lab.SCENARIOS.filter((sc) => sc.group === g.id).map((sc) => {
                const live = liveOf(sc.type);
                const places: [string | null, string][] = sc.where.length ? sc.where : [[null, sc.durationH ? `Start (${sc.durationH} h)` : 'Start']];
                return (
                  <div className="lab-row" key={sc.type}>
                    <div className="lab-row-name"><b>{sc.label.replace(' (urgent)', '')}</b>{sc.label.includes('urgent') && <span className="lab-tag urgent">Urgent</span>}<span className="lab-when"><Icon name="calendar" size={12} />case {sc.when}</span></div>
                    <div className="lab-tiles">
                      {places.map(([id, name]) => {
                        const on = !!live && (live.spec.location_id ?? null) === id;
                        const key = `${sc.type}:${id}`;
                        return <Tile key={key} on={on} busy={busy.has(key)} onClick={startFault(sc, id, name)} label={name} />;
                      })}
                    </div>
                  </div>
                );
              })}
              {g.id === 'Supply' && (
                <div className="lab-row">
                  <div className="lab-row-name"><b>Notices from management</b><span className="lab-when">post one before a cut and the case says "planned works"</span></div>
                  <div className="lab-tiles">
                    {(['water', 'power'] as const).map((u) => {
                      const on = noticeLive(u), what = u === 'water' ? 'Water works notice' : 'Power outage notice';
                      return (
                        <Tile key={u} label={what} on={on} state="Posted · tap to withdraw" busy={busy.has(`n:${u}`)}
                          onClick={act(`n:${u}`, async () => {
                            if (on) { await lab.clearNotices(); return { tone: 'info', title: 'Notices withdrawn', detail: 'Cuts read as unexplained again.', key: Date.now() }; }
                            await lab.postNotice(u === 'water' ? 'water_works' : 'power_outage');
                            return { tone: 'ok', title: `${what} posted`, detail: `A ${u} cut now reads as planned works in the app.`, key: Date.now() };
                          })} />
                      );
                    })}
                  </div>
                </div>
              )}
            </section>
          ))}
          </div>

          <section className="lab-card tone-life">
            <SectionHead icon="home" title="Normal life" blurb="Looks unusual on a meter, but isn't a problem. The app should stay quiet. Tap a running one to stop it." running={lab.NORMAL_EVENTS.filter((e) => happening(e.type) || planned[e.type]).length} />
            <div className="lab-tiles">
              {lab.NORMAL_EVENTS.map((e) => {
                const now = happening(e.type);
                const waiting = !now && !!planned[e.type];
                const until = now?.end ? new Date(Date.parse(now.end) - 1).toLocaleDateString('en-MY', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Asia/Kuala_Lumpur' }) : null;
                return (
                  <Tile key={e.type} label={`${e.label} · ${e.days} days`} on={!!now || waiting} state={now ? `Happening${until ? ` until ${until}` : ''} · tap to stop` : waiting ? 'Starts at the next midnight · tap to cancel' : undefined} busy={busy.has(`l:${e.type}`)}
                    onClick={act(`l:${e.type}`, async () => {
                      if (now || waiting) {
                        // stop it: the simulator ends it from the next house day it hasn't sent yet (a planned one is dropped)
                        const ids = [...new Set([...openRecords(e.type), ...(planned[e.type] ? [planned[e.type]] : [])])];
                        await Promise.allSettled(ids.map((id) => lab.stopEvent(id)));
                        setPlanned((p) => { const n = { ...p }; delete n[e.type]; return n; });
                        return now
                          ? { tone: 'info', title: `Stopping: ${e.label}`, detail: "It ends at the next house midnight (today's readings are already sent).", key: Date.now() }
                          : { tone: 'info', title: `Cancelled: ${e.label}`, key: Date.now() };
                      }
                      const id = await lab.startEvent(e);
                      setPlanned((p) => ({ ...p, [e.type]: id }));
                      const eta = realTime(24);
                      return { tone: 'ok', title: `${e.label}: starts at the next house midnight`, detail: `For ${e.days} days. Expect ${e.expect}${eta ? `; midnight comes in at most ${eta}` : ''}.`, key: Date.now() };
                    })} />
                );
              })}
            </div>
          </section>

          <Scores runs={runs} busy={busy.has('eval')} onRun={act('eval', async () => { await lab.startEval('l1_v0'); return { tone: 'ok', title: 'Water leak test started', detail: 'It simulates 4 houses first: results in a few minutes.', key: Date.now() }; })} />
        </main>

        <aside className="lab-side">
          <section className="lab-card">
            <SectionHead icon="eye" title="Running in the house" running={faults.length} />
            {faults.length === 0 ? <p className="lab-empty">Nothing running. Pick something on the left.</p> : (
              <ul className="lab-list">
                {faults.map((f) => {
                  const sc = scenarioOf(f.spec.fault_type);
                  const tone = GROUPS.find((g) => g.id === sc?.group)?.tone ?? 'water';
                  return (
                    <li key={f.fault_id} className={`tone-${tone}`}>
                      <i className="dot" />
                      <span><b>{sc?.label.replace(' (urgent)', '') ?? f.spec.fault_type}</b>{f.spec.location_id && <span>{placeName(f.spec.location_id)}</span>}</span>
                      <button className="lab-x" disabled={busy.has(`x:${f.fault_id}`)} onClick={act(`x:${f.fault_id}`, async () => { await lab.clearFault(f.fault_id); return { tone: 'info', title: `Stopped: ${sc?.label ?? f.spec.fault_type}`, key: Date.now() }; })} aria-label="Stop">Stop</button>
                    </li>
                  );
                })}
              </ul>
            )}
            {faults.length > 0 && <button className="lab-btn ghost block" disabled={busy.has('all')} aria-busy={busy.has('all')} onClick={act('all', async () => { await lab.clearAll(); return { tone: 'info', title: 'Cleared all faults', detail: 'Detection clears the cases once the readings are back to normal.', key: Date.now() }; })}>{busy.has('all') && <span className="spin" />}Clear all faults</button>}
          </section>

          <section className="lab-card">
            <SectionHead icon="scan" title="What detection sees" running={findings.filter((f) => f.status === 'active').length} />
            {findings.length === 0 ? <p className="lab-empty">No findings yet. They appear once detection is sure.</p> : (
              <ul className="lab-list">
                {findings.map((d, i) => (
                  <li key={i} className={`sev-${d.severity} ${d.status === 'active' ? '' : 'past'}`}>
                    <i className="dot" />
                    <span><b>{d.type.replaceAll('_', ' ').replace(/^./, (ch) => ch.toUpperCase())}</b><span>{d.status === 'active' ? d.severity.replace('_', ' ') : 'cleared'}</span></span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="lab-card">
            <SectionHead icon="wrench" title="Vendor jobs" blurb="After the owner books a vendor in the app, finish the job here." />
            <div className="lab-stack">
              {([['Plumber', 'L1'], ['Electrician', 'L2'], ['Aircon service', 'L3']] as const).map(([trade, layer]) => (
                <button key={trade} className="lab-btn" disabled={busy.has(`j:${layer}`)} aria-busy={busy.has(`j:${layer}`)} onClick={act(`j:${layer}`, async () => {
                  const n = await lab.completeJobs(layer);
                  return n
                    ? { tone: 'ok', title: `${trade} finished ${n} job${n > 1 ? 's' : ''}`, detail: 'The case shows the visit as done; Penjaga checks the readings.', key: Date.now() }
                    : { tone: 'info', title: `No open ${trade.toLowerCase()} booking`, detail: 'Book one on the case in the homeowner app first.', key: Date.now() };
                })}>{busy.has(`j:${layer}`) && <span className="spin" />}{trade} completes the job</button>
              ))}
            </div>
          </section>
          <p className="lab-foot">To reset the house, restart the backend (scripts/dev_stack.py).</p>
        </aside>
      </div>
    </div>
  );
}

function SectionHead({ icon, title, blurb, running }: { icon: string; title: string; blurb?: string; running?: number }) {
  return (
    <div className="lab-sec-head">
      <span className="lab-sec-icon"><Icon name={icon} size={16} /></span>
      <div><h2>{title}</h2>{blurb && <p>{blurb}</p>}</div>
      {!!running && <span className="lab-count"><i />{running} running</span>}
    </div>
  );
}

// A place to start something: lit while it runs (tap again to stop), a spinner while the request is out.
function Tile({ label, on, busy, state, onClick }: { label: ReactNode; on: boolean; busy: boolean; state?: string; onClick: () => void }) {
  return (
    <button className={`lab-tile ${on ? 'on' : ''}`} onClick={onClick} disabled={busy} aria-busy={busy} aria-pressed={on}>
      {busy ? <span className="spin" /> : on ? <i className="pulse" /> : null}
      <span>{label}</span>
      {on && !busy && <small>{state ?? 'Running · tap to stop'}</small>}
    </button>
  );
}

const dayText = (iso: string) => new Date(iso).toLocaleDateString('en-MY', { day: 'numeric', month: 'short', timeZone: 'Asia/Kuala_Lumpur' });
// The detectors running live, in the owner's words (services/detect/deployment.toml); others are candidates.
const DETECTOR: Record<string, string> = {
  l1_mnf_baseline: 'Night-flow leak check',
  l1_burst_rule: 'Burst pipe rule',
  l2_safety_floor: 'Electrical safety rules',
  l2_leakage_baseline: 'Earth-leakage trend',
  l3_health_baseline: 'Appliance health',
  l5_supply: 'Water and power cuts',
};
const SUITE: Record<string, string> = { l1_v0: 'Water leaks', l2_v0: 'Electrical safety', l3_v0: 'Appliances', l5_v0: 'Water and power cuts' };
const faultName = (type: string) => scenarioOf(type)?.label.replace(' (urgent)', '') ?? type;
const pct = (v: number | null | undefined) => (v == null ? 'n/a' : `${Math.round(v * 100)}%`);
const within = (h: number | null | undefined) => (h == null ? '' : h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)} h` : `${(h / 24).toFixed(1)} days`);

/** How well detection works: the latest finished evaluation of each suite (M11, GET /eval/v1/runs), per fault type,
 * the live detectors counted together (a fault is found if any of them found it; false alarms add up). */
function Scores({ runs, busy, onRun }: { runs: lab.EvalRun[] | null; busy: boolean; onRun: () => void }) {
  const done = (runs ?? []).filter((r) => r.status === 'complete' && r.results);
  const latest = Object.values(done.reduce<Record<string, lab.EvalRun>>((by, r) => {
    const held = by[r.suite];
    const better = !held || (r.split === 'test' && held.split !== 'test') || (r.split === held.split && r.created_at > held.created_at);
    return better ? { ...by, [r.suite]: r } : by;
  }, {})).sort((a, b) => a.suite.localeCompare(b.suite));
  const running = (runs ?? []).filter((r) => r.status === 'queued' || r.status === 'running');
  const failed = (runs ?? []).filter((r) => r.status === 'failed').sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const why = failed?.error ? (/free|disk|space/i.test(failed.error) ? 'Not enough free disk: the test needs at least 5 GB free.' : /Access is denied/.test(failed.error) ? 'Windows blocked a folder rename. Restart the backend (scripts/dev_stack.py) and try again.' : failed.error.split('\n').filter(Boolean).pop()?.slice(0, 140)) : null;
  return (
    <section className="lab-card tone-score">
      <SectionHead icon="check" title="How well detection works" blurb="Simulated houses the detectors never saw, with real faults and normal-life events, scored against what really happened." />
      {runs === null && <p className="lab-empty">Can't reach the evaluation service (port 8704).</p>}
      {running.length > 0 && <p className="lab-progress"><span className="spin" />Testing now: {running.map((r) => SUITE[r.suite] ?? r.suite).join(', ')}. This takes a few minutes.</p>}
      {runs !== null && latest.length === 0 && running.length === 0 && (
        <div className="lab-scores-empty">
          <p>No results on this computer yet.{why ? <> Last attempt failed: <b>{why}</b></> : ' A quick test simulates 4 houses with water leaks and scores the live checks.'}</p>
          <button className="lab-btn primary" disabled={busy} aria-busy={busy} onClick={onRun}>{busy && <span className="spin" />}Run a quick water leak test</button>
        </div>
      )}
      <div className="lab-scores">
        {latest.map((r) => {
          const live = Object.entries(r.results!).filter(([name]) => DETECTOR[name]).flatMap(([, d]) => Object.values(d.layers));
          const types = new Map<string, lab.FaultTypeScore>();
          for (const m of live) for (const [ft, t] of Object.entries(m.by_fault_type ?? {})) {
            const held = types.get(ft);
            if (t.n > 0 && (!held || (t.recall ?? 0) > (held.recall ?? 0))) types.set(ft, t);
          }
          const alarms = live.reduce((a, m) => a + (m.false_alarms_per_house_month ?? 0), 0);
          return (
            <div key={r.run_id} className="lab-score">
              <div className="lab-score-head"><b>{SUITE[r.suite] ?? r.suite}</b><span>{r.episodes ?? '?'} houses · {r.split === 'test' ? 'sealed test set' : 'validation set'} · {dayText(r.finished_at ?? r.created_at)}</span></div>
              {[...types].map(([ft, t]) => (
                <div key={ft} className="lab-meter">
                  <span>{faultName(ft)}</span>
                  <span className="bar"><i style={{ width: `${Math.round((t.recall ?? 0) * 100)}%` }} /></span>
                  <b>{pct(t.recall)}</b>
                  <small>{t.ttd_hours_median != null ? `within ${within(t.ttd_hours_median)}` : ''}</small>
                </div>
              ))}
              <div className="lab-score-foot">False alarms: <b>{Number(alarms.toFixed(2))}</b> per house a month · checks: {Object.keys(r.results!).filter((n) => DETECTOR[n]).map((n) => DETECTOR[n]).join(', ')}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
