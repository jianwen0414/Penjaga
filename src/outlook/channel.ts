// The Lab's slider, mirrored into the homeowner app during a demo: the presenter drags "what if they wait?" in the Lab
// tab and the app tab plays the same future in its house. Tab to tab in the same browser (BroadcastChannel), so both
// must be open on the demo machine; nothing goes through the backend and nothing is stored.
export type FutureMessage =
  | { type: 'show'; case_id: string; x: number } // x: how far ahead, in the case's horizon unit
  | { type: 'end' };

const NAME = 'penjaga.future';

export function openFutureChannel(onMessage?: (m: FutureMessage) => void): { post(m: FutureMessage): void; close(): void } {
  if (typeof BroadcastChannel === 'undefined') return { post() {}, close() {} };
  const ch = new BroadcastChannel(NAME);
  if (onMessage) ch.onmessage = (e: MessageEvent<FutureMessage>) => { if (e.data && typeof e.data === 'object') onMessage(e.data); };
  return { post: (m) => ch.postMessage(m), close: () => ch.close() };
}
