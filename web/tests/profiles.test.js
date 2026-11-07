import test from 'node:test';
import assert from 'node:assert/strict';
import { loadProfiles, store } from '../js/app/controller.js';

test('a throttled profile can load after its cooldown, without sending early retries', async t => {
  const id = '76561198310546409';
  let now = 10000;
  let calls = 0;
  t.mock.method(Date, 'now', () => now);
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    return { ok: true, json: async () => ({ profiles: { [id]: calls === 1
      ? { status: 'unavailable', throttled: true, retry_in: 35 }
      : { status: 'ok', name: 'Recovered' } } }) };
  });
  await loadProfiles([id]);
  now += 34000;
  await loadProfiles([id]);
  assert.equal(calls, 1);
  now += 1000;
  await loadProfiles([id]);
  assert.equal(store.get().profiles[id].name, 'Recovered');
  await loadProfiles([id]);
  assert.equal(calls, 2);
});
