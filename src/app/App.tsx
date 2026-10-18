import { useEffect, useRef, useState } from 'react';
import { BrowserRouter, Routes, Route, Link, Navigate, useNavigate } from 'react-router';
import { EngineProvider } from './engine';
import { useView } from '../api/client';
import { Icon } from '../components/ui';
import Agent from '../screens/Agent';
import Tour from '../screens/Tour';
import Lab from '../screens/Lab';
import Sdp from '../screens/Sdp';

// A new case arriving while the app is open (stands in for a push notification).
function NewCaseToast() {
  const v = useView();
  const seen = useRef<Set<string> | null>(null);
  const [toast, setToast] = useState<{ id: string; title: string; sev: string; loc: string } | null>(null);
  useEffect(() => {
    const open = v.cases.filter((c) => c.status === 'open');
    if (!seen.current) { seen.current = new Set(open.map((c) => c.id)); return; }
    const fresh = open.find((c) => !seen.current!.has(c.id));
    open.forEach((c) => seen.current!.add(c.id));
    if (fresh) {
      setToast({ id: fresh.id, title: fresh.title, sev: fresh.severity, loc: fresh.location });
      const t = setTimeout(() => setToast(null), 8000);
      return () => clearTimeout(t);
    }
  }, [v.cases]);
  if (!toast) return null;
  return (
    <Link className="toast" to={`/cases/${toast.id}`} onClick={() => setToast(null)} role="status">
      <span className="agent-mark" style={{ background: toast.sev === 'urgent' ? 'var(--urgent)' : 'var(--act)' }}><Icon name={toast.sev === 'urgent' ? 'bolt' : 'water'} size={17} /></span>
      <span style={{ flex: 1 }}><b>Penjaga · {toast.title}</b><small>{toast.loc}</small></span>
      <Icon name="chevron" size={18} />
    </Link>
  );
}

function Home({ panel }: { panel?: 'notices' | 'settings' | 'record' }) {
  const v = useView();
  const nav = useNavigate();
  const open = v.cases.filter((c) => c.status !== 'resolved');
  useEffect(() => { if (!v.toured) nav('/welcome', { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { document.title = open.length ? `(${open.length}) Penjaga` : 'Penjaga'; }, [open.length]);
  return <><Agent panel={panel} /><NewCaseToast /></>;
}

export default function App() {
  return (
    <BrowserRouter>
      <EngineProvider>
        <Routes>
          <Route index element={<Home />} />
          <Route path="cases/:id" element={<Home />} />
          <Route path="notices" element={<Home panel="notices" />} />
          <Route path="settings" element={<Home panel="settings" />} />
          <Route path="passport" element={<Home panel="record" />} />
          <Route path="welcome" element={<Tour />} />
          {/* Operator + pitch surfaces: never linked from the homeowner app (PRD §13) */}
          <Route path="lab" element={<Lab />} />
          <Route path="sdp" element={<Sdp />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </EngineProvider>
    </BrowserRouter>
  );
}
