// The app's single door to the backend. Screens never import the stand-in or the network directly.
//   VITE_BACKEND=standin (default): the local stand-in (src/standin), so the whole journey works with no backend.
//   VITE_BACKEND=api: the real Core API (src/api/remote.ts) with the same shapes (src/api/types.ts).
// In api mode, wellbeing still uses the stand-in: the Core API has no wellbeing check.
import { useEffect, useState, useSyncExternalStore } from 'react';
import { subscribe, getWorld } from '../standin/world';
import * as agent from '../standin/agent';
import { flowNow, elecAt } from '../standin/sim';
import * as remote from './remote';
import type { ChatMessage, VendorCategory } from './types';

export type { View } from '../standin/agent';

export const BACKEND: 'standin' | 'api' = import.meta.env.VITE_BACKEND === 'api' ? 'api' : 'standin';
const real = BACKEND === 'api';

// Recompute the derived view whenever the world changes (and on the stand-in's clock tick).
let cache: { world: unknown; t: number; v: agent.View } | null = null;
function snapshot(): agent.View {
  const w = getWorld();
  const now = Date.now();
  if (!cache || cache.world !== w || now - cache.t > 4000) cache = { world: w, t: now, v: agent.view(now) };
  return cache.v;
}
export function useView(): agent.View {
  return useSyncExternalStore(real ? remote.subscribe : subscribe, real ? remote.snapshot : snapshot);
}

const standinVendors = (category: VendorCategory) => agent.vendors(category === 'plumber' ? 'plumber' : 'electrician');

export const api = {
  runGuidedCheck: real ? remote.runGuidedCheck : agent.runGuidedCheck,
  book: real ? remote.book : agent.book,
  sendToManagement: real ? remote.sendToManagement : agent.sendToManagement,
  dismiss: real ? remote.dismiss : agent.dismiss,
  ackWellbeing: agent.ackWellbeing, // the Core API has no wellbeing check (view.wellbeing is always null)
  setConsent: real ? remote.setConsent : agent.setConsent,
  markToured: real ? remote.markToured : agent.markToured,
  vendors: (category: VendorCategory) => (real ? remote.cachedVendors(category) : undefined) ?? standinVendors(category),
  // The real chat answers in a few seconds (a promise); the stand-in answers at once. Agent.tsx handles both.
  ask: (real ? remote.ask : agent.ask) as (q: string, ctx?: { caseId?: string }) => ChatMessage | Promise<ChatMessage>,
  // Contract IDs get the house record's names; IDs from the 3D spec fall back to the stand-in's naming.
  objectName: (id: string) => (real ? remote.contractName(id) : undefined) ?? agent.objectName(id),
  roomName: (id?: string | null) =>
    (real && id ? remote.contractName(id) ?? remote.contractName(`room_${id}`) : undefined) ?? agent.roomName(id),
};

/** Now, on the house's clock: the simulator's house time with the real backend, the computer's clock otherwise. */
export const houseNow = (): number => (real ? remote.houseNow() : Date.now());

export function useNow(ms = 30000) {
  const [now, setNow] = useState(houseNow());
  useEffect(() => { const t = setInterval(() => setNow(houseNow()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

// Live readings for the "listening" line. In api mode they come from GET /api/v1/live (the app holds no
// stream token); a value stays at its last reading if the backend misses one.
export function useLive() {
  const [r, setR] = useState(() => (real ? { water_lpm: 0, power_kw: 0 } : { water_lpm: flowNow(), power_kw: elecAt(Date.now()) }));
  useEffect(() => {
    if (real) {
      let alive = true;
      const poll = () => remote.live()
        .then((x) => { if (alive) setR((prev) => ({ water_lpm: x.water_lpm ?? prev.water_lpm, power_kw: x.power_kw ?? prev.power_kw })); })
        .catch(() => { /* keep the last reading */ });
      void poll();
      const t = setInterval(poll, 2500);
      return () => { alive = false; clearInterval(t); };
    }
    const t = setInterval(() => setR({ water_lpm: flowNow() + (Math.random() < 0.3 ? Math.random() * 0.4 : 0), power_kw: elecAt(Date.now()) * (0.9 + Math.random() * 0.2) }), 2500);
    return () => clearInterval(t);
  }, []);
  return r;
}
