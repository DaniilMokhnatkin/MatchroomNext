// Hash router: #/overview, #/live, #/matches, #/matches/<id>, #/casual, #/radar, #/lobbies.
// Back/forward work, and each view remembers its own scroll position.
export const VIEWS = ['overview', 'live', 'matches', 'casual', 'radar', 'lobbies'];
// Detail pages reached from links, not from the navigation rail.
const DETAIL = ['player', 'replay'];

export function parseHash(hash) {
  const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  if (DETAIL.includes(parts[0]) && parts[1]) return { view: parts[0], id: parts[1] };
  const view = VIEWS.includes(parts[0]) ? parts[0] : 'overview';
  return { view, id: view === 'matches' && parts[1] ? parts[1] : null };
}

export function routeHash(view, id) {
  return '#/' + view + (id ? '/' + encodeURIComponent(id) : '');
}

export function createRouter(onChange) {
  const scrolls = new Map();
  let current = parseHash(location.hash);

  const scroller = () => document.querySelector('[data-scroll-root]');

  const apply = () => {
    const next = parseHash(location.hash);
    const previous = current;
    const viewChanged = next.view !== previous.view;
    if (viewChanged) scrolls.set(previous.view, scroller()?.scrollTop || 0);
    current = next;
    onChange(next, () => {
      if (!viewChanged) return;
      const root = scroller();
      if (root) root.scrollTop = scrolls.get(next.view) || 0;
    });
  };

  window.addEventListener('hashchange', apply);

  return {
    current: () => current,
    go(view, id = null, { replace = false } = {}) {
      const hash = routeHash(view, id);
      if (location.hash === hash) return;
      if (replace) {
        history.replaceState(null, '', hash);
        apply();
      } else {
        location.hash = hash;
      }
    },
    start() {
      if (!location.hash || !location.hash.startsWith('#/')) history.replaceState(null, '', routeHash(current.view, current.id));
      onChange(current, () => {});
    },
  };
}
