// The owner's voice choices, kept on this device (Privacy panel): whether Penjaga speaks its answers aloud, and whether
// in an emergency it may open the microphone by itself for the one answer it asks for ("where are you now?").
import { useSyncExternalStore } from 'react';

export interface VoicePrefs { speak: boolean; emergencyListen: boolean }
const KEY = 'penjaga.voice.prefs';
const DEFAULTS: VoicePrefs = { speak: true, emergencyListen: true };

function load(): VoicePrefs {
  try { return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<VoicePrefs>) }; } catch { return DEFAULTS; }
}
let prefs = load();
const subs = new Set<() => void>();

export const voicePrefs = (): VoicePrefs => prefs;
export function setVoicePref<K extends keyof VoicePrefs>(key: K, value: VoicePrefs[K]): void {
  prefs = { ...prefs, [key]: value };
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* storage unavailable: this session only */ }
  subs.forEach((f) => f());
}
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
export const useVoicePrefs = (): VoicePrefs => useSyncExternalStore(subscribe, voicePrefs);
