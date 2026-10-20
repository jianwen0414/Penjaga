// The owner's words as they speak, in the chat: each word fades and lifts in as it's heard. Recognition revises its
// guess as it goes ("where's my" → "where is my"), so words are matched by position and text: a word that stays the
// same never animates again, a word that changes fades in afresh, and the words still being guessed are lighter
// until they settle. A blinking caret marks that Penjaga is still listening.
import { useRef } from 'react';

export default function LiveWords({ text, settled, listening }: { text: string; settled: string; listening: boolean }) {
  const words = text ? text.split(' ') : [];
  const settledCount = settled ? settled.split(' ').length : 0;
  // a stable key per word: its position and the version of the word at that position
  const seen = useRef<{ word: string; v: number }[]>([]);
  const keys = words.map((w, i) => {
    const prev = seen.current[i];
    const v = !prev ? 0 : prev.word.toLowerCase() === w.toLowerCase() ? prev.v : prev.v + 1;
    seen.current[i] = { word: w, v };
    return `${i}:${v}`;
  });
  seen.current.length = words.length;
  return (
    <span className="live-words" aria-live="off">
      {words.map((w, i) => (
        <span key={keys[i]} className={`lw ${i < settledCount ? 'settled' : 'guess'}`}>
          {w}{i < words.length - 1 ? ' ' : ''}
        </span>
      ))}
      {listening && <span className="lw-caret" aria-hidden="true" />}
      {!words.length && listening && <span className="lw-hint">Listening…</span>}
    </span>
  );
}
