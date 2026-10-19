import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router';
import { EngineProvider } from './engine';
import { useView } from '../api/client';
import Agent from '../screens/Agent';
import Tour from '../screens/Tour';
import Lab from '../screens/Lab';
import Sdp from '../screens/Sdp';

function Home({ panel }: { panel?: 'notices' | 'settings' | 'record' }) {
  const v = useView();
  const nav = useNavigate();
  const open = v.cases.filter((c) => c.status !== 'resolved');
  useEffect(() => { if (!v.toured) nav('/welcome', { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { document.title = open.length ? `(${open.length}) Penjaga` : 'Penjaga'; }, [open.length]);
  return <Agent panel={panel} />;
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
