import { describe, expect, test } from 'vitest';
import { placeFrom, normalise, pickFrom } from './place';

describe('picking from the choices Penjaga just offered', () => {
  const baths = ['bath_master', 'bath2', 'bath3', 'bath4'];
  const beds = ['master', 'bed2', 'bed3', 'bed4'];
  test.each([
    ['the master one', baths, 'bath_master'],
    ['number two', baths, 'bath2'],
    ['bathroom to', baths, 'bath2'],
    ['three', baths, 'bath3'],
    ['the downstairs one', baths, 'bath4'],
    ['flexi', baths, 'bath4'],
    ['master bedroom', beds, 'master'],
    ['the guest room', beds, 'bed4'],
    ['two', beds, 'bed2'],
  ])('%s is %s', (said, options, want) => expect(pickFrom(said, options)).toBe(want));
  test('nothing that sets one apart, or two that fit, is no answer', () => {
    expect(pickFrom('the bathroom', baths)).toBeNull();
    expect(pickFrom('the kitchen', baths)).toBeNull();
    expect(pickFrom('two or three', baths)).toBeNull();
  });
});

const room = (said: string) => { const p = placeFrom(said); return p.kind === 'room' ? p.room : p.kind; };

describe('where the owner says they are', () => {
  test.each([
    ["I'm in the kitchen", 'kitchen'],
    ['kitchen', 'kitchen'],
    ['dekat dapur', 'kitchen'],
    ["I'm in the living room watching TV", 'living'],
    ['ruang tamu', 'living'],
    ['upstairs in bedroom two', 'bed2'],
    ['bedroom 3', 'bed3'],
    ['in the second bedroom', 'bed2'],
    ['bathroom to', 'bath2'], // what recognition makes of "bathroom two"
    ['bedroom tree', 'bed3'],
    ['master bathroom', 'bath_master'],
    ["I'm in the master bedroom", 'master'],
    ['the en suite', 'bath_master'],
    ["Mak's room", 'bed4'],
    ['the guest room downstairs', 'bed4'],
    ['downstairs toilet', 'bath4'],
    ['at the back yard hanging clothes', 'yard'],
    ['at the car porch', 'porch'],
    ['near the front door', 'foyer'],
    ['family area upstairs', 'family'],
    ['kitchin', 'kitchen'], // one typo in a long word
  ])('%s is %s', (said, want) => expect(room(said)).toBe(want));

  test('a word several rooms answer to is a question back', () => {
    expect(placeFrom('in the bathroom')).toEqual({ kind: 'ambiguous', options: ['bath_master', 'bath2', 'bath3', 'bath4'], what: 'bathroom' });
    expect(placeFrom('my room').kind).toBe('ambiguous');
    expect(placeFrom("I'm upstairs").kind).toBe('ambiguous');
  });
  test('not at home is its own answer', () => {
    expect(room("I'm not home")).toBe('away');
    expect(room("I'm at work")).toBe('away');
    expect(room('tak ada di rumah')).toBe('away');
    expect(room("I'm not at home, I left the kitchen tap on")).toBe('away');
  });
  test('nothing recognisable is unknown', () => {
    expect(room('')).toBe('unknown');
    expect(room('hello there')).toBe('unknown');
  });
  test('numbers become digits, and only after a room word does "to" mean 2', () => {
    expect(normalise('Bathroom Two!')).toBe(' bathroom 2 ');
    expect(normalise('going to the kitchen')).toBe(' going to the kitchen ');
  });
});
