// The developer-side view for the pitch (SDP value). Static and clearly illustrative; it does not need to work.
import { Icon } from '../components/ui';

const DEFECTS = [
  ['Toilet tank flush valves', 41], ['Outdoor socket moisture', 27], ['Aircon drain blockage', 22],
  ['Roof-tank float valves', 14], ['Water-heater earth leakage', 9],
];
const TOWNS = [['City of Elmina', 412], ['Bandar Bukit Raja', 318], ['Serenia City', 266], ['Ara Damansara', 141], ['USJ / Subang Jaya', 103]];

export default function Sdp() {
  return (
    <div className="sdp" style={{ minHeight: '100%' }}>
      <div className="page">
        <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div><div className="label">For the developer · anonymised, aggregated</div><h1 className="serif" style={{ fontSize: 34 }}>Penjaga across your townships</h1></div>
          <span className="chip" style={{ background: 'var(--heads-soft)', color: 'var(--heads)' }}>Illustrative figures for the pitch, not real data</span>
        </div>
        <div className="grid2" style={{ marginTop: 18, gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
          {[['Homes with Penjaga', '1,240'], ['Issues caught early (12 mo)', '387'], ['Electrical risks flagged', '61'], ['Water saved', '2.9 million L']].map(([l, v]) => (
            <div key={l} className="card pad"><div className="label">{l}</div><div className="kpi" style={{ marginTop: 8 }}>{v}</div></div>
          ))}
        </div>
        <div className="grid2" style={{ marginTop: 14 }}>
          <div className="card pad">
            <b>Recurring defects → next project's spec</b>
            <p className="small muted">Owners opt in to share anonymised cases. Patterns go back to design and procurement.</p>
            <div className="bars">{DEFECTS.map(([n, v]) => <div className="b" key={n}><span>{n}</span><i style={{ width: `${(Number(v) / 41) * 100}%` }} /><span>{v}</span></div>)}</div>
          </div>
          <div className="card pad">
            <b>Homes connected by township</b>
            <p className="small muted">Scales to all 28 townships: the 3D home is generated from each house type's floor plan at handover.</p>
            <div className="bars">{TOWNS.map(([n, v]) => <div className="b" key={n}><span>{n}</span><i style={{ width: `${(Number(v) / 412) * 100}%`, background: 'var(--info)' }} /><span>{v}</span></div>)}</div>
          </div>
        </div>
        <div className="grid2" style={{ marginTop: 14 }}>
          <div className="card pad">
            <b><Icon name="wrench" size={16} /> Built in during construction</b>
            <ul className="small" style={{ paddingLeft: 18 }}>
              <li>Flow sensor on the water inlet and a monitor in the distribution board, fitted while walls are open</li>
              <li>3D model and services drawings from the as-built set, generated per house type</li>
              <li>Indicative sensor kit cost: around RM150 per home at prototype scale (to be validated at volume)</li>
            </ul>
          </div>
          <div className="card pad">
            <b><Icon name="shield" size={16} /> Why it matters to SDP</b>
            <ul className="small" style={{ paddingLeft: 18 }}>
              <li>"SDP homes warn you before they break": a brand promise competitors can't copy quickly</li>
              <li>Fewer defect-liability call-outs, caught small and located precisely</li>
              <li>Home Passport: a verified health history that supports resale value</li>
              <li>Vetted-vendor marketplace: commission on pre-diagnosed jobs</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
