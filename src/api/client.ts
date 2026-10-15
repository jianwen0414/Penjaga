// The app's single door to the backend. Today it is backed by the local stand-in (src/standin) so the whole
// journey works end to end; the Agent + Integration workstream swaps these calls for the real REST/SSE API
// with the same shapes (src/api/types.ts). Screens never import the stand-in directly.
import { useEffect, useState, useSyncExternalStore } from 'react';
import { subscribe, getWorld } from '../standin/world';
import * as agent from '../standin/agent';
import { flowNow, elecAt } from '../standin/sim';

export type { View } from '../standin/agent';

// Recompute the derived view whenever the world changes (and on the stand-in's clock tick).
let cache: { world: unknown; t: number; v: agent.View } | null = null;
function snapshot(): agent.View {
  const w = getWorld();
  const now = Date.now();
  if (!cache || cache.world !== w || now - cache.t > 4000) cache = { world: w, t: now, v: agent.view(now) };
  return cache.v;
}
export function useView(): agent.View {
  return useSyncExternalStore(subscribe, snapshot);
}

export const api = {
  runGuidedCheck: agent.runGuidedCheck,
  book: agent.book,
  sendToManagement: agent.sendToManagement,
  dismiss: agent.dismiss,
  ackWellbeing: agent.ackWellbeing,
  setConsent: agent.setConsent,
  markToured: agent.markToured,
  vendors: agent.vendors,
  ask: agent.ask,
  objectName: agent.objectName,
  roomName: agent.roomName,
};

export function useNow(ms = 30000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

// Live readings for the "listening" line (the real app subscribes to the readings stream).
export function useLive() {
  const [r, setR] = useState(() => ({ water_lpm: flowNow(), power_kw: elecAt(Date.now()) }));
  useEffect(() => {
    const t = setInterval(() => setR({ water_lpm: flowNow() + (Math.random() < 0.3 ? Math.random() * 0.4 : 0), power_kw: elecAt(Date.now()) * (0.9 + Math.random() * 0.2) }), 2500);
    return () => clearInterval(t);
  }, []);
  return r;
}
