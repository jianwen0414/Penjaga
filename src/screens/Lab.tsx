// Penjaga Lab (PRD §13): the operator surface. Separate route, never linked from the homeowner app.
// It controls the simulated world (faults, management notices, job completion). Ground truth lives here and in
// the simulator only; the homeowner app learns about faults solely through detection on readings.
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { BACKEND } from '../api/client';
import * as lab from '../api/lab';
import { getWorld, subscribe, update, resetWorld } from '../standin/world';
import { completeBookings } from '../standin/agent';
import { detect } from '../standin/detect';
import { nightMinFlow, residualDaily, pressureNow, BASELINE_M3_PER_MONTH, TIME } from '../standin/sim';

const CISTERNS = [['fix_bath2_wc_cistern', 'Bathroom 2'], ['fix_bath1_wc_cistern', 'Bathroom 1'], ['fix_bath_master_wc_cistern', 'Master bath'], ['fix_bath_guest_wc_cistern', 'Suite bath']];

export default function Lab() {
  return BACKEND === 'api' ? <RealLab /> : <StandinLab />;
}

function StandinLab() {
  const w = useSyncExternalStore(subscribe, getWorld);
  const now = Date.now();
  const inject = (type: 'cistern_leak' | 'earth_leakage' | 'supply_drop' | 'quiet_suite', location?: string, onsetDays = 0) =>
    update((x) => { x.faults = x.faults.filter((f) => !(f.type === type && !f.cleared_at)); x.faults.push({ id: `f${Date.now()}`, type, location, started_at: Date.now() - onsetDays * TIME.DAY }); });
  const clear = (type: string) => update((x) => { x.faults.filter((f) => f.type === type && !f.cleared_at).forEach((f) => { f.cleared_at = Date.now(); }); });
  const postNotice = () => update((x) => {
    x.notices.push({ id: `n${Date.now()}`, type: 'water_disruption', title: 'Scheduled water supply disruption', area: 'Jalan Elmina Green 6 and 7',
      starts_at: now - 30 * 60e3, ends_at: now + 5 * TIME.HOUR, posted_at: now - TIME.DAY,
      body: 'Air Selangor is replacing a valve on the main serving your street. Supply will be interrupted during the works. Please store water for essential use.' });
  });
  const active = (t: string) => w.faults.some((f) => f.type === t && !f.cleared_at);
  const findings = detect(now);
  return (
    <div className="ops">
      <div className="page">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div><div className="label">Operator only</div><h1 className="serif" style={{ fontSize: 32 }}>Penjaga Lab</h1></div>
          <a className="btn" href="/" target="_blank" rel="noreferrer">Open homeowner app ↗</a>
        </div>
        <p className="muted">Inject faults into the simulated house. Detection only sees the resulting readings. Keep the homeowner app open in another tab: it updates live.</p>

        <div className="grid2" style={{ marginTop: 18 }}>
          <div className="card pad stack">
            <b>L1 · Water</b>
            <div className="small muted">Running toilet tank (≈0.125 L/min), onset 3 nights ago</div>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              {CISTERNS.map(([id, l]) => <button key={id} className="btn" onClick={() => inject('cistern_leak', id, 3)}>{l}</button>)}
            </div>
            <div className="row"><button className="btn" onClick={() => completeBookings('plumber')}>Plumber completes job</button><button className="btn" onClick={() => clear('cistern_leak')}>Clear leak</button></div>
            <div className="small muted">{active('cistern_leak') ? `Active at ${w.faults.find((f) => f.type === 'cistern_leak' && !f.cleared_at)?.location}` : 'No leak'}</div>
          </div>
          <div className="card pad stack">
            <b>L2 · Electrical</b>
            <div className="small muted">Earth leakage on the outdoor socket circuit, worse after rain, onset 5 days ago</div>
            <div className="row"><button className="btn" onClick={() => inject('earth_leakage', 'circ_sockets_outdoor', 5)}>Inject earth leakage</button></div>
            <div className="row"><button className="btn" onClick={() => completeBookings('electrician')}>Electrician completes job</button><button className="btn" onClick={() => clear('earth_leakage')}>Clear</button></div>
            <div className="small muted">{active('earth_leakage') ? 'Active' : 'None'}</div>
          </div>
          <div className="card pad stack">
            <b>L5 · Management + supply</b>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              <button className="btn" onClick={postNotice}>Post disruption notice</button>
              <button className="btn" onClick={() => inject('supply_drop')}>Supply drops</button>
              <button className="btn" onClick={() => { clear('supply_drop'); update((x) => { x.notices = []; }); }}>Restore + clear notices</button>
            </div>
            <div className="small muted">Pressure now: {pressureNow(now)} bar · notices: {w.notices.length}</div>
          </div>
          <div className="card pad stack">
            <b>Living-alone check</b>
            <div className="small muted">No water or power use in the ground-floor suite since 10pm</div>
            <div className="row"><button className="btn" onClick={() => inject('quiet_suite')}>Quiet morning in the suite</button><button className="btn" onClick={() => clear('quiet_suite')}>Clear</button></div>
          </div>
        </div>

        <div className="card pad" style={{ marginTop: 14 }}>
          <b>What detection sees</b>
          <div className="grid2 small" style={{ marginTop: 10 }}>
            <div>Night minimum flow (L/min): {nightMinFlow(7, now).map((n) => n.v).join(' · ')}</div>
            <div>Earth leakage, daily max (mA): {residualDaily(7, now).map((d) => `${d.v}${d.rain ? '☂' : ''}`).join(' · ')}</div>
            <div>Household baseline: {BASELINE_M3_PER_MONTH} m³/month</div>
          </div>
          <pre style={{ whiteSpace: 'pre-wrap', marginTop: 10 }}><code>{JSON.stringify(findings, null, 1)}</code></pre>
        </div>

        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn" onClick={() => resetWorld(true)}>Reset world</button>
          <button className="btn" onClick={() => resetWorld(false)}>Reset world + first-run tour</button>
        </div>
      </div>
    </div>
  );
}

// The Lab on the real backend: the same scenarios as simulator faults, notices and job completion through the Core
// API's operator routes, and detection's own findings. The homeowner app (VITE_BACKEND=api) updates live.
const REAL_CISTERNS = [['fix_bath2_wc_cistern', 'Bathroom 2'], ['fix_bath_master_wc_cistern', 'Master bath'], ['fix_bath3_wc_cistern', 'Bathroom 3'], ['fix_bath4_wc_cistern', 'Bathroom 4']];
const SCENARIO_TYPE: Record<lab.Scenario, string> = { cistern_leak: 'W2', earth_leakage: 'E2', supply_drop: 'U2' };

function RealLab() {
  const [faults, setFaults] = useState<lab.SimFault[]>([]);
  const [findings, setFindings] = useState<unknown[]>([]);
  const [note, setNote] = useState('');
  const refresh = useCallback(async () => {
    try {
      const [f, d] = await Promise.all([lab.faults(), lab.findings()]);
      setFaults(f);
      setFindings(d);
    } catch {
      setNote("Can't reach the simulator or detection. Start the backend: uv run python scripts/dev_stack.py");
    }
  }, []);
  useEffect(() => { void refresh(); const t = setInterval(() => void refresh(), 3000); return () => clearInterval(t); }, [refresh]);
  const run = (label: string, work: () => Promise<unknown>) => () => {
    setNote(`${label}…`);
    work().then(() => { setNote(`${label}: done`); void refresh(); }).catch((e) => setNote(`${label} failed: ${e instanceof Error ? e.message : e}`));
  };
  const active = (s: lab.Scenario) => faults.find((f) => f.spec.fault_type === SCENARIO_TYPE[s] && f.state !== 'cleared');
  const cistern = active('cistern_leak');
  return (
    <div className="ops">
      <div className="page">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div><div className="label">Operator only</div><h1 className="serif" style={{ fontSize: 32 }}>Penjaga Lab</h1></div>
          <a className="btn" href="/" target="_blank" rel="noreferrer">Open homeowner app ↗</a>
        </div>
        <p className="muted">Inject faults into the simulated house. Detection only sees the resulting readings, so a case appears once detection is sure (a leak needs two nights of house time). Keep the homeowner app open in another tab: it updates live.</p>
        {note && <div className="small muted" role="status">{note}</div>}

        <div className="grid2" style={{ marginTop: 18 }}>
          <div className="card pad stack">
            <b>L1 · Water</b>
            <div className="small muted">Running toilet tank, 0.5 L/min</div>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              {REAL_CISTERNS.map(([id, l]) => <button key={id} className="btn" onClick={run(`Leak in ${l}`, () => lab.inject('cistern_leak', id))}>{l}</button>)}
            </div>
            <div className="row"><button className="btn" onClick={run('Plumber completes the job', () => lab.completeJobs('plumber'))}>Plumber completes job</button><button className="btn" onClick={run('Clear leak', () => lab.clear('cistern_leak'))}>Clear leak</button></div>
            <div className="small muted">{cistern ? `Active at ${REAL_CISTERNS.find(([id]) => id === cistern.spec.location_id)?.[1] ?? cistern.spec.location_id}` : 'No leak'}</div>
          </div>
          <div className="card pad stack">
            <b>L2 · Electrical</b>
            <div className="small muted">Earth leakage on the yard socket circuit, 1.5 mA</div>
            <div className="row"><button className="btn" onClick={run('Earth leakage', () => lab.inject('earth_leakage', 'circ_sockets_yard'))}>Inject earth leakage</button></div>
            <div className="row"><button className="btn" onClick={run('Electrician completes the job', () => lab.completeJobs('electrician'))}>Electrician completes job</button><button className="btn" onClick={run('Clear earth leakage', () => lab.clear('earth_leakage'))}>Clear</button></div>
            <div className="small muted">{active('earth_leakage') ? 'Active' : 'None'}</div>
          </div>
          <div className="card pad stack">
            <b>L5 · Management + supply</b>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              <button className="btn" onClick={run('Post disruption notice', lab.postNotice)}>Post disruption notice</button>
              <button className="btn" onClick={run('Supply drops', () => lab.inject('supply_drop'))}>Supply drops</button>
              <button className="btn" onClick={run('Restore and clear notices', async () => { await lab.clear('supply_drop'); await lab.clearNotices(); })}>Restore + clear notices</button>
            </div>
            <div className="small muted">Supply: {active('supply_drop') ? 'off (6 hours, unless restored)' : 'normal'}</div>
          </div>
          <div className="card pad stack">
            <b>Living-alone check</b>
            <div className="small muted">Not part of the Core API yet: this check runs only with the stand-in backend.</div>
          </div>
        </div>

        <div className="card pad" style={{ marginTop: 14 }}>
          <b>What detection sees</b>
          <div className="small muted" style={{ marginTop: 6 }}>Simulator faults: {faults.filter((f) => f.state !== 'cleared').map((f) => `${f.spec.fault_type}${f.spec.location_id ? ` at ${f.spec.location_id}` : ''} (${f.state})`).join(' · ') || 'none'}</div>
          <pre style={{ whiteSpace: 'pre-wrap', marginTop: 10 }}><code>{JSON.stringify(findings, null, 1)}</code></pre>
        </div>

        <div className="small muted" style={{ marginTop: 14 }}>To reset the house, restart the backend (scripts/dev_stack.py).</div>
      </div>
    </div>
  );
}

