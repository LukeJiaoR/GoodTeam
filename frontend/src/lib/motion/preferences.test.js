import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeMotionPreference, shouldReduceMotion } from './preferences.js';

test('system preference follows a non-reducing system setting', () => {
  assert.equal(shouldReduceMotion('system', false), false);
});

test('system preference follows a reducing system setting', () => {
  assert.equal(shouldReduceMotion('system', true), true);
});

test('explicit reduce applies regardless of the system setting', () => {
  assert.equal(shouldReduceMotion('reduce', false), true);
  assert.equal(shouldReduceMotion('reduce', true), true);
});

test('invalid and null saved values normalize to system', () => {
  for (const value of ['invalid', '', null, undefined]) {
    assert.equal(normalizeMotionPreference(value), 'system');
    assert.equal(shouldReduceMotion(value, false), false);
    assert.equal(shouldReduceMotion(value, true), true);
  }
});

test('a saved full-motion-like value cannot override a reducing system setting', () => {
  assert.equal(shouldReduceMotion('no-preference', true), true);
});
