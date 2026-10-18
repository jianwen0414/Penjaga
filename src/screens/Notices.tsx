import { useView } from '../api/client';
import { Icon, fmtDay, fmtTime } from '../components/ui';

export default function Notices({ embedded = false }: { embedded?: boolean }) {
  const v = useView();
  const now = Date.now();
  return (
    <div className={embedded ? '' : 'page'}>
      {!embedded && <h1 className="serif" style={{ fontSize: 34 }}>Notices</h1>}
      <p className="muted">From your township management. Penjaga reads each one against your home's readings, so planned works don't raise false alarms.</p>
      <div className="stack" style={{ marginTop: 20 }}>
        {!v.notices.length && <div className="card pad muted">No notices right now.</div>}
        {v.notices.map((n) => {
          const live = n.starts_at <= now && now <= n.ends_at;
          return (
            <article key={n.id} className="card pad">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="chip" style={live ? { background: 'var(--info-soft)', color: 'var(--info)' } : undefined}>{live ? 'Happening now' : n.ends_at < now ? 'Ended' : 'Upcoming'}</span>
                <span className="small muted">{n.issuer}</span>
              </div>
              <h2 className="serif" style={{ fontSize: 22, margin: '10px 0 4px' }}>{n.title}</h2>
              <div className="small muted">{fmtDay(n.starts_at)} · {fmtTime(n.starts_at)}–{fmtTime(n.ends_at)} · {n.area}</div>
              <p>{n.body}</p>
              {n.impact && (
                <div className="booked" style={{ background: 'var(--ok-soft)' }}>
                  <span className="brand-mark" style={{ background: 'var(--ok)' }}><Icon name="shield" size={17} /></span>
                  <div><b>What this means for your home</b><div className="small">{n.impact}</div></div>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
