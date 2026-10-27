// App shell: navigation rail, top bar with account menu, banners, toasts.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, getLanguage, time, duration } from '../lib/i18n.js';
import { routeHash } from '../lib/router.js';
import { activityValues } from '../domain/activity.js';
import {
  ownerId, ownerRating, nameOf, toggleMenu, chooseAccount, openDialog, setRemoteLanguage, toggleDemoEmpty, dismissToast,
  loadOwner, toast, store,
} from '../app/controller.js';
import { avatar, iconButton, kbd, segmented } from '../ui/components.js';
import { overviewView, overviewScope } from './overview.js';
import { matchesView } from './matches.js';
import { radarView, radarHeadline } from './radar.js';
import { lobbiesView } from './lobbies.js';
import { dialogs } from './dialogs.js';

export const NAV = [
  { id: 'overview', icon: 'overview', key: '1' },
  { id: 'matches', icon: 'history', key: '2' },
  { id: 'radar', icon: 'radar', key: '3' },
  { id: 'lobbies', icon: 'lobbies', key: '4' },
];

function navDot(state, id) {
  if (id === 'radar') {
    const head = radarHeadline(state);
    return !head.paused && head.active.length ? 'accent' : null;
  }
  if (id === 'lobbies') return state.lobbies.fresh && (state.lobbies.lobbies || []).some(row => row.activity?.IsNew) ? 'win' : null;
  return null;
}

function rail(state, ctx) {
  const view = state.route.view;
  return h('nav', { class: 'rail', 'aria-label': t('navigation') },
    h('a', { class: 'rail-brand', href: routeHash('overview'), 'aria-label': 'Matchroom Next' },
      h('img', { src: '/forts_icon.png', alt: '', width: '30', height: '30' })),
    h('div', { class: 'rail-items' }, NAV.map(item => {
      const active = view === item.id;
      const dot = navDot(state, item.id);
      return h('a', {
        key: item.id, class: ['rail-item', { 'is-active': active }], href: routeHash(item.id),
        'aria-current': active ? 'page' : null, title: t(item.id) + ' (' + item.key + ')',
      },
      icon(item.icon),
      h('span', { class: 'rail-label' }, t(item.id)),
      dot ? h('span', { class: 'rail-dot', 'data-tone': dot, 'aria-hidden': 'true' }) : null,
      dot ? h('span', { class: 'sr-only' }, ', ' + t('hasUpdates')) : null);
    })),
    h('div', { class: 'rail-foot' },
      iconButton({ icon: 'command', label: t('palette') + ' (Ctrl K)', tipSide: 'right', onClick: () => ctx.openPalette() }),
      iconButton({ icon: 'settings', label: t('settings'), tipSide: 'right', onClick: () => ctx.openSettings() })));
}

function titleFor(state) {
  const view = state.route.view;
  if (view === 'overview') {
    const scope = overviewScope(state);
    return { title: t('overview'), sub: scope.season === 'all' ? t('allSeasons') : scope.season ? t('seasonN', { n: scope.season }) : '' };
  }
  if (view === 'matches') return { title: t('matches'), sub: state.loaded ? t('archiveCount', { count: state.matches.length }) : '' };
  if (view === 'radar') return { title: t('radar'), sub: radarHeadline(state).title };
  const status = { ok: 'lobbyLive', manual: 'lobbyManual', stale: 'lobbyStale', error: 'lobbyError', paused: 'lobbyPaused' }[state.lobbies.status] || 'lobbyChecking';
  return { title: t('lobbies'), sub: t(status) };
}

function statusChips(state) {
  const data = state.app.game_activity || {};
  const values = activityValues(data);
  const checked = data.checked_at ? t('checkedAt', { time: time(new Date(typeof data.checked_at === 'number' ? data.checked_at * 1000 : data.checked_at)) }) : '';
  return h('div', { class: 'topbar-status' },
    h('span', { class: ['live-chip', { 'is-unknown': values.online === null }], 'data-tone': 'win', title: t('onlineHint') + (checked ? '\n' + checked : '') },
      h('span', { class: 'status-dot', 'aria-hidden': 'true' }),
      h('span', { class: 'num' }, values.online === null ? '—' : number(values.online)),
      h('span', { class: 'live-chip-label' }, t('onlineLabel'))),
    h('span', { class: ['live-chip', { 'is-unknown': values.queue === null }], 'data-tone': 'accent', title: t('queueHint') + (checked ? '\n' + checked : '') },
      h('span', { class: 'status-dot', 'aria-hidden': 'true' }),
      h('span', { class: 'num' }, values.queue === null ? '—' : number(values.queue) + (values.truncated ? '+' : '')),
      h('span', { class: 'live-chip-label' }, t('queueLabel'))));
}

function accountMenu(state, ctx) {
  const id = ownerId(state);
  const profile = state.profiles[id];
  const name = profile?.name || (id ? 'Steam ' + id.slice(-6) : t('noAccount'));
  const rating = ownerRating(state);
  const open = state.ui.menu;
  const manual = state.app.owner_mode === 'manual';
  const ratingText = rating.rank ? '№ ' + number(rating.rank) + (rating.score !== null ? ' · ' + number(rating.score) : '') : id ? t('noRating') : '—';

  const choose = async (mode, accountId = '') => {
    try { await chooseAccount(mode, accountId); } catch (error) { toast(error.message || t('requestFailed'), { tone: 'error' }); }
  };

  const menu = open ? h('div', { class: 'menu', id: 'account-menu', role: 'group', 'aria-label': t('accountMenu') },
    h('div', { class: 'menu-profile' },
      avatar({ name, src: profile?.avatar, size: 'lg' }),
      h('div', { class: 'menu-profile-text' },
        h('strong', { class: 'ellipsis' }, name),
        h('span', { class: 'num tone-accent' }, t('seasonN', { n: state.app.active_season || '—' }) + ' · ' + ratingText),
        rating.checkedAt ? h('small', { class: 'muted' }, t('checkedAt', { time: time(rating.checkedAt) })) : null),
      iconButton({ icon: 'refresh', label: t('refreshRating'), busy: !!state.busy.owner, disabled: !id, onClick: () => loadOwner(true) })),
    h('div', { class: 'menu-section' },
      h('span', { class: 'caps menu-caption' }, t('profileInMatchroom')),
      h('button', { type: 'button', 'aria-pressed': String(!manual), class: ['menu-item', { 'is-checked': !manual }], onClick: () => choose('auto') },
        icon('refresh'), h('span', { class: 'menu-item-text' }, h('span', null, t('automaticAccount')), h('small', { class: 'muted' }, t('automaticAccountHint'))),
        !manual ? icon('check', 'icon-sm menu-check') : null),
      (state.app.accounts || []).map(accountId => {
        const checked = manual && id === accountId;
        return h('button', { type: 'button', key: accountId, 'aria-pressed': String(checked), class: ['menu-item', { 'is-checked': checked }], onClick: () => choose('manual', accountId) },
          avatar({ name: nameOf(accountId, state) || accountId.slice(-2), src: state.profiles[accountId]?.avatar, size: 'xs' }),
          h('span', { class: 'menu-item-text' }, h('span', { class: 'ellipsis' }, nameOf(accountId, state) || 'Steam ' + accountId.slice(-6)), h('small', { class: 'muted mono' }, accountId)),
          checked ? icon('check', 'icon-sm menu-check') : null);
      }),
      h('button', { type: 'button', class: 'menu-item', onClick: () => { store.merge('ui', { accountInput: '', accountError: '' }); openDialog('account'); } },
        icon('plus'), h('span', { class: 'menu-item-text' }, t('otherAccount')))),
    h('div', { class: 'menu-section menu-row' },
      h('span', { class: 'menu-row-label' }, icon('globe'), t('language')),
      segmented({ items: [{ id: 'ru', label: 'RU' }, { id: 'en', label: 'EN' }], value: getLanguage(), label: t('language'), size: 'sm', onChange: value => setRemoteLanguage(value) })),
    h('div', { class: 'menu-section' },
      h('button', { type: 'button', class: 'menu-item', onClick: () => { toggleMenu(false); ctx.openSettings(); } }, icon('settings'), h('span', { class: 'menu-item-text' }, t('settings'))),
      h('button', { type: 'button', class: 'menu-item', onClick: () => { toggleMenu(false); openDialog('shortcuts'); } }, icon('keyboard'), h('span', { class: 'menu-item-text' }, t('shortcuts')), kbd('?')),
      h('button', { type: 'button', class: 'menu-item menu-item-danger', onClick: () => openDialog('quit') }, icon('power'), h('span', { class: 'menu-item-text' }, t('quitApp'))))) : null;

  return h('div', { class: 'account', 'data-menu-root': '' },
    h('button', {
      type: 'button', class: ['account-btn', { 'is-open': open }], 'aria-controls': 'account-menu', 'aria-expanded': String(open),
      onClick: () => toggleMenu(),
    },
    avatar({ name, src: profile?.avatar, size: 'sm' }),
    h('span', { class: 'account-text' },
      h('strong', { class: 'ellipsis' }, name),
      h('small', { class: 'num' }, ratingText)),
    icon('chevronDown', 'icon-sm account-caret')),
    menu);
}

function topbar(state, ctx) {
  const { title, sub } = titleFor(state);
  return h('header', { class: 'topbar' },
    h('div', { class: 'topbar-title' }, h('h1', null, title), sub ? h('span', { class: 'topbar-sub ellipsis' }, sub) : null),
    statusChips(state),
    h('button', { type: 'button', class: 'palette-trigger', onClick: () => ctx.openPalette() },
      icon('search', 'icon-sm'), h('span', { class: 'palette-trigger-text' }, t('paletteTrigger')), kbd('Ctrl K')),
    accountMenu(state, ctx));
}

function banners(state) {
  const list = [];
  if (state.app.demo) {
    list.push(h('div', { class: 'banner banner-demo', key: 'demo' },
      icon('info', 'icon-sm'),
      h('span', null, t('demoBanner')),
      h('button', { type: 'button', class: 'banner-action', onClick: toggleDemoEmpty }, t(state.app.demo_empty ? 'restoreDemo' : 'emptyDemo'))));
  }
  if (state.app.steam_throttle?.limited) {
    list.push(h('div', { class: 'banner banner-warning', key: 'steam' },
      icon('alert', 'icon-sm'), h('span', null, t('steamThrottled', { time: duration(state.app.steam_throttle.seconds) }))));
  }
  if (state.connection === 'lost') {
    list.push(h('div', { class: 'banner banner-error', role: 'alert', key: 'conn' },
      icon('alert', 'icon-sm'), h('span', null, t('connectionLost'))));
  }
  return list;
}

function toasts(state) {
  const glyph = { success: 'check', error: 'alert', accent: 'activity', info: 'info' };
  return h('div', { class: 'toasts', role: 'region', 'aria-live': 'polite', 'aria-label': t('notificationsRegion') },
    state.ui.toasts.map(item => h('div', { key: String(item.id), class: ['toast', 'toast-' + item.tone] },
      icon(glyph[item.tone] || 'info'),
      h('span', { class: 'toast-text' }, item.text),
      item.action ? h('a', { class: 'toast-action', href: item.action.href, onClick: () => dismissToast(item.id) }, item.action.label) : null,
      h('button', { type: 'button', class: 'toast-close', 'aria-label': t('close'), onClick: () => dismissToast(item.id) }, icon('close', 'icon-sm')))));
}

function stoppedScreen() {
  return h('div', { class: 'stopped' },
    h('img', { src: '/forts_icon.png', alt: '', width: '56', height: '56' }),
    h('h1', null, t('stoppedTitle')),
    h('p', { class: 'muted' }, t('stoppedText')));
}

const VIEWS = { overview: overviewView, matches: matchesView, radar: radarView, lobbies: lobbiesView };

export function appView(state, ctx) {
  if (state.connection === 'stopped') return stoppedScreen();
  const view = state.route.view;
  return [
    h('button', { type: 'button', class: 'skip-link', onClick: () => document.getElementById('view')?.focus() }, t('skipToContent')),
    rail(state, ctx),
    h('div', { class: 'main' },
      topbar(state, ctx),
      banners(state),
      h('main', { class: ['view', 'view-' + view], key: 'view:' + view, id: 'view', 'data-scroll-root': '', tabindex: '-1' },
        VIEWS[view](state, ctx))),
    toasts(state),
    state.ui.dragging ? h('div', { class: 'drop', 'aria-hidden': 'true' },
      icon('upload', 'icon-lg'), h('strong', null, t('dropFiles')), h('span', { class: 'muted' }, t('dropFilesHint'))) : null,
    dialogs(state, ctx),
  ];
}

