// Player dossier: who this is, how they usually play, and every game Next saw them in.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, percent, dateCompact, dateShort, getLanguage } from '../lib/i18n.js';
import { routeHash } from '../lib/router.js';
import { METRICS, formatMetric, deviationTone, deviationText, dossierScope, scopeMatches, internalSeries, stampDate } from '../domain/analysis.js';
import { store, nameOf, loadDossier, isDemo } from '../app/controller.js';
import { panel, avatar, chip, stat, emptyState, skeleton, button, segmented, externalLink, hint } from '../ui/components.js';
import { ratingChart } from '../ui/charts.js';
import { formBadge } from './replay.js';

function bestName(state, dossier) {
  return nameOf(dossier.steam_id, state) || dossier.names?.[0]?.name || t('player');
}

export function playerHeadline(state) {
  const data = state.dossiers[state.route.id]?.data;
  return data ? bestName(state, data) : '';
}

function hero(state, data) {
  const name = bestName(state, data);
  const board = data.board;
  const rate = data.wins + data.losses ? data.wins / (data.wins + data.losses) : null;
  const others = (data.names || []).map(n => n.name).filter(n => n !== name).slice(0, 4);
  const vs = data.with_you.opponent, ally = data.with_you.ally;
  return h('header', { class: 'panel dossier-hero' },
    avatar({ name, src: state.profiles[data.steam_id]?.avatar, size: 'lg' }),
    h('div', { class: 'dossier-id' },
      h('h2', null, name),
      others.length ? h('p', { class: 'muted' }, t('alsoKnownAs', { names: others.join(', ') })) : null,
      h('p', { class: 'muted mono' }, data.steam_id),
      h('div', { class: 'dossier-links' },
        externalLink({ href: 'https://steamcommunity.com/profiles/' + data.steam_id, label: 'Steam', demo: isDemo(state), onDemo() {} }),
        externalLink({ href: 'https://forts-tools.com/player/' + data.steam_id, label: 'forts-tools', demo: isDemo(state), onDemo() {} }))),
    h('dl', { class: 'dossier-facts' },
      h('div', null, h('dt', { class: 'caps' }, t('internalRating')), h('dd', { class: 'num' }, board?.internal ? number(board.internal) : '—'),
        board?.rank ? h('small', { class: 'muted' }, '№ ' + number(board.rank) + (board.score ? ' · ' + number(board.score) : '')) : null),
      h('div', null, h('dt', { class: 'caps' }, t('seasonRecord')), h('dd', { class: 'num' }, board ? `${board.wins}–${board.losses}` : '—'),
        board?.season ? h('small', { class: 'muted' }, t('seasonN', { n: board.season })) : null),
      h('div', null, h('dt', { class: 'caps' }, t('againstYou')), h('dd', { class: 'num' }, vs.wins + vs.losses ? `${vs.wins}–${vs.losses}` : '—'),
        vs.wins + vs.losses ? h('small', { class: 'muted' }, t('youFirst', { record: `${vs.losses}–${vs.wins}` })) : null),
      h('div', null, h('dt', { class: 'caps' }, t('withYouAlly')), h('dd', { class: 'num' }, ally.wins + ally.losses ? `${ally.wins}–${ally.losses}` : '—')),
      h('div', null, h('dt', { class: 'caps' }, t('seenGames')), h('dd', { class: 'num' }, number(data.games)),
        rate !== null ? h('small', { class: 'muted' }, t('winRateValue', { value: percent(rate) })) : null),
      h('div', null, h('dt', { class: 'caps' }, t('lastSeen')), h('dd', { class: 'num' }, data.last_seen ? dateShort(stampDate(data.last_seen)) : '—'),
        board?.last_match ? h('small', { class: 'muted' }, t('lastRanked', { date: dateCompact(stampDate(board.last_match)) })) : null)));
}

function norms(data, scope) {
  const averages = data.averages?.[scope] || {};
  const count = scopeMatches(data, scope).length;
  return panel({
    title: t('usualPlay'), caption: t('usualPlayCaption', { count }), className: 'dossier-norms',
    actions: hint(t('usualPlayHint'), t('aboutData'), { end: true }),
    children: count ? h('div', { class: 'kpi-row kpi-row-8' }, METRICS.map(metric => stat({
      label: t('metric_' + metric.id), value: formatMetric(metric.kind, averages[metric.id]), title: t('metricHint_' + metric.id),
    }))) : h('p', { class: 'panel-empty' }, t('noGamesInScope')),
  });
}

function ratingPanel(state, data) {
  const points = internalSeries(data.ratings);
  return panel({
    title: t('internalHistory'), caption: t('internalHistoryCaption'), className: 'dossier-rating',
    children: points.length >= 2
      ? ratingChart(points, { label: t('internalHistory'), sig: [getLanguage(), data.steam_id, points.length].join(':') })
      : h('p', { class: 'panel-empty' }, points.length ? t('internalOnePoint', { value: number(points[0].score) }) : t('internalNone')),
  });
}

function recentOpponents(state, data) {
  const rows = data.board?.opponents || [];
  return panel({
    title: t('boardOpponents'), caption: t('boardOpponentsCaption'), className: 'dossier-opponents',
    children: rows.length ? h('ul', { class: 'opponent-list' }, rows.map((row, index) => h('li', { key: row.steam_id + index },
      h('a', { class: 'opponent-row', href: routeHash('player', row.steam_id) },
        avatar({ name: nameOf(row.steam_id, state) || row.name || '?', src: state.profiles[row.steam_id]?.avatar, size: 'xs' }),
        h('span', { class: 'ellipsis' }, nameOf(row.steam_id, state) || row.name || row.steam_id))))) : h('p', { class: 'panel-empty' }, t('boardOpponentsNone')),
  });
}

function mapsPanel(data) {
  return panel({
    title: t('maps'), caption: t('byMatches'), className: 'dossier-maps',
    children: data.maps?.length ? h('ul', { class: 'bar-list' }, data.maps.slice(0, 8).map(row => {
      const decided = row.wins + row.losses;
      const rate = decided ? row.wins / decided : null;
      return h('li', { key: row.map, class: 'bar-item' },
        h('div', { class: 'bar-item-head' }, h('span', { class: 'ellipsis' }, row.map), h('span', { class: 'num muted' }, `${row.wins}–${row.losses}`)),
        h('div', { class: 'bar-item-body' }, h('span', { class: ['meter', 'meter-' + (rate === null ? 'neutral' : rate >= 0.5 ? 'win' : 'loss')] },
          h('span', { class: 'meter-fill', style: { '--value': String(rate ?? 0) } })), h('span', { class: 'num' }, rate !== null ? percent(rate) : '—')));
    })) : h('p', { class: 'panel-empty' }, t('mapsEmpty')),
  });
}

function gamesTable(state, data, scope) {
  const games = scopeMatches(data, scope);
  const limit = state.dossierUi.limit;
  const relation = row => row.relation === 'ally' ? chip(t('relationAlly'), { tone: 'accent' })
    : row.relation === 'opponent' ? chip(t('relationOpponent'), { tone: 'loss' }) : null;
  return panel({
    title: t('gamesByReplay'), caption: t('gamesByReplayCaption'), className: 'dossier-games',
    children: games.length ? [
      h('div', { class: 'table-scroll' }, h('table', { class: 'data-table analysis-table' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, t('dateCol')), h('th', { scope: 'col' }, t('mapCol')), h('th', { scope: 'col' }, t('resultCol')),
          METRICS.slice(0, 7).map(metric => h('th', { scope: 'col', class: 'num-col', key: metric.id, title: t('metricHint_' + metric.id) }, t('metric_' + metric.id))),
          h('th', { scope: 'col' }, t('formCol')))),
        h('tbody', null, games.slice(0, limit).map(row => h('tr', { key: row.key, class: 'is-link', onClick: () => { location.hash = routeHash('replay', row.key); } },
          h('td', { class: 'num muted nowrap' }, row.played ? dateCompact(stampDate(row.played)) : '—'),
          h('td', null, h('a', { class: 'cell-link ellipsis', href: routeHash('replay', row.key) }, row.map || '—'),
            h('small', { class: 'muted' }, ' ' + row.mode + (row.ranked ? ' · ' + t('rankedShort') : '')), ' ', relation(row)),
          h('td', null, h('span', { class: 'tone-' + (row.won === true ? 'win' : row.won === false ? 'loss' : 'neutral') },
            t(row.won === true ? 'resultWin' : row.won === false ? 'resultLoss' : 'resultUnknown'))),
          METRICS.slice(0, 7).map(metric => {
            const deviation = row.deviation?.[metric.id];
            return h('td', { class: 'num-col metric-cell', key: metric.id },
              h('span', { class: 'num' }, formatMetric(metric.kind, row.metrics?.[metric.id])),
              deviation ? h('small', { class: ['metric-dev', 'tone-' + deviationTone(deviation)] }, deviationText(deviation)) : null);
          }),
          h('td', null, formBadge(row.form))))))),
      games.length > limit ? h('div', { class: 'panel-more' }, button({ label: t('showMore', { count: Math.min(60, games.length - limit) }), variant: 'ghost', size: 'sm',
        onClick: () => store.merge('dossierUi', { limit: limit + 60 }) })) : null,
    ] : h('p', { class: 'panel-empty' }, t('noGamesInScope')),
  });
}

export function playerView(state) {
  const id = state.route.id;
  const entry = state.dossiers[id];
  if (!entry || (entry.loading && !entry.data)) return h('div', { class: 'panel' }, skeleton(6));
  if (entry.error && !entry.data) {
    return emptyState({ icon: 'user', title: t('dossierFailed'), text: entry.error,
      actions: [button({ label: t('refresh'), icon: 'refresh', onClick: () => loadDossier(id, true) })] });
  }
  const data = entry.data;
  const scope = dossierScope(data, state.dossierUi.scope);
  const duel = (data.matches || []).filter(m => m.mode === '1v1').length;
  return h('div', { class: 'dossier' },
    hero(state, data),
    h('div', { class: 'toolbar' },
      segmented({ items: [{ id: 'duel', label: t('scopeDuel'), count: duel }, { id: 'team', label: t('scopeTeam'), count: data.matches.length - duel }],
        value: scope, label: t('scope'), size: 'sm', onChange: value => store.merge('dossierUi', { scope: value, limit: 40 }) }),
      h('span', { class: 'toolbar-note muted' }, state.app.players_status?.pending
        ? t('analysisQueue', { count: state.app.players_status.pending }) : t('analysisDone', { count: state.app.players_status?.analysed || 0 }))),
    norms(data, scope),
    h('div', { class: 'dossier-grid' }, ratingPanel(state, data), recentOpponents(state, data), mapsPanel(data)),
    gamesTable(state, data, scope));
}
