// Live match: who you face from commander select on, and how both of you play.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, signed, percent, duration, dateCompact } from '../lib/i18n.js';
import { routeHash } from '../lib/router.js';
import { PHASES, phaseIndex, stakes, compareRows, edge, mapRecord } from '../domain/live.js';
import { nameOf, ownerId, openSettings } from '../app/controller.js';
import { panel, avatar, chip, meter, emptyState, hint, button, externalLink } from '../ui/components.js';
import { rankText } from './common.js';

const ACTIVE = ['queue', 'select', 'loading', 'playing'];

export function liveHeadline(state) {
  const live = state.app.live_match;
  if (!live || live.phase === 'off') return { title: t('liveOff'), tone: 'neutral', active: false };
  if (live.phase === 'idle' || live.phase === 'lobby' || !live.ranked) return { title: t('liveIdle'), tone: 'neutral', active: false };
  const id = live.opponent?.steam_id;
  const name = id ? (nameOf(id, state) || live.opponent.name || live.intel?.opponent?.name || t('player')) : null;
  if (live.phase === 'finished') return { title: t('liveFinished'), tone: 'neutral', active: false, name };
  if (live.phase === 'queue') return { title: t('phase_queue'), tone: 'accent', active: true };
  return { title: name ? t('liveVs', { name }) : t('opponentHidden'), tone: 'accent', active: true, name };
}

function since(live) {
  if (!live.since) return '';
  return duration(Math.max(0, Math.round(Date.now() / 1000 - live.since)));
}

function stepper(live) {
  const current = phaseIndex(live.phase);
  return h('ol', { class: 'live-steps', 'aria-label': t('livePhases') },
    PHASES.map((phase, index) => h('li', {
      key: phase, class: ['live-step', { 'is-done': index < current, 'is-current': index === current }],
      'aria-current': index === current ? 'step' : null,
    },
    h('span', { class: 'live-step-mark', 'aria-hidden': 'true' }, index < current ? icon('check', 'icon-sm') : String(index + 1)),
    h('span', { class: 'live-step-label' }, t('phase_' + phase)),
    index === current && live.since ? h('span', { class: 'live-step-time num muted' }, since(live)) : null)));
}

function sideCard(state, { id, summary, board, tone, label, hidden, internal }) {
  const profile = id ? state.profiles[id] : null;
  const name = (id && nameOf(id, state)) || summary?.name || (hidden ? t('opponentHidden') : t('player'));
  const row = board || {};
  return h('div', { class: ['duel-side', 'duel-side-' + tone] },
    avatar({ name, src: profile?.avatar, size: 'lg' }),
    h('span', { class: 'caps duel-side-label' }, label),
    id ? h('a', { class: 'duel-name ellipsis', title: t('openDossier'), href: routeHash('player', id) }, name) : h('strong', { class: 'duel-name ellipsis', title: name }, name),
    h('span', { class: 'num duel-rank' }, rankText(row.rank ?? summary?.rank, row.score ?? summary?.score)),
    internal ? chip(t('internalShort', { value: number(internal) }), { tone: 'neutral' }) : null,
    hidden ? h('small', { class: 'muted duel-hidden' }, t('opponentHiddenHint')) : null);
}

function duel(state, live) {
  const intel = live.intel || {};
  const own = live.self_id || ownerId(state);
  const opponentId = live.opponent?.steam_id || null;
  const chance = typeof intel.win_chance === 'number' ? intel.win_chance
    : typeof live.opponent?.win_chance === 'number' ? 1 - live.opponent.win_chance : null;
  const stake = stakes(chance);
  const board = intel.board || {};
  return h('section', { class: 'duel panel' },
    sideCard(state, { id: own, summary: intel.self, board: board[own], tone: 'you', label: t('you'), internal: intel.internal?.[own] ?? intel.self?.internal_rating }),
    h('div', { class: 'duel-center' },
      h('span', { class: 'caps' }, t('winChance')),
      h('strong', { class: 'duel-chance num' }, chance !== null ? percent(chance) : intel.loading ? '…' : '—'),
      chance !== null ? h('div', { class: 'duel-bar', role: 'img', 'aria-label': t('chanceBar', { you: percent(chance), them: percent(1 - chance) }) },
        h('span', { class: 'duel-bar-you', style: { '--value': String(chance) } })) : null,
      stake ? h('div', { class: 'duel-stakes num' },
        h('span', { class: 'tone-win', title: t('stakeWinHint') }, signed(stake.win)),
        h('span', { class: 'muted' }, ' / '),
        h('span', { class: 'tone-loss', title: t('stakeLossHint') }, signed(-stake.loss)),
        hint(t('stakeHint'), t('aboutData'))) : null,
      live.map ? chip(live.map, { icon: 'map' }) : null,
      intel.chance_basis ? h('small', { class: 'muted' }, t(intel.chance_basis === 'internal' ? 'basisInternal' : 'basisScore')) : null),
    sideCard(state, { id: opponentId, summary: intel.opponent, board: opponentId ? board[opponentId] : null, tone: 'them', label: t('opponent'), hidden: !opponentId, internal: opponentId ? intel.internal?.[opponentId] ?? intel.opponent?.internal_rating : null }));
}

function formatValue(row, value) {
  if (value === null || value === undefined) return '—';
  if (row.kind === 'rate') return percent(value);
  if (row.kind === 'signed') return (value > 0 ? '+' : value < 0 ? '−' : '') + Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (row.kind === 'pct') return t('topPctValue', { value: value.toLocaleString(undefined, { maximumFractionDigits: 1 }) });
  return number(Math.round(value));
}

function comparison(live) {
  const intel = live.intel || {};
  const own = live.self_id;
  const opponentId = live.opponent?.steam_id;
  const rows = compareRows(intel.self, intel.opponent, { a: intel.board?.[own], b: intel.board?.[opponentId] });
  const score = edge(rows);
  let body;
  if (intel.loading) body = h('p', { class: 'panel-empty' }, t('intelLoading'));
  else if (!rows.length) body = h('p', { class: 'panel-empty' }, live.fortstools?.enabled ? t('intelEmpty') : t('intelNeedsFortsTools'));
  else {
    body = h('ul', { class: 'versus-list' }, rows.map(row => h('li', { key: row.id, class: 'versus-row' },
      h('span', { class: ['versus-value', 'num', { 'is-lead': row.leader === 'a' }] },
        row.leader === 'a' ? h('span', { class: 'versus-lead', 'aria-label': t('leads') }, '▲') : null,
        formatValue(row, row.a), row.noteA ? h('small', { class: 'muted' }, row.noteA) : null),
      h('div', { class: 'versus-mid' },
        h('span', { class: 'versus-label' }, t('cmp_' + row.id), hint(t('cmpHint_' + row.id), t('aboutData'))),
        h('span', { class: 'versus-bar', 'aria-hidden': 'true' }, h('span', { class: 'versus-bar-a', style: { '--value': String(row.share) } }))),
      h('span', { class: ['versus-value', 'versus-value-b', 'num', { 'is-lead': row.leader === 'b' }] },
        row.noteB ? h('small', { class: 'muted' }, row.noteB) : null, formatValue(row, row.b),
        row.leader === 'b' ? h('span', { class: 'versus-lead', 'aria-label': t('leads') }, '▲') : null))));
  }
  return panel({
    title: t('efficiency'), className: 'versus-panel',
    caption: score.total ? t('edgeCaption', { a: score.a, b: score.b, count: score.total }) : t('efficiencyCaption'),
    children: body,
  });
}

function formStrip(form) {
  if (!form?.length) return null;
  return h('ol', { class: 'form-strip form-strip-sm', 'aria-label': t('form') },
    form.map((won, index) => h('li', { key: String(index) },
      h('span', { class: 'form-cell', 'data-result': won ? 'win' : 'loss', title: t(won ? 'resultWin' : 'resultLoss') }, t(won ? 'letterWin' : 'letterLoss')))));
}

function headToHead(state, live) {
  const h2h = live.intel?.h2h;
  return panel({
    title: t('headToHead'), caption: t('fromYourReplays'), className: 'h2h-panel',
    children: !h2h || !h2h.games ? h('p', { class: 'panel-empty' }, t('h2hEmpty')) : [
      h('div', { class: 'h2h-score num' },
        h('span', { class: 'tone-win' }, number(h2h.wins)), h('span', { class: 'muted' }, ' – '), h('span', { class: 'tone-loss' }, number(h2h.losses))),
      h('ul', { class: 'h2h-list' }, h2h.recent.map(row => h('li', { key: row.match_id },
        h('a', { class: 'h2h-row', href: routeHash('matches', row.match_id), 'data-result': row.won === null ? 'unknown' : row.won ? 'win' : 'loss' },
          h('span', { class: 'recent-mark', 'aria-hidden': 'true' }),
          h('span', null, t(row.won === null ? 'resultUnknown' : row.won ? 'resultWin' : 'resultLoss')),
          h('span', { class: 'ellipsis muted' }, row.map || '—'),
          h('time', { class: 'num muted' }, row.date ? dateCompact(parseStamp(row.date)) : '—'))))),
    ],
  });
}

function parseStamp(value) {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/.exec(value || '');
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null;
}

function scouting(state, live) {
  const other = live.intel?.opponent;
  const id = live.opponent?.steam_id;
  if (!other) {
    return panel({ title: t('scouting'), className: 'scout-panel',
      children: h('p', { class: 'panel-empty' }, live.fortstools?.enabled ? t('scoutEmpty') : t('intelNeedsFortsTools')) });
  }
  const onMap = mapRecord(other, live.map);
  const streak = other.streak?.current;
  return panel({
    title: t('scouting'), caption: other.name || '', className: 'scout-panel',
    actions: other.source ? externalLink({ href: other.source, label: 'forts-tools' }) : null,
    children: [
      formStrip(other.form),
      h('dl', { class: 'scout-facts' },
        h('div', null, h('dt', { class: 'caps' }, t('streakNow')),
          h('dd', { class: ['num', streak ? (streak.won ? 'tone-win' : 'tone-loss') : ''] },
            streak ? t(streak.won ? 'streakWins' : 'streakLosses', { count: streak.n }) : '—')),
        h('div', null, h('dt', { class: 'caps' }, t('onThisMap')),
          h('dd', { class: 'num' }, onMap ? `${onMap.wins}–${onMap.losses}` : t('noMapRecord'))),
        h('div', null, h('dt', { class: 'caps' }, t('bestFinish')),
          h('dd', { class: 'num' }, other.best_finish?.rank ? t('bestFinishValue', { rank: other.best_finish.rank, season: other.best_finish.season }) : '—')),
        h('div', null, h('dt', { class: 'caps' }, t('careerGames')),
          h('dd', { class: 'num' }, other.career?.games ? number(other.career.games) : '—'))),
      other.maps?.length ? h('ul', { class: 'bar-list' }, other.maps.slice(0, 5).map(row => {
        const total = (row.wins || 0) + (row.losses || 0);
        const rate = total ? row.wins / total : null;
        return h('li', { key: row.map, class: ['bar-item', { 'is-current': mapRecord({ maps: [row] }, live.map) }] },
          h('div', { class: 'bar-item-head' }, h('span', { class: 'ellipsis' }, row.map), h('span', { class: 'num muted' }, `${row.wins}–${row.losses}`)),
          h('div', { class: 'bar-item-body' }, meter(rate ?? 0, rate === null ? 'neutral' : rate >= 0.5 ? 'loss' : 'win'),
            h('span', { class: 'num' }, rate !== null ? percent(rate) : '—')));
      })) : null,
      id ? h('small', { class: 'muted mono' }, id) : null,
    ],
  });
}

function sourceNote(live) {
  const source = live.opponent?.source;
  const text = source ? t('source_' + source) : t('source_none');
  return h('p', { class: 'live-source muted' },
    icon(live.opponent?.confidence === 'probable' ? 'help' : 'info', 'icon-sm'),
    h('span', null, text),
    h('span', null, ' · ' + t('tokensKnown', { count: live.tokens_known || 0 })));
}

function idle(state, live) {
  const off = !live || live.phase === 'off';
  return h('div', { class: 'live-idle' },
    emptyState({
      icon: 'swords', title: t(off ? 'liveOffTitle' : 'liveIdleTitle'), text: t(off ? 'liveOffText' : 'liveIdleText'),
      actions: live && !live.fortstools?.enabled ? [button({ label: t('settings'), icon: 'settings', onClick: openSettings })] : null,
    }),
    h('ol', { class: 'steps live-how' },
      ['howQueue', 'howSelect', 'howAfter'].map((key, index) => h('li', { class: 'step', key },
        h('span', { class: 'step-mark' }, String(index + 1)),
        h('div', null, h('strong', null, t(key)), h('small', null, t(key + 'Hint')))))),
    live ? sourceNote(live) : null);
}

function finishedBanner(state, live) {
  const result = live.result;
  if (!result) return null;
  return h('div', { class: ['live-result', result.won ? 'is-win' : 'is-loss'], role: 'status' },
    icon(result.won ? 'trophy' : 'flame'),
    h('strong', null, t(result.won ? 'liveWon' : 'liveLost')),
    result.replay ? h('span', { class: 'muted ellipsis' }, result.replay) : null);
}

export function liveView(state, ctx) {
  const live = state.app.live_match;
  if (!live || !live.ranked || !['queue', 'select', 'loading', 'playing', 'finished'].includes(live.phase)) return idle(state, live);
  if (live.phase === 'finished' && !live.opponent) return [finishedBanner(state, live), idle(state, live)];
  return h('div', { class: 'live' },
    stepper(live),
    live.phase === 'finished' ? finishedBanner(state, live) : null,
    live.phase === 'queue' && !live.opponent
      ? h('section', { class: 'panel live-searching', role: 'status' },
        h('span', { class: 'live-pulse', 'aria-hidden': 'true' }),
        h('div', null, h('strong', null, t('searchingTitle')), h('p', { class: 'muted' }, t(live.hosting ? 'searchingHost' : 'searchingJoin'))))
      : [
        duel(state, live),
        h('div', { class: 'live-grid' }, comparison(live), h('div', { class: 'live-side' }, headToHead(state, live), scouting(state, live))),
      ],
    sourceNote(live));
}

export const liveActive = state => ACTIVE.includes(state.app.live_match?.phase) && !!state.app.live_match?.ranked;
