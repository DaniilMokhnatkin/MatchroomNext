// Lobby games: ordinary multiplayer matches, counted apart from ranked.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, percent, duration, dateCompact } from '../lib/i18n.js';
import { playable, filterGames, summarizeCasual, byMode, byMap, teammates, opponents, gameDate, MODES } from '../domain/casual.js';
import { store, ownerId, nameOf } from '../app/controller.js';
import { panel, stat, meter, segmented, select, emptyState, skeleton, avatar, button } from '../ui/components.js';
import { toolbar } from './common.js';
import { routeHash } from '../lib/router.js';

const tone = rate => (rate === null ? 'neutral' : rate >= 0.5 ? 'win' : 'loss');

function rateCell(row) {
  return h('span', { class: 'rate-cell' }, meter(row.winRate ?? 0, tone(row.winRate)),
    h('span', { class: 'num' }, row.winRate !== null ? percent(row.winRate) : '—'));
}

function record(row) {
  return h('span', { class: 'num' }, h('span', { class: 'tone-win' }, number(row.wins)), ' – ', h('span', { class: 'tone-loss' }, number(row.losses)));
}

function peopleTable(state, rows, emptyKey) {
  if (!rows.length) return h('p', { class: 'panel-empty' }, t(emptyKey));
  return h('table', { class: 'data-table' },
    h('thead', null, h('tr', null,
      h('th', { scope: 'col' }, t('playerCol')),
      h('th', { scope: 'col', class: 'num-col' }, t('gamesCol')),
      h('th', { scope: 'col', class: 'num-col' }, t('recordCol')),
      h('th', { scope: 'col', class: 'rate-col' }, t('winRate')))),
    h('tbody', null, rows.map(row => h('tr', { key: row.steam_id },
      h('td', null, h('span', { class: 'cell-player' },
        avatar({ name: nameOf(row.steam_id, state) || row.name, src: state.profiles[row.steam_id]?.avatar, size: 'sm' }),
        h('a', { class: 'cell-link ellipsis', href: routeHash('player', row.steam_id) }, nameOf(row.steam_id, state) || row.name || t('player')))),
      h('td', { class: 'num-col num' }, number(row.total)),
      h('td', { class: 'num-col' }, record(row)),
      h('td', { class: 'rate-col' }, rateCell(row))))));
}

function barList(rows, labelOf) {
  return h('ul', { class: 'bar-list' }, rows.map(row => h('li', { key: labelOf(row), class: 'bar-item' },
    h('div', { class: 'bar-item-head' },
      h('span', { class: 'ellipsis' }, labelOf(row)),
      h('span', { class: 'num muted' }, `${row.wins}–${row.losses}` + (row.unknown ? ' · ' + t('noResultShort', { count: row.unknown }) : ''))),
    h('div', { class: 'bar-item-body' }, meter(row.winRate ?? 0, tone(row.winRate)),
      h('span', { class: 'num' }, row.winRate !== null ? percent(row.winRate) : '—')))));
}

function recent(state, games, limit) {
  return h('ul', { class: 'casual-list' }, games.slice(0, limit).map(game => {
    const date = gameDate(game);
    const mates = game.players.filter(p => p.role === 'ally').map(p => p.name).filter(Boolean);
    const rivals = game.players.filter(p => p.role === 'opponent').map(p => p.name).filter(Boolean);
    const result = game.result === 'win' || game.result === 'loss' ? game.result : 'unknown';
    return h('li', { key: game.id, class: 'casual-row', 'data-result': result },
      h('span', { class: 'recent-mark', 'aria-hidden': 'true' }),
      h('span', { class: 'casual-result' }, t(result === 'win' ? 'resultWin' : result === 'loss' ? 'resultLoss' : 'resultUnknown')),
      h('a', { class: 'casual-map ellipsis', href: routeHash('replay', game.file) }, h('strong', null, game.map || '—'), h('small', { class: 'muted' }, game.mode)),
      h('span', { class: 'casual-teams ellipsis muted', title: [mates.join(', '), rivals.join(', ')].join('  vs  ') },
        mates.length ? t('withTeam', { names: mates.slice(0, 3).join(', ') }) + ' · ' : '', t('againstTeam', { names: rivals.slice(0, 3).join(', ') || '—' })),
      h('span', { class: 'num muted casual-length' }, game.duration ? duration(game.duration) : '—'),
      h('time', { class: 'num muted' }, date ? dateCompact(date) : '—'));
  }));
}

export function casualHeadline(state) {
  const games = playable(state.casual.games, ownerId(state));
  return t('casualSub', { count: games.length });
}

export function casualView(state) {
  const ui = state.casualUi;
  if (!state.casual.loaded) return h('div', { class: 'panel' }, skeleton(4));
  const all = playable(state.casual.games, ownerId(state));
  const status = state.casual.status || {};
  if (!all.length) {
    return emptyState({ icon: 'trophy', title: t('casualEmptyTitle'),
      text: status.scanning ? t('casualScanning', { count: status.pending || 0 }) : t('casualEmptyText') });
  }
  const games = filterGames(all, { mode: ui.mode, days: Number(ui.days) || 0 });
  const stats = summarizeCasual(games);
  const modeCounts = Object.fromEntries(byMode(filterGames(all, { days: Number(ui.days) || 0 })).map(row => [row.mode, row.total]));
  const modes = [{ id: '', label: t('allModes') }, ...MODES.map(mode => ({ id: mode, label: mode, count: modeCounts[mode] || 0 })),
    { id: 'other', label: t('otherModes'), count: modeCounts.other || 0 }];
  const mates = teammates(games).slice(0, 8);
  const rivals = opponents(games).slice(0, 8);
  const streak = stats.streak.type ? t(stats.streak.type === 'win' ? 'streakWins' : 'streakLosses', { count: stats.streak.count }) : t('streakNone');
  return h('div', { class: 'casual' },
    toolbar(
      segmented({ items: modes, value: ui.mode, label: t('mode'), size: 'sm', onChange: value => store.merge('casualUi', { mode: value, limit: 40 }) }),
      h('div', { class: 'toolbar-group' },
        select({ value: String(ui.days || 0), label: t('period'), onChange: value => store.merge('casualUi', { days: Number(value), limit: 40 }),
          options: [{ value: '0', label: t('periodAll') }, { value: '90', label: t('periodDays', { count: 90 }) },
            { value: '30', label: t('periodDays', { count: 30 }) }, { value: '7', label: t('periodDays', { count: 7 }) }] }),
        status.scanning ? h('span', { class: 'toolbar-note muted' }, icon('refresh', 'spin icon-sm'), t('casualScanning', { count: status.pending || 0 })) : null)),
    games.length ? [
      h('div', { class: 'kpi-row' },
        stat({ label: t('gamesPlayed'), value: number(stats.total), sub: stats.unknown ? t('withoutResult', { count: stats.unknown }) : t('lobbyOnly') }),
        stat({ label: t('recordCol'), value: `${number(stats.wins)}–${number(stats.losses)}`, sub: streak }),
        stat({ label: t('winRate'), value: stats.winRate !== null ? percent(stats.winRate) : '—', tone: tone(stats.winRate), sub: t('decidedOnly') }),
        stat({ label: t('avgLength'), value: stats.avgDuration ? duration(Math.round(stats.avgDuration)) : '—', sub: t('perGame') })),
      stats.form.length ? h('ol', { class: 'form-strip form-strip-wide', 'aria-label': t('form') }, stats.form.map(item => h('li', { key: item.id },
        h('span', { class: 'form-cell', 'data-result': item.result, title: (item.map || '') + (item.date ? ' · ' + dateCompact(item.date) : '') },
          t(item.result === 'win' ? 'letterWin' : 'letterLoss'))))) : null,
      h('div', { class: 'casual-grid' },
        panel({ title: t('byMode'), className: 'casual-modes', children: barList(byMode(games), row => row.mode === 'other' ? t('otherModes') : row.mode) }),
        panel({ title: t('maps'), caption: t('byMatches'), className: 'casual-maps', children: barList(byMap(games).slice(0, 8), row => row.map) }),
        panel({ title: t('teammates'), caption: t('teammatesCaption'), className: 'casual-mates', children: peopleTable(state, mates, 'teammatesEmpty') }),
        panel({ title: t('lobbyOpponents'), caption: t('byMeetings'), className: 'casual-rivals', children: peopleTable(state, rivals, 'rivalsEmpty') }),
        panel({ title: t('recentGames'), className: 'casual-recent',
          children: [recent(state, games, ui.limit),
            games.length > ui.limit ? h('div', { class: 'panel-more' }, button({ label: t('showMore', { count: Math.min(60, games.length - ui.limit) }), variant: 'ghost', size: 'sm',
              onClick: () => store.merge('casualUi', { limit: ui.limit + 60 }) })) : null] })),
    ] : emptyState({ icon: 'trophy', title: t('casualNoneFiltered'), text: t('casualNoneFilteredText') }));
}
