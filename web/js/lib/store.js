// Single application store. set() merges shallowly and schedules one render
// per animation frame, however many updates arrive in between.
export function createStore(initial) {
  let state = initial;
  const listeners = new Set();
  let scheduled = false;

  const flush = () => {
    scheduled = false;
    for (const listener of listeners) listener(state);
  };

  // Render on the next frame; the timer covers windows where the browser
  // pauses animation frames (occluded or embedded views).
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    let done = false;
    const run = () => {
      if (done) return;
      done = true;
      flush();
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
    setTimeout(run, 120);
  };

  return {
    get: () => state,
    set(update) {
      const patch = typeof update === 'function' ? update(state) : update;
      if (!patch) return;
      state = { ...state, ...patch };
      schedule();
    },
    /** Merge into one nested slice: store.merge('lobbyUi', { tab: 'all' }). */
    merge(slice, patch) {
      const current = state[slice] || {};
      const next = typeof patch === 'function' ? patch(current) : patch;
      state = { ...state, [slice]: { ...current, ...next } };
      schedule();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    flush,
  };
}
