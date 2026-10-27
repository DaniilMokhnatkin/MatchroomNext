// Entry point: read the boot block, wire router, store, rendering and input.
import { patch } from './lib/dom.js';
import { setToken } from './lib/api.js';
import { setLanguage, t } from './lib/i18n.js';
import { createRouter, VIEWS } from './lib/router.js';
import {
  store, startPolling, addFiles, openSettings, openDialog, closeDialog, toggleMenu, loadMatchSteam, loadOwner,
  refreshRadar, refreshLobbies, trackWindow,
} from './app/controller.js';
import { appView } from './views/shell.js';
import { stepMatch } from './views/matches.js';

const boot = JSON.parse(document.getElementById('boot').textContent || '{}');
setToken(boot.token);
setLanguage(boot.language || 'ru');
store.set({ boot });

const root = document.getElementById('app');

const fileInput = document.createElement('input');
fileInput.type = 'file';
fileInput.accept = '.fwr';
fileInput.multiple = true;
fileInput.hidden = true;
document.body.append(fileInput);

let afterRender = null;

const router = createRouter((route, restoreScroll) => {
  const previous = store.get().route;
  store.set({ route, ui: { ...store.get().ui, menu: false } });
  if (route.view === 'matches' && (route.id !== previous.id || previous.view !== 'matches')) {
    queueMicrotask(() => loadMatchSteam());
  }
  afterRender = restoreScroll;
});

const ctx = {
  router,
  pickFiles: () => fileInput.click(),
  openSettings,
  openPalette: () => {
    store.merge('ui', { palette: { query: '', index: 0 } });
    openDialog('palette');
  },
  openOpponent(steamId, matchId) {
    store.merge('matchesUi', { opponent: steamId || '', query: '', season: '', result: '', limit: 80 });
    router.go('matches', matchId || null);
  },
};

fileInput.addEventListener('change', () => {
  addFiles(fileInput.files, router);
  fileInput.value = '';
});

function syncDialogs() {
  for (const dialog of root.querySelectorAll('dialog[data-modal]')) {
    if (dialog.open) continue;
    dialog.showModal();
    dialog.querySelector('[autofocus]')?.focus();
  }
}

store.subscribe(state => {
  patch(root, appView(state, ctx));
  root.removeAttribute('aria-busy');
  syncDialogs();
  document.title = 'Matchroom Next — ' + t(state.route.view);
  if (afterRender) {
    const restore = afterRender;
    afterRender = null;
    restore();
  }
});

/* ---------- Keyboard ---------- */

const typing = target => target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));

document.addEventListener('keydown', event => {
  const state = store.get();
  if ((event.ctrlKey || event.metaKey) && (event.key === 'k' || event.key === 'K' || event.code === 'KeyK')) {
    event.preventDefault();
    if (state.ui.dialog === 'palette') closeDialog();
    else ctx.openPalette();
    return;
  }
  if (event.key === 'Escape' && state.ui.menu) {
    toggleMenu(false);
    requestAnimationFrame(() => document.querySelector('.account-btn')?.focus());
    return;
  }
  if (state.ui.dialog || typing(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;

  const view = state.route.view;
  if (/^[1-4]$/.test(event.key)) {
    router.go(VIEWS[Number(event.key) - 1]);
    event.preventDefault();
  } else if (event.key === '/') {
    const input = document.querySelector('#view .search-field input');
    if (input) { event.preventDefault(); input.focus(); input.select(); }
  } else if (event.key === '?') {
    openDialog('shortcuts');
  } else if (view === 'matches' && (event.key === 'j' || event.key === 'J' || event.key === 'о' || event.key === 'ArrowDown')) {
    event.preventDefault();
    stepMatch(state, ctx, 1);
  } else if (view === 'matches' && (event.key === 'k' || event.key === 'K' || event.key === 'л' || event.key === 'ArrowUp')) {
    event.preventDefault();
    stepMatch(state, ctx, -1);
  } else if (event.key === 'r' || event.key === 'R' || event.key === 'к') {
    if (view === 'overview') loadOwner(true);
    else if (view === 'matches') loadMatchSteam(true);
    else if (view === 'radar') refreshRadar();
    else refreshLobbies();
  }
});

/* ---------- Pointer: close menus and hints on outside click ---------- */

document.addEventListener('pointerdown', event => {
  if (store.get().ui.menu && !event.target.closest('[data-menu-root]')) toggleMenu(false);
  for (const hint of document.querySelectorAll('details.hint[open]')) {
    if (!hint.contains(event.target)) hint.removeAttribute('open');
  }
});

/* ---------- Drag and drop replays ---------- */

let dragDepth = 0;
const hasFiles = event => Array.from(event.dataTransfer?.types || []).includes('Files');
document.addEventListener('dragenter', event => {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth++;
  if (!store.get().ui.dragging) store.merge('ui', { dragging: true });
});
document.addEventListener('dragover', event => { if (hasFiles(event)) event.preventDefault(); });
document.addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) store.merge('ui', { dragging: false });
});
document.addEventListener('drop', event => {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  store.merge('ui', { dragging: false });
  addFiles(event.dataTransfer.files, router);
});

router.start();
startPolling();
if (boot.window) trackWindow();
