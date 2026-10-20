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

export interface RouteShape {
  legs: { floor: string; points: [number, number][] }[];
  stair: { foot: [number, number, number]; top: [number, number, number]; up: boolean } | null;
  to?: { id?: string };
}
export type InspectPhase = 'walk' | 'look' | 'xray' | 'found' | 'done';
export interface Inspection { image: string; video: Blob | null; room: string | null }

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
  /** A beam crosses the house; rooms in `rooms` (`${floor}:${room}` → severity) show their problem, the rest a tick. */
  scan(rooms?: Record<string, Severity>): Promise<void>;
  /** Follow a pipe or circuit from its source to the problem; returns the animation's length in seconds. */
  trace(ids: string[], severity?: Severity): number;
  roomOf(id: string): string | null;
  /** Walk into the room at eye level, x-ray the wall, mark the fault red; resolves with the captured shot and clip
   * (null if it was cancelled, e.g. by clearFocus). Phases: walk, look, xray, found, done. */
  inspect(cmd: FocusCommand, opts?: { onPhase?: (phase: InspectPhase, info: { room?: string | null }) => void; record?: boolean }): Promise<Inspection | null>;
  capture(label?: string): string;
  /** Draw the walk from where the owner is to where they need to be (src/nav/route.ts), framed, walls see-through;
   * the destination device glows. Lasts until clearRoute() or clearFocus(). */
  route(r: RouteShape, opts?: { from?: string; to?: string; color?: number }): boolean;
  clearRoute(): void;
  /** The Lab's "what if they wait?": the case's part glows at the severity reached, with a line; a leak's water stands
   * beside the house as a cube to scale. Same `key` updates in place; a new key frames the view. */
  projection(p: { key: string; layer: string; ids: string[]; severity: Severity; label: string; waterM3?: number | null; maxWaterM3?: number | null }): boolean;
  clearProjection(): void;
  describe(id: string): ElementInfo | null;
  has(id: string): boolean;
  on<K extends keyof Events>(ev: K, f: (data: Events[K]) => void): () => void;
  reduceMotion: boolean;
}
export function createEngine(spec: unknown, opts?: { onProgress?: (p: number) => void; lite?: boolean }): Promise<PenjagaEngine>;
export const SEVERITY_COLOR: Record<Severity, number>;
