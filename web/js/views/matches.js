// Matches: archive list on the left, the selected match on the right.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, signed, dateLong, dateShort, time, dayLabel, getLanguage } from '../lib/i18n.js';
import { mapImageUrl } from '../lib/api.js';
import { routeHash } from '../lib/router.js';
import {
  pair, outcome, ratingChange, dateOf, filterMatches, seasonsOf, historicalRating, boardKey, modeLabel, ORIGINAL_AUTHOR_ID,
} from '../domain/matches.js';
import {
  store, ownerId, nameOf, playerName, selectedMatch, isDemo, loadMatchSteam, loadEncounters, encountersKey, retryProfile,
  copyText, toast, toggleWatch, openSettings,
} from '../app/controller.js';
import { button, chip, select, searchField, avatar, callout, emptyState, skeleton, externalLink, iconButton, picture } from '../ui/components.js';
import { resultText, resultChip, RESULT_TONE, deltaShort, deltaTitle, deltaTone, roleText, rankText } from './common.js';

const PAGE = 80;

export function visibleMatches(state) {
  const ui = state.matchesUi;
  return filterMatches(state.matches, ui, {
    ownId: ownerId(state),
    nameOf: id => nameOf(id, state),
    dateText: dateShort,
  });
}

/* ---------- List ---------- */

function listRow(state, ctx, match, selected) {
  const own = ownerId(state);
  const [left, right] = pair(match, own);
  const result = outcome(match, own);
  const change = ratingChange(match, left?.steam_id);
  const date = dateOf(match);
  const opponent = playerName(right, state);
  return h('a', {
    key: match.match_id, class: ['match-row', { 'is-selected': selected }], href: routeHash('matches', match.match_id),
    'data-result': result, 'aria-current': selected ? 'true' : null, 'data-match-id': match.match_id,
    'data-sig': [match.match_id, selected, opponent, result, change.kind, change.value, getLanguage()].join('|'),
    onClick(event) { event.preventDefault(); ctx.router.go('matches', match.match_id, { replace: true }); },
  },
  h('span', { class: 'match-row-mark', 'aria-hidden': 'true' }),
  h('span', { class: 'match-row-main' },
    h('strong', { class: 'ellipsis' }, opponent),
    h('small', { class: 'ellipsis' }, resultText(result) + ' · ' + (match.map || 'Forts'))),
  h('span', { class: 'match-row-side' },
    h('span', { class: ['num', 'match-row-delta', 'tone-' + deltaTone(change)], title: deltaTitle(change) }, deltaShort(change)),
    h('time', { class: 'num' }, time(date))));
}

function listPane(state, ctx, filtered, selectedId) {
  const ui = state.matchesUi;
  const seasons = seasonsOf(state.matches);
  const rows = [];
  let previousDay = null;
  for (const match of filtered.slice(0, ui.limit)) {
    const date = dateOf(match);
    const day = date ? date.toDateString() : 'unknown';
    if (day !== previousDay) {
      rows.push(h('h3', { class: 'day-head caps', key: 'day:' + day }, dayLabel(date)));
      previousDay = day;
    }
    rows.push(listRow(state, ctx, match, match.match_id === selectedId));
  }
  const watch = state.app.watch || { enabled: false, folders: [] };
  const errors = state.app.status?.errors || [];
  const watchState = !watch.folders?.length ? 'missing' : errors.length ? 'error' : watch.enabled ? 'on' : 'off';
  const opponentName = ui.opponent ? nameOf(ui.opponent, state) || state.matches.flatMap(m => m.players || []).find(p => p.steam_id === ui.opponent)?.name || ui.opponent : '';

  return h('aside', { class: 'list-pane', 'aria-label': t('matchArchive') },
    h('div', { class: 'list-tools' },
      searchField({ id: 'matchSearch', value: ui.query, placeholder: t('matchSearchPlaceholder'), shortcut: '/', onInput: value => store.merge('matchesUi', { query: value, limit: PAGE }) }),
      h('div', { class: 'list-filters' },
        select({
          value: ui.season, label: t('season'), onChange: value => store.merge('matchesUi', { season: value, limit: PAGE }),
          options: [{ value: '', label: t('allSeasons') }, ...seasons.map(s => ({ value: String(s), label: t('seasonN', { n: s }) }))],
        }),
        select({
          value: ui.result, label: t('result'), onChange: value => store.merge('matchesUi', { result: value, limit: PAGE }),
          options: [
            { value: '', label: t('allResults') }, { value: 'win', label: t('filterWins') }, { value: 'loss', label: t('filterLosses') },
            { value: 'draw', label: t('filterDraws') }, { value: 'unknown', label: t('filterUnknown') },
          ],
        })),
      opponentName ? h('div', { class: 'list-active-filter' },
        chip(t('vsOpponent', { name: opponentName }), { tone: 'accent', icon: 'swords' }),
        iconButton({ icon: 'close', label: t('clearFilter'), onClick: () => store.merge('matchesUi', { opponent: '' }) })) : null),
    h('div', { class: 'list-count' },
      h('span', { class: 'caps' }, t('matchArchive')),
      h('span', { class: 'num muted' }, filtered.length === state.matches.length ? number(state.matches.length) : t('filteredOf', { shown: filtered.length, total: state.matches.length }))),
    h('div', { class: 'match-list', id: 'matchList', role: 'list' },
      !state.loaded ? skeleton(6)
        : rows.length ? rows
          : h('p', { class: 'list-empty' }, state.matches.length ? t('noSearchResults') : t('archiveEmpty')),
      filtered.length > ui.limit ? button({
        label: t('showMore', { count: Math.min(PAGE, filtered.length - ui.limit) }), variant: 'ghost', size: 'sm', className: 'list-more',
        onClick: () => store.merge('matchesUi', { limit: ui.limit + PAGE }),
      }) : null),
    h('footer', { class: 'list-foot' },
      button({ label: state.ui.importing ? t('importing') : t('addReplay'), icon: 'upload', busy: !!state.ui.importing, onClick: () => ctx.pickFiles(), className: 'list-add' }),
      h('div', { class: 'watch-line', 'data-state': watchState, title: errors.join('\n') || (watch.folders || []).join('\n') },
        h('span', { class: 'status-dot', 'aria-hidden': 'true' }),
        h('span', { class: 'ellipsis' }, t({ missing: 'watchMissing', error: 'watchError', on: 'watchOn', off: 'watchOff' }[watchState])),
        watchState === 'missing'
          ? button({ label: t('configure'), variant: 'link', size: 'sm', onClick: openSettings })
          : button({ label: t(watch.enabled ? 'pause' : 'resume'), variant: 'link', size: 'sm', onClick: toggleWatch }))));
}

/* ---------- Detail ---------- */

function fighter(state, match, player) {
  if (!player) {
    return h('section', { class: 'fighter is-empty' }, h('span', { class: 'caps' }, t('roleOpponent')), h('p', { class: 'muted' }, t('noData')));
  }
  const season = state.app.active_season;
  const board = state.boards[boardKey(match, season)];
  const row = board?.players?.[player.steam_id];
  const past = historicalRating(match, player);
  const profile = state.profiles[player.steam_id];
  const winner = match.winner_side && match.winner_side === player.side;
  const side = player.side === 1 || player.side === 2 ? player.side : null;
  const currentText = row?.rank ? rankText(row.rank, row.score) : board?.status === 'ok' ? t('unranked') : board ? t('unavailable') : '…';

  const chips = [];
  if (winner) chips.push(chip(t('winner'), { tone: 'win', icon: 'trophy' }));
  if (past?.rank > 0 && past.rank <= 20) chips.push(chip(t('top20'), { tone: 'accent' }));
  if (player.steam_id === ORIGINAL_AUTHOR_ID) chips.push(chip(t('originalAuthor'), { tone: 'info' }));

  return h('section', { class: 'fighter', 'data-role': player.role, 'data-side': side },
    h('header', { class: 'fighter-head' }, h('span', { class: 'caps fighter-role' }, roleText(player.role)), h('div', { class: 'fighter-chips' }, chips)),
    h('div', { class: 'fighter-id' },
      avatar({ name: playerName(player, state), src: profile?.avatar, size: 'lg', tone: side ? 'team-' + side : '' }),
      h('div', { class: 'fighter-name' },
        h('h3', { class: 'ellipsis' }, playerName(player, state)),
        h('span', { class: 'muted ellipsis' }, commanderName(state, match.commanders?.[player.side])))),
    h('dl', { class: 'fighter-stats' },
      h('div', null,
        h('dt', { class: 'caps' }, t('atMatchTime')),
        h('dd', { class: 'num fighter-rank' }, past?.rank ? '№ ' + number(past.rank) : '—'),
        h('small', { class: 'muted num' }, past?.rank && past.score !== null && past.score !== undefined ? t('pointsN', { count: past.score }) : past ? t('unranked') : t('noSnapshot'))),
      h('div', null,
        h('dt', { class: 'caps' }, t('nowInSeason', { n: season || '—' })),
        h('dd', { class: 'num fighter-rank fighter-rank-now' }, row?.rank ? '№ ' + number(row.rank) : '—'),
        h('small', { class: 'muted num' }, row?.rank && row.score !== null && row.score !== undefined ? t('pointsN', { count: row.score }) : currentText))),
    past ? h('p', { class: 'fighter-note' }, icon('clock', 'icon-sm'),
      past.at_match_start ? t('snapshotStart') : t('snapshotBefore', { value: Math.max(1, Math.floor((past.age_seconds || 0) / 60)) })) : null,
    h('footer', { class: 'fighter-actions' },
      player.steam_id ? [
        externalLink({
          href: 'https://steamcommunity.com/profiles/' + encodeURIComponent(player.steam_id), label: t('steamProfile'),
          demo: isDemo(state), onDemo: () => toast(t('demoUnsupported')),
        }),
        button({ label: t('copyId'), icon: 'copy', variant: 'link', size: 'sm', onClick: () => copyText(player.steam_id, t('copied')) }),
      ] : h('span', { class: 'muted' }, t('noSteamId')),
      profile?.status === 'unavailable' ? button({ label: t('retryProfile'), icon: 'refresh', variant: 'link', size: 'sm', onClick: () => retryProfile(player.steam_id) }) : null));
}

function commanderName(state, slug) {
  if (!slug) return t('commanderUnknown');
  const names = state.boot.commanderNames || {};
  return names[slug]?.[getLanguage()] || names[slug]?.en || slug.replace(/^commander-[^-]+-/, '').replaceAll('-', ' ');
}

function heroArt(state, match) {
  const src = isDemo(state)
    ? '/assets/' + (match.map?.includes('Foundry') ? 'map-foundry.svg' : 'map-stalactites.svg')
    : mapImageUrl(match.map);
  return picture({ src, className: 'match-hero-art' });
}

function summaryTab(state, match, left, change) {
  const own = match.rating_change?.steam_id && match.rating_change.steam_id === left?.steam_id;
  const before = own ? match.rating_change.before_score : null;
  const after = own ? match.rating_change.after_score : null;
  const fact = (label, value, tone) => h('div', { class: 'fact' }, h('dt', { class: 'caps' }, label), h('dd', { class: ['num', tone ? 'tone-' + tone : ''] }, value));
  return [
    h('dl', { class: 'facts' },
      fact(t('scoreBefore'), before !== null && before !== undefined ? number(before) : '—'),
      fact(t('scoreAfter'), after !== null && after !== undefined ? number(after) : '—'),
      fact(t('ratingDelta'), change.kind === 'confirmed' ? signed(change.value) : deltaTitle(change), deltaTone(change)),
      fact(t('recordedSeason'), String(match.season)),
      fact(t('date'), dateShort(dateOf(match)) + ' · ' + time(dateOf(match))),
      fact(t('replayFile'), h('span', { class: 'ellipsis mono', title: match.file || '' }, match.file || '—'))),
    h('p', { class: 'footnote' }, icon('info', 'icon-sm'), t('changeNote')),
    match.rating_change?.message && !isDemo(state) ? h('p', { class: 'footnote' }, match.rating_change.message) : null,
  ];
}

function encountersTab(state, ctx, match, right) {
  const key = encountersKey(match, state);
  if (!key) return h('p', { class: 'muted' }, t('meetingsNeedIds'));
  const data = state.encounters[key];
  if (!data || data.status === 'loading') return skeleton(3);
  if (data.status === 'error') return callout({ tone: 'error', text: t('meetingsError') });
  const own = ownerId(state);
  const list = data.matches || [];
  const decided = list.map(item => item.outcome === 'draw' ? 'draw' : item.winner_side ? (item.winner_side === item.own_side ? 'win' : 'loss') : 'unknown');
  const thisResult = outcome(match, own);
  const all = [thisResult, ...decided];
  const wins = all.filter(r => r === 'win').length, losses = all.filter(r => r === 'loss').length;
  return [
    h('div', { class: 'h2h' },
      h('div', { class: 'h2h-score num' }, h('span', { class: 'tone-win' }, number(wins)), h('span', { class: 'muted' }, ':'), h('span', { class: 'tone-loss' }, number(losses))),
      h('p', null, t('h2hSummary', { name: playerName(right, state), count: data.total }))),
    list.length ? h('ul', { class: 'encounters' }, list.map((item, index) => h('li', { key: item.match_id },
      h('button', {
        type: 'button', class: 'encounter-row',
        onClick() {
          if (!state.matches.some(m => m.match_id === item.match_id)) { toast(t('requestFailed')); return; }
          store.merge('matchesUi', { query: '', season: '', result: '', opponent: '' });
          ctx.router.go('matches', item.match_id, { replace: true });
        },
      },
      h('time', { class: 'num muted' }, dateShort(dateOf(item))),
      h('span', { class: 'ellipsis' }, item.map || 'Forts'),
      h('span', { class: 'muted' }, t('seasonN', { n: item.season })),
      resultChip(decided[index]))))) : h('p', { class: 'muted' }, t('noMeetings')),
  ];
}

function participantsTab(state, players) {
  return h('table', { class: 'data-table' },
    h('thead', null, h('tr', null, h('th', { scope: 'col' }, t('player')), h('th', { scope: 'col' }, t('role')), h('th', { scope: 'col' }, 'Steam ID'), h('th', { scope: 'col' }, h('span', { class: 'sr-only' }, t('actions'))))),
    h('tbody', null, players.map(player => h('tr', { key: player.steam_id || player.name },
      h('td', null, h('span', { class: 'cell-player' }, avatar({ name: playerName(player, state), src: state.profiles[player.steam_id]?.avatar, size: 'sm', tone: player.side ? 'team-' + player.side : '' }), h('span', { class: 'ellipsis' }, playerName(player, state)))),
      h('td', null, chip(roleText(player.role), { tone: player.role === 'you' || player.role === 'recorder' ? 'accent' : player.role === 'opponent' ? 'loss' : 'neutral' })),
      h('td', { class: 'mono muted' }, player.steam_id || '—'),
      h('td', { class: 'cell-actions' }, player.steam_id ? iconButton({ icon: 'copy', label: t('copyId'), onClick: () => copyText(player.steam_id, t('copied')) }) : null)))));
}

function detailPane(state, ctx, match) {
  if (!match) {
    return h('section', { class: 'detail-pane' }, state.loaded ? emptyState({
      icon: 'history', title: t('archiveEmptyTitle'), text: t('archiveEmpty'),
      actions: button({ label: t('addReplay'), icon: 'upload', variant: 'primary', onClick: () => ctx.pickFiles() }),
    }) : h('div', { class: 'detail' }, skeleton(5)));
  }
  const own = ownerId(state);
  const [left, right, players] = pair(match, own);
  const result = outcome(match, own);
  const change = ratingChange(match, left?.steam_id);
  const date = dateOf(match);
  const season = state.app.active_season;
  const key = boardKey(match, season);
  const board = state.boards[key];
  const busy = !!state.busy[key];
  const tab = state.matchesUi.tab;
  const notices = [];
  if (state.app.game_activity?.game_running === false && !isDemo(state)) notices.push(callout({ tone: 'warning', text: t('fortsClosed') }));
  else if (board && board.status !== 'ok') notices.push(callout({ tone: 'warning', text: t('currentUnavailable') + (board.message ? ' — ' + board.message : '') }));
  for (const warning of match.warnings || []) notices.push(callout({ tone: 'warning', text: warning }));

  const tabs = [
    { id: 'summary', label: t('tabSummary') },
    { id: 'meetings', label: t('tabMeetings') },
    { id: 'participants', label: t('tabParticipants'), count: players.length },
  ];
  if (tab === 'meetings') queueMicrotask(() => loadEncounters(match));
  const mode = modeLabel(match);

  return h('section', { class: 'detail-pane', 'aria-label': t('selectedMatch') },
    h('article', { class: 'detail', key: 'detail:' + match.match_id },
      h('button', { type: 'button', class: 'detail-back btn btn-ghost btn-sm', onClick: () => ctx.router.go('matches') }, icon('arrowLeft'), h('span', null, t('backToList'))),
      h('header', { class: 'match-hero', 'data-result': result },
        heroArt(state, match),
        h('div', { class: 'match-hero-body' },
          h('span', { class: 'caps' }, [t('seasonN', { n: match.season }), mode ? t('rankedMode', { mode }) : null].filter(Boolean).join(' · ')),
          h('div', { class: 'match-hero-result' },
            h('h2', { class: 'tone-' + RESULT_TONE[result] }, resultText(result)),
            h('span', { class: ['num', 'match-hero-delta', 'tone-' + deltaTone(change)], title: deltaTitle(change) },
              change.kind === 'confirmed' ? signed(change.value) : deltaTitle(change))),
          h('div', { class: 'match-hero-meta' },
            h('span', null, icon('clock', 'icon-sm'), dateLong(date) + ' · ' + time(date)),
            h('span', null, icon('map', 'icon-sm'), match.map || 'Forts'))),
        h('div', { class: 'match-hero-actions' },
          button({ label: t('refreshRatings'), icon: 'refresh', size: 'sm', busy, onClick: () => loadMatchSteam(true) }))),
      notices.length ? h('div', { class: 'notices' }, notices) : null,
      h('div', { class: 'duel' }, fighter(state, match, left), h('div', { class: 'duel-vs', 'aria-hidden': 'true' }, 'VS'), fighter(state, match, right)),
      h('div', { class: 'tabs' },
        h('div', { class: 'tab-list', role: 'tablist' }, tabs.map(item => h('button', {
          type: 'button', role: 'tab', id: 'tab-' + item.id, key: item.id, class: { tab: true, 'is-active': item.id === tab },
          'aria-selected': String(item.id === tab), 'aria-controls': 'tab-panel', tabindex: item.id === tab ? '0' : '-1',
          onClick: () => store.merge('matchesUi', { tab: item.id }),
          onKeydown(event) {
            const index = tabs.findIndex(x => x.id === tab);
            const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
            if (!step) return;
            event.preventDefault();
            store.merge('matchesUi', { tab: tabs[(index + step + tabs.length) % tabs.length].id });
            requestAnimationFrame(() => document.querySelector('.tab.is-active')?.focus());
          },
        }, item.label, item.count ? h('span', { class: 'segment-count num' }, item.count) : null))),
        h('div', { class: 'tab-panel', id: 'tab-panel', role: 'tabpanel', 'aria-labelledby': 'tab-' + tab },
          tab === 'meetings' ? encountersTab(state, ctx, match, right)
            : tab === 'participants' ? participantsTab(state, players)
              : summaryTab(state, match, left, change)))));
}

export function matchesView(state, ctx) {
  const filtered = visibleMatches(state);
  const match = selectedMatch(state);
  return h('div', { class: ['matches', { 'has-route-id': !!state.route.id }] },
    listPane(state, ctx, filtered, match?.match_id),
    detailPane(state, ctx, match));
}

/** Keyboard navigation through the visible list (J/K or arrows). */
export function stepMatch(state, ctx, step) {
  const filtered = visibleMatches(state).slice(0, state.matchesUi.limit);
  if (!filtered.length) return;
  const current = selectedMatch(state);
  const index = filtered.findIndex(m => m.match_id === current?.match_id);
  const next = filtered[Math.max(0, Math.min(filtered.length - 1, index + step))];
  if (next) {
    ctx.router.go('matches', next.match_id, { replace: true });
    requestAnimationFrame(() => document.querySelector(`[data-match-id="${CSS.escape(next.match_id)}"]`)?.scrollIntoView({ block: 'nearest' }));
  }
}

