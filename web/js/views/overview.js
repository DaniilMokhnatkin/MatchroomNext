// Overview: the season at a glance — rank, rating trend, form, rivals, maps.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, signed, percent, dateCompact, getLanguage } from '../lib/i18n.js';
import { routeHash } from '../lib/router.js';
import { summarize, opponentStats, mapStats, seasonsOf, pair, outcome, ratingChange, dateOf, historicalRating } from '../domain/matches.js';
import { store, ownerId, ownerRating, nameOf, playerName, openSettings, toggleWatch, loadProfiles } from '../app/controller.js';
import { panel, stat, button, chip, select, meter, emptyState, skeleton, avatar, hint } from '../ui/components.js';
import { ratingChart } from '../ui/charts.js';
import { resultLetter, resultText, deltaShort, deltaTone, toolbar } from './common.js';
import { radarHeadline } from './radar.js';
import { tabCounts } from '../domain/lobbies.js';

export function overviewScope(state) {
  const seasons = seasonsOf(state.matches);
  const active = Number(state.app.active_season);
  const chosen = state.overviewUi.season;
  if (chosen === 'all') return { season: 'all', seasons };
  if (chosen && seasons.includes(Number(chosen))) return { season: Number(chosen), seasons };
  return { season: seasons.includes(active) ? active : seasons[0] ?? active, seasons };
}

function daysLeft(state) {
  const end = Date.parse(state.app.season_end || '');
  if (!Number.isFinite(end)) return null;
  return Math.max(0, Math.ceil((end - Date.now()) / 86400000));
}

function seasonHero(state, ctx, scope, matches, stats) {
  const own = ownerId(state);
  const isActive = scope.season !== 'all' && Number(scope.season) === Number(state.app.active_season);
  let rank = null, score = null, rankNote = '';
  if (isActive) {
    const live = ownerRating(state);
    rank = live.rank; score = live.score;
    rankNote = t('rankNow');
  } else if (matches.length) {
    const latest = matches[0];
    const past = historicalRating(latest, pair(latest, own)[0]);
    rank = past?.rank || null; score = past?.score ?? null;
    rankNote = t('rankAtLastMatch');
  }
  const left = daysLeft(state);
  const seasonLabel = scope.season === 'all' ? t('allSeasons') : t('seasonN', { n: scope.season });

  const chartLabel = t('ratingChartLabel', { count: stats.series.length });
  const chartArea = stats.series.length >= 2
    ? ratingChart(stats.series, {
      label: chartLabel,
      sig: [getLanguage(), scope.season, stats.series.map(p => p.id + p.score).join('|')].join(':'),
      onSelect: id => ctx.router.go('matches', id),
    })
    : h('div', { class: 'chart-empty' }, icon('activity', 'icon-lg'), h('p', null, t('ratingChartEmpty')));

  return h('section', { class: 'hero panel' },
    h('div', { class: 'hero-summary' },
      h('div', { class: 'hero-season' },
        h('span', { class: 'caps' }, seasonLabel),
        isActive ? chip(left !== null ? t('daysLeft', { count: left }) : t('seasonCurrent'), { tone: 'accent' }) : chip(t('seasonArchive'))),
      h('div', { class: 'hero-rank' },
        h('span', { class: 'hero-rank-value num' }, rank ? '№ ' + number(rank) : '—'),
        h('span', { class: 'hero-rank-note' }, rank ? rankNote : t('rankUnknown'))),
      h('dl', { class: 'hero-facts' },
        h('div', null, h('dt', { class: 'caps' }, t('score')), h('dd', { class: 'num' }, score !== null ? number(score) : '—')),
        h('div', null, h('dt', { class: 'caps' }, t('seasonDelta')),
          h('dd', { class: ['num', stats.deltaSum > 0 ? 'tone-win' : stats.deltaSum < 0 ? 'tone-loss' : ''] }, stats.deltaSum !== null ? signed(stats.deltaSum) : '—')),
        h('div', null, h('dt', { class: 'caps' }, t('peakScore')), h('dd', { class: 'num' }, stats.peak !== null ? number(stats.peak) : '—')))),
    h('div', { class: 'hero-chart' },
      h('div', { class: 'hero-chart-head' },
        h('span', { class: 'caps' }, t('ratingTrend')),
        hint(t('ratingTrendHint'), t('aboutData'), { end: true })),
      chartArea));
}

function formPanel(state, stats) {
  const streak = stats.streak;
  const streakText = streak.type
    ? t(streak.type === 'win' ? 'streakWins' : 'streakLosses', { count: streak.count })
    : t('streakNone');
  return panel({
    title: t('form'), caption: t('lastN', { count: stats.form.length }), className: 'form-panel',
    children: [
      h('ol', { class: 'form-strip', 'aria-label': t('form') },
        stats.form.map(item => h('li', { key: item.id },
          h('a', {
            class: 'form-cell', 'data-result': item.result, href: routeHash('matches', item.id),
            title: resultText(item.result) + ' · ' + (item.map || '') + ' · ' + (item.date ? dateCompact(item.date) : ''),
          }, resultLetter(item.result))))),
      h('div', { class: 'form-streak', 'data-tone': streak.type || 'none' }, icon('flame', 'icon-sm'), h('span', null, streakText)),
      h('div', { class: 'form-record' },
        h('div', null, h('span', { class: 'num tone-win' }, number(stats.wins)), h('small', null, t('winsShort'))),
        h('div', null, h('span', { class: 'num tone-loss' }, number(stats.losses)), h('small', null, t('lossesShort'))),
        h('div', null, h('span', { class: 'num' }, number(stats.draws)), h('small', null, t('drawsShort')))),
      h('div', { class: 'form-rate' },
        h('div', { class: 'form-rate-head' }, h('span', { class: 'caps' }, t('winRate')),
          h('strong', { class: 'num' }, stats.winRate !== null ? percent(stats.winRate) : '—')),
        meter(stats.winRate ?? 0, 'win')),
    ],
  });
}

function kpis(stats) {
  return h('div', { class: 'kpi-row' },
    stat({ label: t('matchesPlayed'), value: number(stats.total), sub: stats.unknown ? t('withoutResult', { count: stats.unknown }) : t('rankedOnly') }),
    stat({ label: t('avgWin'), value: stats.avgGain !== null ? signed(Math.round(stats.avgGain)) : '—', tone: 'win', sub: t('perWin') }),
    stat({ label: t('avgLoss'), value: stats.avgDrop !== null ? signed(Math.round(stats.avgDrop)) : '—', tone: 'loss', sub: t('perLoss') }),
    stat({ label: t('bestStreak'), value: number(stats.bestWinStreak), sub: t('winsInRow', { count: stats.bestWinStreak }) }));
}

function rivalsPanel(state, ctx, matches) {
  const own = ownerId(state);
  const rows = opponentStats(matches, own, id => nameOf(id, state)).slice(0, 7);
  queueMicrotask(() => loadProfiles(rows.map(row => row.steam_id)));
  return panel({
    title: t('rivals'), caption: t('byMeetings'), className: 'rivals-panel',
    children: rows.length ? h('table', { class: 'data-table' },
      h('thead', null, h('tr', null,
        h('th', { scope: 'col' }, t('opponentCol')),
        h('th', { scope: 'col', class: 'num-col' }, t('meetingsCol')),
        h('th', { scope: 'col', class: 'num-col' }, t('recordCol')),
        h('th', { scope: 'col', class: 'rate-col' }, t('winRate')))),
      h('tbody', null, rows.map(row => h('tr', {
        key: row.key, class: 'is-link',
        onClick: () => ctx.openOpponent(row.steam_id, row.lastId),
      },
      // The name is the keyboard and screen-reader target; its click bubbles to the row handler above.
      h('td', null, h('span', { class: 'cell-player' },
        avatar({ name: row.name, src: state.profiles[row.steam_id]?.avatar, size: 'sm' }),
        h('button', { type: 'button', class: 'cell-link ellipsis' }, row.name || t('player')))),
      h('td', { class: 'num-col num' }, number(row.total)),
      h('td', { class: 'num-col num' }, h('span', { class: 'tone-win' }, row.wins), ' – ', h('span', { class: 'tone-loss' }, row.losses)),
      h('td', { class: 'rate-col' }, h('span', { class: 'rate-cell' }, meter(row.winRate ?? 0, row.winRate === null ? 'neutral' : row.winRate >= 0.5 ? 'win' : 'loss'),
        h('span', { class: 'num' }, row.winRate !== null ? percent(row.winRate) : '—'))))))) : h('p', { class: 'panel-empty' }, t('rivalsEmpty')),
  });
}

function mapsPanel(state, matches) {
  const rows = mapStats(matches, ownerId(state)).slice(0, 6);
  return panel({
    title: t('maps'), caption: t('byMatches'), className: 'maps-panel',
    children: rows.length ? h('ul', { class: 'bar-list' }, rows.map(row => h('li', { key: row.map, class: 'bar-item' },
      h('div', { class: 'bar-item-head' },
        h('span', { class: 'ellipsis' }, row.map),
        h('span', { class: 'num muted' }, t('matchesCount', { count: row.total }))),
      h('div', { class: 'bar-item-body' },
        meter(row.winRate ?? 0, row.winRate === null ? 'neutral' : row.winRate >= 0.5 ? 'win' : 'loss'),
        h('span', { class: 'num' }, row.winRate !== null ? percent(row.winRate) : '—'))))) : h('p', { class: 'panel-empty' }, t('mapsEmpty')),
  });
}

function recentPanel(state, ctx, matches) {
  const own = ownerId(state);
  return panel({
    title: t('recentMatches'),
    actions: button({ label: t('allMatches'), variant: 'ghost', size: 'sm', icon: 'chevronRight', onClick: () => ctx.router.go('matches') }),
    className: 'recent-panel',
    children: h('ul', { class: 'recent-list' }, matches.slice(0, 6).map(match => {
      const [left, right] = pair(match, own);
      const result = outcome(match, own);
      const change = ratingChange(match, left?.steam_id);
      const date = dateOf(match);
      return h('li', { key: match.match_id },
        h('a', { class: 'recent-row', href: routeHash('matches', match.match_id), 'data-result': result },
          h('span', { class: 'recent-mark', 'aria-hidden': 'true' }),
          h('span', { class: 'recent-result' }, resultText(result)),
          h('span', { class: 'recent-opponent ellipsis' }, playerName(right, state)),
          h('span', { class: 'recent-map ellipsis muted' }, match.map || '—'),
          h('span', { class: ['num', 'recent-delta', 'tone-' + deltaTone(change)] }, deltaShort(change)),
          h('time', { class: 'muted num' }, date ? dateCompact(date) : '—')));
    })),
  });
}

function livePanel(state, ctx) {
  const radar = radarHeadline(state);
  const counts = tabCounts(state.lobbies.lobbies || []);
  const lobbyLive = state.lobbies.fresh;
  return panel({
    title: t('liveNow'), className: 'live-panel',
    children: [
      h('a', { class: 'live-row', href: routeHash('radar') },
        h('span', { class: 'live-icon', 'data-tone': radar.tone }, icon('radar')),
        h('span', { class: 'live-text' }, h('strong', null, radar.title), h('small', null, radar.names || radar.hint)),
        icon('chevronRight', 'icon-sm live-go')),
      h('a', { class: 'live-row', href: routeHash('lobbies') },
        h('span', { class: 'live-icon', 'data-tone': lobbyLive ? 'win' : 'neutral' }, icon('lobbies')),
        h('span', { class: 'live-text' },
          h('strong', null, lobbyLive ? t('lobbiesRecruiting', { count: counts.recruiting }) : t('lobbiesNotLive')),
          h('small', null, lobbyLive ? t('lobbiesWatchable', { count: counts.watching }) : t('lobbiesNotLiveHint'))),
        icon('chevronRight', 'icon-sm live-go')),
    ],
  });
}

function onboarding(state, ctx) {
  const watch = state.app.watch || { folders: [] };
  const found = (watch.folders || []).length > 0;
  return h('div', { class: 'onboarding' },
    emptyState({
      icon: 'history', title: t('overviewEmptyTitle'), text: t('overviewEmptyText'),
      actions: [
        button({ label: t('addReplay'), icon: 'upload', variant: 'primary', onClick: () => ctx.pickFiles() }),
        button({ label: t('settings'), icon: 'settings', onClick: openSettings }),
      ],
    }),
    h('ol', { class: 'steps' },
      h('li', { class: ['step', { 'is-done': found }] }, h('span', { class: 'step-mark' }, found ? icon('check', 'icon-sm') : '1'),
        h('div', null, h('strong', null, found ? t('stepFolderFound') : t('stepFolderMissing')),
          h('small', null, found ? (watch.folders || []).join(', ') : t('stepFolderHint')))),
      h('li', { class: ['step', { 'is-done': watch.enabled }] }, h('span', { class: 'step-mark' }, watch.enabled ? icon('check', 'icon-sm') : '2'),
        h('div', null, h('strong', null, watch.enabled ? t('stepWatchOn') : t('stepWatchOff')),
          h('small', null, t('stepWatchHint')),
          !watch.enabled ? button({ label: t('enable'), size: 'sm', onClick: toggleWatch }) : null)),
      h('li', { class: 'step' }, h('span', { class: 'step-mark' }, '3'),
        h('div', null, h('strong', null, t('stepPlay')), h('small', null, t('stepPlayHint'))))));
}

export function overviewView(state, ctx) {
  if (!state.loaded) return h('div', { class: 'overview' }, h('div', { class: 'panel' }, skeleton(4)));
  if (!state.matches.length) return onboarding(state, ctx);
  const scope = overviewScope(state);
  const matches = scope.season === 'all' ? state.matches : state.matches.filter(m => Number(m.season) === Number(scope.season));
  const stats = summarize(matches, ownerId(state));
  const options = [
    ...scope.seasons.map(season => ({ value: String(season), label: t('seasonN', { n: season }) + (season === Number(state.app.active_season) ? ' · ' + t('current') : '') })),
    { value: 'all', label: t('allSeasons') },
  ];
  return h('div', { class: 'overview' },
    toolbar(
      select({ value: String(scope.season), options, label: t('season'), onChange: value => store.merge('overviewUi', { season: value }) }),
      h('span', { class: 'toolbar-note muted' }, matches.length ? t('basedOnMatches', { count: matches.length }) : t('noMatchesInSeason'))),
    matches.length ? [
      h('div', { class: 'overview-top' }, seasonHero(state, ctx, scope, matches, stats), formPanel(state, stats)),
      kpis(stats),
      h('div', { class: 'overview-grid' },
        rivalsPanel(state, ctx, matches),
        mapsPanel(state, matches),
        recentPanel(state, ctx, matches),
        livePanel(state, ctx)),
    ] : emptyState({ icon: 'history', title: t('noMatchesInSeason'), text: t('noMatchesInSeasonText') }));
}
