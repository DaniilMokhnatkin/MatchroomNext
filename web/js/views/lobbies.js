// Lobbies: public Forts rooms in a dense, scannable list.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, signed, duration, dateTime, time, getLanguage } from '../lib/i18n.js';
import { filterLobbies, tabCounts, isWatchable, comparisonCandidate, activitySummary, samplesOf, statusKey } from '../domain/lobbies.js';
import {
  store, isDemo, refreshLobbies, joinLobby, restartGame, forgetLastLobby, toggleFavorite, setLobbyNotify, testSound, exportLobbies, lobbyScenario,
} from '../app/controller.js';
import { button, iconButton, chip, segmented, searchField, select, checkChip, emptyState, hint, meter, callout } from '../ui/components.js';
import { sparkline, occupancyChart } from '../ui/charts.js';
import { toolbar } from './common.js';

const STATUS = {
  ok: ['lobbyLive', 'lobbyLiveHint', 'win'],
  manual: ['lobbyManual', 'lobbyManualHint', 'accent'],
  stale: ['lobbyStale', 'lobbyStaleHint', 'accent'],
  error: ['lobbyError', 'lobbyErrorHint', 'loss'],
  paused: ['lobbyPaused', 'lobbyPausedHint', 'neutral'],
  checking: ['lobbyChecking', 'lobbyCheckingHint', 'neutral'],
};

export function activityText(row, state) {
  const a = activitySummary(row, state);
  switch (a.kind) {
    case 'oneShot': return t('historyOneShot');
    case 'paused': return t('historyPaused');
    case 'unknown': return t('playersUnknown');
    case 'collecting': return t('historyCollecting');
    case 'net': return t('netOver', { value: signed(a.value), time: duration(a.seconds) });
    case 'stalled': return t('stalledFor', { time: duration(a.seconds) });
    case 'mixed': return t('mixedOver', { gains: a.gains, losses: a.losses, time: duration(a.seconds) });
    default: return t('unchangedFor', { time: duration(a.seconds) });
  }
}

function occupancy(row) {
  return (row.players ?? '—') + ' / ' + (row.max_players ?? '—');
}

function slotsText(row) {
  if (row.free_slots === null || row.free_slots === undefined) return t('slotsUnknown');
  if (isWatchable(row)) return t(row.in_progress ? 'lobbyInProgress' : 'lobbyFull');
  return t('slotsFree', { count: row.free_slots });
}

function historyBlock(row) {
  const samples = samplesOf(row);
  if (samples.length < 2) return h('p', { class: 'muted' }, t('historyCollecting'));
  const changes = samples.filter((p, i) => i === 0 || i === samples.length - 1 || p.players !== samples[i - 1].players);
  return h('div', { class: 'lobby-history' },
    occupancyChart(samples, {
      max: row.max_players, label: t('occupancyHistory'),
      format: point => point.players + ' / ' + (row.max_players ?? '—') + ' · ' + time(point.at, true),
    }),
    h('table', { class: 'data-table data-table-compact' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('time')), h('th', { scope: 'col', class: 'num-col' }, t('players')))),
      h('tbody', null, changes.map(point => h('tr', null,
        h('td', { class: 'num' }, time(point.at, true)),
        h('td', { class: 'num-col num' }, point.players + ' / ' + (row.max_players ?? '—')))))),
    h('p', { class: 'footnote' }, icon('info', 'icon-sm'), t('historyHint')));
}

function lobbyRow(state, row, { own = false } = {}) {
  const lobbies = state.lobbies;
  const ui = state.lobbyUi;
  const favorite = (lobbies.favorites || []).includes(row.id);
  const expanded = !!ui.expanded[row.id];
  const fresh = !!lobbies.fresh;
  const isNew = row.activity?.IsNew && fresh;
  const watchable = isWatchable(row);
  const busy = ui.busy || lobbies.busy;
  const samples = samplesOf(row).map(p => p.players);
  const canExpand = fresh && samples.length > 1;
  const fill = row.max_players ? (row.players || 0) / row.max_players : 0;
  const sig = JSON.stringify([row, favorite, expanded, fresh, lobbies.manual, busy, own, getLanguage()]);

  const chips = [];
  if (own) chips.push(chip(t('ownLobby'), { tone: 'accent' }));
  if (isNew) chips.push(chip(t('newRoom'), { tone: 'win' }));
  if (row.ranked === true) chips.push(chip(t('lobbyRanked'), { tone: 'info' }));
  if (row.password === true) chips.push(chip(t('password'), { icon: 'lock' }));
  else if (row.password === null || row.password === undefined) chips.push(chip(t('accessUnknown')));

  return h('article', { class: ['lobby', { 'is-new': isNew, 'is-own': own, 'is-open': expanded }], key: row.id, 'data-sig': sig },
    h('div', { class: 'lobby-row' },
      iconButton({
        icon: 'star', label: t(favorite ? 'unfavorite' : 'favorite'), active: favorite, className: 'lobby-star',
        onClick: () => toggleFavorite(row.id),
      }),
      h('div', { class: 'lobby-name' },
        h('strong', { class: 'ellipsis', title: row.name || '' }, row.name || t('room')),
        h('span', { class: 'lobby-chips' }, chips)),
      h('div', { class: 'lobby-map' },
        h('span', { class: 'ellipsis' }, row.map || '—'),
        h('small', { class: 'ellipsis muted' }, t('hostName', { name: row.host || '—' }) + (row.host_afk ? ' · AFK' : ''))),
      h('div', { class: 'lobby-slots' },
        h('span', { class: 'num lobby-count' }, occupancy(row)),
        meter(fill, watchable ? 'neutral' : 'accent'),
        h('small', { class: 'muted' }, slotsText(row))),
      h('div', { class: 'lobby-activity' },
        sparkline(samples, { max: row.max_players, tone: isNew ? 'win' : 'accent' }),
        h('small', { class: 'muted' }, activityText(row, lobbies))),
      h('div', { class: 'lobby-action' },
        own ? h('span', { class: 'muted lobby-state' }, t(statusKey(row))) : button({
          label: t(watchable ? 'watchLobby' : 'joinLobby'), icon: watchable ? 'eye' : 'enter',
          variant: watchable ? 'secondary' : 'primary', size: 'sm',
          disabled: !row.can_join || busy, title: t(!row.can_join || busy ? 'joinUnavailable' : 'joinHint'),
          onClick: () => joinLobby(row.id),
        }),
        canExpand ? iconButton({
          icon: 'chevronDown', label: t(expanded ? 'hideHistory' : 'showHistory'), active: expanded, className: 'lobby-expand',
          'aria-expanded': String(expanded),
          onClick: () => store.merge('lobbyUi', ui2 => ({ expanded: { ...ui2.expanded, [row.id]: !expanded } })),
        }) : h('span', { class: 'lobby-expand-placeholder' }))),
    expanded && canExpand ? historyBlock(row) : null);
}

function statusStrip(state) {
  const lobbies = state.lobbies;
  const [titleKey, hintKey, tone] = STATUS[lobbies.status] || STATUS.checking;
  return h('section', { class: 'strip', 'data-tone': tone, 'aria-live': 'polite' },
    h('span', { class: 'status-dot', 'aria-hidden': 'true' }),
    h('div', { class: 'strip-text' },
      h('strong', null, t(titleKey)),
      h('span', { class: 'muted' }, t(hintKey))),
    lobbies.checked_at ? h('span', { class: 'strip-time num muted' }, t('updatedAt', { time: dateTime(new Date(lobbies.checked_at)) })) : null);
}

// After a crash or restart: offer the room joined last through Matchroom.
function returnOffer(state) {
  const lobbies = state.lobbies;
  const last = lobbies.last_lobby;
  if (!last || !last.game_closed) return null;
  const name = last.name || t('room');
  const forget = button({ label: t('dismiss'), variant: 'ghost', size: 'sm', onClick: forgetLastLobby });
  if (!lobbies.game_running) {
    return callout({ tone: 'info', icon: 'enter', title: t('returnTitle'), text: t('returnClosed', { name }), action: forget });
  }
  const row = (lobbies.lobbies || []).find(r => r.id === last.id);
  const busy = state.lobbyUi.busy || lobbies.busy;
  if (!row) return callout({ tone: 'info', icon: 'enter', title: t('returnTitle'), text: t('returnGone', { name }), action: forget });
  return callout({
    tone: 'info', icon: 'enter', title: t('returnTitle'), text: t('returnReady', { name }),
    action: h('div', { class: 'callout-actions' },
      button({
        label: t('returnJoin'), icon: 'enter', variant: 'primary', size: 'sm',
        disabled: !row.can_join || busy, title: !row.can_join || busy ? t('joinUnavailable') : t('joinHint'),
        onClick: () => joinLobby(row.id),
      }), forget),
  });
}

function restartButton(state) {
  const ui = state.lobbyUi;
  const running = !!state.lobbies.game_running;
  if (running && ui.confirmRestart) {
    return h('span', { class: 'restart-confirm' },
      button({ label: t('restartConfirm'), icon: 'refresh', variant: 'primary', busy: !!ui.restarting, onClick: restartGame }),
      button({ label: t('cancel'), variant: 'ghost', onClick: () => store.merge('lobbyUi', { confirmRestart: false }) }));
  }
  return button({
    label: running ? t('restartGame') : t('startGame'), icon: running ? 'refresh' : 'play', variant: 'secondary',
    busy: !!ui.restarting, title: t(running ? 'restartGameHint' : 'startGameHint'),
    onClick: () => running ? store.merge('lobbyUi', { confirmRestart: true }) : restartGame(),
  });
}

export function lobbiesView(state) {
  const lobbies = state.lobbies;
  const ui = state.lobbyUi;
  const rows = lobbies.lobbies || [];
  const busy = ui.busy || lobbies.busy;
  const counts = tabCounts(rows);
  const filtered = filterLobbies(rows, { ...ui, favorites: lobbies.favorites });
  const own = rows.find(row => row.own);
  const tabs = [
    { id: 'recruiting', label: t('tabRecruiting'), count: counts.recruiting },
    { id: 'watching', label: t('tabWatching'), count: counts.watching },
    { id: 'all', label: t('tabAll'), count: counts.all },
    { id: 'ranked', label: t('tabRanked'), count: counts.ranked },
  ];

  let comparison = null;
  if (own && lobbies.fresh && !own.in_progress) {
    const other = comparisonCandidate(rows);
    comparison = h('p', { class: 'own-compare muted' }, icon('activity', 'icon-sm'),
      other ? t('compareWith', { name: other.name, slots: occupancy(other), activity: activityText(other, lobbies) }) : t('noComparison'));
  }

  return h('div', { class: 'lobbies' },
    toolbar(
      [h('span', { class: 'toolbar-note muted' }, t('lobbiesIntro')), hint(t('lobbyLimitations'), t('lobbyLimitationsTitle'))],
      [
        isDemo(state) ? select({
          value: '', label: t('demoScenarios'), className: 'demo-select', onChange: value => value && lobbyScenario(value),
          options: [{ value: '', label: t('demoScenarios') }, { value: 'live', label: t('lobbyLive') }, { value: 'manual', label: t('lobbyManual') },
            { value: 'stale', label: t('lobbyStale') }, { value: 'empty', label: t('demoEmptyList') }, { value: 'error', label: t('lobbyError') }],
        }) : null,
        isDemo(state) ? null : restartButton(state),
        button({ label: t('exportSnapshot'), icon: 'download', variant: 'ghost', disabled: !rows.length, onClick: exportLobbies }),
        button({ label: busy ? t('lobbyRefreshBusy') : lobbies.game_running ? t('refresh') : t('checkOnce'), icon: 'refresh', busy, onClick: refreshLobbies }),
      ]),
    statusStrip(state),
    isDemo(state) ? null : returnOffer(state),
    lobbies.message ? callout({ tone: 'error', text: lobbies.message }) : null,
    lobbies.truncated ? callout({ tone: 'warning', text: t('lobbyTruncated') }) : null,
    own ? h('section', { class: 'own-lobby', 'aria-label': t('ownLobby') }, lobbyRow(state, own, { own: true }), comparison) : null,
    h('div', { class: 'lobby-tools' },
      segmented({ items: tabs, value: ui.tab, label: t('roomFilters'), onChange: tab => store.merge('lobbyUi', { tab }) }),
      h('div', { class: 'lobby-filters' },
        searchField({ value: ui.query, placeholder: t('lobbySearch'), onInput: value => store.merge('lobbyUi', { query: value }) }),
        checkChip({ checked: ui.noPassword, label: t('noPassword'), icon: 'lock', onChange: value => store.merge('lobbyUi', { noPassword: value }) }),
        checkChip({ checked: ui.favoritesOnly, label: t('favoritesOnly'), icon: 'star', onChange: value => store.merge('lobbyUi', { favoritesOnly: value }) }),
        select({
          value: ui.sort, label: t('sortLobbies'), onChange: sort => store.merge('lobbyUi', { sort }),
          options: [{ value: 'new', label: t('sortNew') }, { value: 'slots', label: t('sortSlots') }, { value: 'growth', label: t('sortGrowth') }, { value: 'name', label: t('sortName') }],
        }))),
    h('div', { class: 'lobby-list-head' },
      h('span', { class: 'num muted' }, t('roomsCount', { count: filtered.length })),
      h('div', { class: 'lobby-sound' },
        checkChip({ checked: !!lobbies.notify, label: t('newRoomSound'), icon: 'volume', onChange: setLobbyNotify }),
        iconButton({ icon: 'play', label: t('testSound'), onClick: testSound }))),
    h('div', { class: 'lobby-columns', 'aria-hidden': 'true' },
      h('span'), h('span', { class: 'caps' }, t('room')), h('span', { class: 'caps' }, t('mapAndHost')),
      h('span', { class: 'caps' }, t('players')), h('span', { class: 'caps' }, t('activity')), h('span')),
    h('div', { class: 'lobby-list' },
      filtered.length ? filtered.map(row => lobbyRow(state, row))
        : emptyState({ icon: 'lobbies', title: t('noLobbies'), text: t(ui.favoritesOnly ? 'lobbyFavoritesEmpty' : 'noLobbiesHint') })));
}

