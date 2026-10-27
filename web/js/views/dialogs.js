// Modal dialogs and the command palette. Each dialog exists in the DOM only
// while open; the shell calls showModal() after it is inserted.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, getLanguage, dateShort } from '../lib/i18n.js';
import { filterMatches, pair, outcome, dateOf, opponentStats } from '../domain/matches.js';
import {
  store, closeDialog, openDialog, saveSettings, completeOnboarding, chooseAccount, quit, ownerId, nameOf, playerName,
  loadOwner, refreshRadar, refreshLobbies, toggleWatch, setRemoteLanguage, testNotification, checkUpdate, installUpdate,
} from '../app/controller.js';
import { button, toggle, callout, kbd } from '../ui/components.js';
import { resultText } from './common.js';

function modal({ name, title, sub, children, footer, size = 'md', className = '' }) {
  return h('dialog', {
    class: ['modal', 'modal-' + size, className], 'data-modal': name, key: 'dialog:' + name, 'aria-labelledby': name + '-title',
    onClose() { if (store.get().ui.dialog === name) closeDialog(); },
    onClick(event) { if (event.target === this) this.close(); },
  },
  h('div', { class: 'modal-inner' },
    title ? h('header', { class: 'modal-head' },
      h('div', null, h('h2', { id: name + '-title' }, title), sub ? h('p', { class: 'muted' }, sub) : null),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('close'), onClick: closeDialog }, icon('close'))) : null,
    h('div', { class: 'modal-body' }, children),
    footer ? h('footer', { class: 'modal-foot' }, footer) : null));
}

function settingsDialog(state) {
  const draft = state.settings || {};
  const notifications = state.app.notifications || {};
  const set = patch => store.merge('settings', patch);
  return modal({
    name: 'settings', title: t('settings'), sub: t('settingsSub'), size: 'lg',
    children: h('form', {
      id: 'settingsForm', class: 'settings-form',
      onSubmit(event) { event.preventDefault(); saveSettings(); },
    },
    h('fieldset', { class: 'settings-group' },
      h('legend', { class: 'caps' }, t('replays')),
      toggle({ checked: !!draft.watchEnabled, label: t('autoImport'), hint: t('autoImportHint'), onChange: value => set({ watchEnabled: value }) }),
      h('label', { class: 'field' },
        h('span', { class: 'field-label' }, t('replayFolders')),
        h('textarea', { rows: '3', spellcheck: 'false', value: draft.folders || '', placeholder: 'C:\\…\\Forts\\users\\…\\replays', onInput: event => set({ folders: event.target.value }) }),
        h('small', { class: 'field-hint' }, t('foldersHint')))),
    h('fieldset', { class: 'settings-group' },
      h('legend', { class: 'caps' }, t('account')),
      h('label', { class: 'field' },
        h('span', { class: 'field-label' }, t('fallbackId')),
        h('input', { type: 'text', inputmode: 'numeric', maxlength: '17', value: draft.myId || '', placeholder: '7656119…', onInput: event => set({ myId: event.target.value }) }),
        h('small', { class: 'field-hint' }, t('fallbackIdHint')))),
    h('fieldset', { class: 'settings-group' },
      h('legend', { class: 'caps' }, t('radar')),
      toggle({ checked: !!draft.radarEnabled, label: t('monitorPlayers'), hint: t('monitorHint'), onChange: value => set({ radarEnabled: value }) })),
    h('fieldset', { class: 'settings-group' },
      h('legend', { class: 'caps' }, t('notifications')),
      toggle({
        checked: !!draft.windowsNotifications, disabled: !notifications.available, label: t('windowsNotifications'),
        hint: t(notifications.available ? 'windowsNotificationsHint' : 'notificationsUnavailable'),
        onChange: value => set({ windowsNotifications: value }),
      }),
      notifications.available ? h('div', { class: 'settings-actions' },
        button({ label: t('testNotification'), icon: 'play', size: 'sm', busy: !!draft.testing, onClick: testNotification })) : null,
      notifications.blocked ? callout({ tone: 'warning', text: t('notificationsBlocked') }) : null),
    h('section', { class: 'settings-about' },
      h('img', { src: '/forts_icon.png', alt: '', width: '36', height: '36' }),
      h('div', null,
        h('strong', null, 'Matchroom Next ' + (state.app.app_version || '').replace(/^next-/, '')),
        h('p', { class: 'muted' }, t('aboutText')),
        h('p', { class: 'muted' }, t('originalCredit')),
        h('div', { class: 'settings-actions' }, button({ label: t('checkUpdates'), icon: 'refresh', size: 'sm', busy: !!draft.updateBusy, onClick: checkUpdate }),
          draft.updateAvailable ? button({ label: t('installUpdate'), variant: 'primary', size: 'sm', busy: !!draft.updateBusy, onClick: installUpdate }) : null),
        h('p', { class: 'muted', role: 'status', 'aria-live': 'polite' }, draft.updateMessage || ''))),
    draft.error ? callout({ tone: 'error', text: draft.error }) : null),
    footer: [
      button({ label: t('quitApp'), icon: 'power', variant: 'danger-ghost', onClick: () => openDialog('quit'), className: 'modal-foot-start' }),
      button({ label: t('cancel'), onClick: closeDialog }),
      button({ label: t('save'), variant: 'primary', type: 'submit', form: 'settingsForm', busy: !!draft.saving }),
    ],
  });
}

function welcomeDialog(state) {
  const found = (state.app.watch?.folders || []).length > 0;
  return modal({
    name: 'welcome', size: 'sm', className: 'modal-welcome',
    children: [
      h('img', { class: 'welcome-logo', src: '/forts_icon.png', alt: '', width: '56', height: '56' }),
      h('h2', { id: 'welcome-title' }, t('welcomeTitle')),
      h('p', { class: 'muted' }, t('welcomeText')),
      callout({ tone: found ? 'success' : 'warning', icon: found ? 'check' : 'folder', text: t(found ? 'foundFolders' : 'missingFolders') }),
      h('p', { class: 'field-hint' }, t('welcomeIsolation')),
    ],
    footer: [
      button({ label: t('configureFolders'), onClick: () => completeOnboarding(true) }),
      button({ label: t('start'), variant: 'primary', onClick: () => completeOnboarding(false), autofocus: true }),
    ],
  });
}

function accountDialog(state) {
  const ui = state.ui;
  return modal({
    name: 'account', title: t('otherAccount'), sub: t('accountSwitchHint'), size: 'sm',
    children: h('form', {
      id: 'accountForm', class: 'account-form',
      async onSubmit(event) {
        event.preventDefault();
        const value = (ui.accountInput || '').trim();
        try {
          await chooseAccount('manual', value);
          closeDialog();
        } catch (error) {
          store.merge('ui', { accountError: error.message || t('requestFailed') });
        }
      },
    },
    h('label', { class: 'field' },
      h('span', { class: 'field-label' }, 'Steam ID64'),
      h('input', {
        type: 'text', inputmode: 'numeric', maxlength: '17', placeholder: '7656119…', autofocus: true,
        value: ui.accountInput || '', onInput: event => store.merge('ui', { accountInput: event.target.value, accountError: '' }),
      })),
    ui.accountError ? callout({ tone: 'error', text: ui.accountError }) : null),
    footer: [
      button({ label: t('cancel'), onClick: closeDialog }),
      button({ label: t('selectAccount'), variant: 'primary', type: 'submit', form: 'accountForm' }),
    ],
  });
}

function quitDialog() {
  return modal({
    name: 'quit', title: t('quitTitle'), size: 'sm',
    children: h('p', { class: 'muted' }, t('quitText')),
    footer: [
      button({ label: t('cancel'), onClick: closeDialog, autofocus: true }),
      button({ label: t('quitApp'), icon: 'power', variant: 'danger', onClick: quit }),
    ],
  });
}

export const SHORTCUTS = [
  [['Ctrl', 'K'], 'shortcutPalette'],
  [['1', '–', '4'], 'shortcutViews'],
  [['/'], 'shortcutSearch'],
  [['J', 'K'], 'shortcutStep'],
  [['R'], 'shortcutRefresh'],
  [['?'], 'shortcutHelp'],
  [['Esc'], 'shortcutClose'],
];

function shortcutsDialog() {
  return modal({
    name: 'shortcuts', title: t('shortcuts'), size: 'sm',
    children: h('dl', { class: 'shortcut-list' }, SHORTCUTS.map(([keys, label]) => h('div', { class: 'shortcut' },
      h('dt', null, keys.map(key => key === '–' ? h('span', { class: 'muted' }, '–') : kbd(key))),
      h('dd', null, t(label))))),
  });
}

/* ---------- Command palette ---------- */

export function paletteItems(state, ctx) {
  const query = state.ui.palette.query.trim();
  const q = query.toLowerCase();
  const own = ownerId(state);
  const watch = state.app.watch || {};
  const commands = [
    { id: 'go-overview', group: 'groupNavigate', icon: 'overview', label: t('overview'), keys: ['1'], run: () => ctx.router.go('overview') },
    { id: 'go-matches', group: 'groupNavigate', icon: 'history', label: t('matches'), keys: ['2'], run: () => ctx.router.go('matches') },
    { id: 'go-radar', group: 'groupNavigate', icon: 'radar', label: t('radar'), keys: ['3'], run: () => ctx.router.go('radar') },
    { id: 'go-lobbies', group: 'groupNavigate', icon: 'lobbies', label: t('lobbies'), keys: ['4'], run: () => ctx.router.go('lobbies') },
    { id: 'add-replay', group: 'groupActions', icon: 'upload', label: t('addReplay'), run: () => ctx.pickFiles() },
    { id: 'refresh-rating', group: 'groupActions', icon: 'refresh', label: t('refreshRating'), run: () => loadOwner(true) },
    { id: 'refresh-radar', group: 'groupActions', icon: 'radar', label: t('refreshRadar'), run: refreshRadar },
    { id: 'refresh-lobbies', group: 'groupActions', icon: 'lobbies', label: t('refreshLobbies'), run: refreshLobbies },
    { id: 'toggle-watch', group: 'groupActions', icon: watch.enabled ? 'pause' : 'play', label: t(watch.enabled ? 'pauseImport' : 'resumeImport'), run: toggleWatch },
    { id: 'language', group: 'groupActions', icon: 'globe', label: t('switchLanguage'), run: () => setRemoteLanguage(getLanguage() === 'ru' ? 'en' : 'ru') },
    { id: 'settings', group: 'groupActions', icon: 'settings', label: t('settings'), run: () => ctx.openSettings() },
    { id: 'shortcuts', group: 'groupActions', icon: 'keyboard', label: t('shortcuts'), keys: ['?'], run: () => openDialog('shortcuts') },
    { id: 'quit', group: 'groupActions', icon: 'power', label: t('quitApp'), run: () => openDialog('quit') },
  ].filter(item => !q || item.label.toLowerCase().includes(q));

  if (!q) return commands;

  const opponents = opponentStats(state.matches, own, id => nameOf(id, state))
    .filter(row => row.steam_id && (row.name || '').toLowerCase().includes(q)).slice(0, 4)
    .map(row => ({
      id: 'opp-' + row.key, group: 'groupOpponents', icon: 'swords',
      label: t('matchesVs', { name: row.name }), hint: t('matchesCount', { count: row.total }),
      run: () => ctx.openOpponent(row.steam_id, row.lastId),
    }));

  const matches = filterMatches(state.matches, { query }, { ownId: own, nameOf: id => nameOf(id, state), dateText: dateShort }).slice(0, 6)
    .map(match => {
      const [, right] = pair(match, own);
      return {
        id: 'match-' + match.match_id, group: 'groupMatches', icon: 'history',
        label: playerName(right, state) + ' · ' + (match.map || 'Forts'),
        hint: dateShort(dateOf(match)) + ' · ' + resultText(outcome(match, own)),
        run: () => ctx.router.go('matches', match.match_id),
      };
    });

  return [...commands, ...opponents, ...matches];
}

function paletteDialog(state, ctx) {
  const items = paletteItems(state, ctx);
  const index = Math.min(state.ui.palette.index, Math.max(0, items.length - 1));
  const run = item => { closeDialog(); store.merge('ui', { palette: { query: '', index: 0 } }); item.run(); };
  let group = null;
  const rows = [];
  items.forEach((item, i) => {
    if (item.group !== group) { group = item.group; rows.push(h('li', { class: 'palette-group caps', role: 'presentation', key: 'g:' + group }, t(group))); }
    rows.push(h('li', {
      key: item.id, id: 'palette-' + i, role: 'option', 'aria-selected': String(i === index),
      class: ['palette-item', { 'is-active': i === index }],
      onPointermove() { if (store.get().ui.palette.index !== i) store.merge('ui', ui => ({ palette: { ...ui.palette, index: i } })); },
      onClick: () => run(item),
    },
    icon(item.icon),
    h('span', { class: 'palette-label ellipsis' }, item.label),
    item.hint ? h('span', { class: 'palette-hint muted ellipsis' }, item.hint) : null,
    item.keys ? h('span', { class: 'palette-keys' }, item.keys.map(kbd)) : null));
  });

  return h('dialog', {
    class: 'modal modal-palette', 'data-modal': 'palette', key: 'dialog:palette', 'aria-label': t('palette'),
    onClose() { if (store.get().ui.dialog === 'palette') closeDialog(); },
    onClick(event) { if (event.target === this) this.close(); },
  },
  h('div', { class: 'modal-inner' },
    h('label', { class: 'palette-search' },
      icon('search'),
      h('input', {
        type: 'text', autofocus: true, role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'palette-list',
        'aria-activedescendant': items.length ? 'palette-' + index : null,
        placeholder: t('palettePlaceholder'), value: state.ui.palette.query, autocomplete: 'off', spellcheck: 'false',
        onInput: event => store.merge('ui', { palette: { query: event.target.value, index: 0 } }),
        onKeydown(event) {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const step = event.key === 'ArrowDown' ? 1 : -1;
            store.merge('ui', ui => ({ palette: { ...ui.palette, index: (index + step + items.length) % Math.max(1, items.length) } }));
            requestAnimationFrame(() => document.querySelector('.palette-item.is-active')?.scrollIntoView({ block: 'nearest' }));
          } else if (event.key === 'Enter' && items[index]) {
            event.preventDefault();
            run(items[index]);
          }
        },
      }),
      kbd('Esc')),
    h('ul', { class: 'palette-list', id: 'palette-list', role: 'listbox' },
      rows.length ? rows : h('li', { class: 'palette-empty muted' }, t('paletteEmpty')))));
}

export function dialogs(state, ctx) {
  switch (state.ui.dialog) {
    case 'settings': return settingsDialog(state);
    case 'welcome': return welcomeDialog(state);
    case 'account': return accountDialog(state);
    case 'quit': return quitDialog();
    case 'shortcuts': return shortcutsDialog();
    case 'palette': return paletteDialog(state, ctx);
    default: return null;
  }
}

