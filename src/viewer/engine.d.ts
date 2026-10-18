// Types for the framework-free 3D engine (engine.js).
import type { FocusCommand, Severity } from '../api/types';

export interface ElementInfo {
  id: string; kind: string; layer: string; floor: string; room: string | null; label: string | null;
  system: string | null; d_mm: number | null; rating_a: number | null; circuit_kind: string | null;
  links: string[] | null; meta: Record<string, unknown> | null;
}
export interface EngineState { view: string; water: boolean; electrical: boolean; xray: boolean; furniture: boolean; drill: boolean }
export interface DrillResult { wall: ElementInfo; services: (ElementInfo & { from_m: number; to_m: number })[] }
type Events = { select: ElementInfo | null; hover: ElementInfo | null; state: EngineState; drill: DrillResult | null; focusDone: { object_ids: string[] } };

export interface PenjagaEngine {
  mount(el: HTMLElement): void;
  unmount(el: HTMLElement): void;
  setInteractive(on: boolean): void;
  setAutoRotate(on: boolean): void;
  setInsets(inset: { left?: number }): void;
  frame(duration?: number, opts?: { tight?: number }): void;
  setView(view: string, opts?: { reframe?: boolean }): void;
  setLayer(name: 'water' | 'electrical', on: boolean): void;
  setXray(on: boolean): void;
  setDrill(on: boolean): void;
  getState(): EngineState;
  focus(cmd: FocusCommand): boolean;
  focusRoom(floor: string, roomId: string): boolean;
  focusId(id: string): boolean;
  flag(ids: string[], severity?: Severity, opts?: { label?: string }): void;
  clearFlags(): void;
  clearFocus(opts?: { reframe?: boolean }): void;
  drillCheck(wallId: string): DrillResult | null;
  describe(id: string): ElementInfo | null;
  has(id: string): boolean;
  on<K extends keyof Events>(ev: K, f: (data: Events[K]) => void): () => void;
  reduceMotion: boolean;
}
export function createEngine(spec: unknown, opts?: { onProgress?: (p: number) => void; lite?: boolean }): Promise<PenjagaEngine>;
export const SEVERITY_COLOR: Record<Severity, number>;
