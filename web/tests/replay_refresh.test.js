import test from 'node:test';
import assert from 'node:assert/strict';
import { loadReplay, store, sync } from '../js/app/controller.js';

test('pending analysis refreshes automatically even when the state response is unchanged', async t => {
  let now = 10000;
  let attempts = 0;
  const id = 'new-match.fwr';
  t.mock.method(Date, 'now', () => now);
  const previousDocument = globalThis.document;
  globalThis.document = { hasFocus: () => true };
  t.after(() => { globalThis.document = previousDocument; });
  t.mock.method(globalThis, 'fetch', async path => {
    if (path.startsWith('/api/state')) {
      return { ok: true, text: async () => JSON.stringify({ revision: 'same', demo: true, onboarding_complete: true }) };
    }
    assert.ok(path.startsWith('/api/replay-analysis?'));
    attempts++;
    return attempts === 1
      ? { ok: false, status: 404, json: async () => ({ error: 'not ready' }) }
      : { ok: true, json: async () => ({ key: 'ready', players: [] }) };
  });
  store.set({ route: { view: 'replay', id }, app: { demo: true, onboarding_complete: true }, replays: {} });
  await loadReplay(id);
  assert.equal(store.get().replays[id].error, 'pending');
  await sync();
  assert.equal(attempts, 1);
  now += 2000;
  await sync();
  assert.equal(store.get().replays[id].data?.key, 'ready');
  assert.equal(attempts, 2);
});
