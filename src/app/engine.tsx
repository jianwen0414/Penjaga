// One shared 3D engine for the whole app. Screens place a <ViewerSlot>; the canvas moves into it, so the house
// loads once and stays loaded as the owner moves between Home, a case and My Home.
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import spec from '../../house.spec.json';
import { createEngine } from '../viewer/engine.js';

export type Engine = Awaited<ReturnType<typeof createEngine>>;
const Ctx = createContext<{ engine: Engine | null; progress: number; failed: boolean }>({ engine: null, progress: 0, failed: false });

let shared: Promise<Engine> | null = null;
export function EngineProvider({ children }: { children: ReactNode }) {
  const [engine, setEngine] = useState<Engine | null>(null);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    shared ??= createEngine(spec, { onProgress: setProgress, lite: new URLSearchParams(location.search).has('lite') });
    shared.then(setEngine).catch((e) => { console.error(e); setFailed(true); });
  }, []);
  return <Ctx.Provider value={{ engine, progress, failed }}>{children}</Ctx.Provider>;
}
export const useEngine = () => useContext(Ctx);

// Mounts the engine canvas here while this slot is on screen; `onReady` runs once the engine is attached.
export function ViewerSlot({ onReady, className = 'slot', label }: { onReady?: (e: Engine) => void | (() => void); className?: string; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const { engine, progress, failed } = useEngine();
  const ready = useRef(onReady);
  ready.current = onReady;
  useEffect(() => {
    if (!engine || !ref.current) return;
    const el = ref.current;
    engine.mount(el);
    const cleanup = ready.current?.(engine);
    return () => { if (typeof cleanup === 'function') cleanup(); engine.unmount(el); };
  }, [engine]);
  return (
    <div ref={ref} className={className} role="img" aria-label={label}>
      {!engine && (
        <div className="viewer-loading" style={{ ['--p' as string]: progress }}>
          {failed ? <span>3D view unavailable on this device.</span> : <><span>Loading your home</span><i /></>}
        </div>
      )}
    </div>
  );
}
