// Domain and i18n tests for the Next client. Run from the project folder:
//   node --test web/tests
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  perspective, outcome, ratingChange, historicalRating, filterMatches, summarize, opponentStats, mapStats, validSteamId, modeLabel,
} from '../js/domain/matches.js';
import { filterLobbies, isWatchable, comparisonCandidate, tabCounts, activitySummary } from '../js/domain/lobbies.js';
import { activityValues } from '../js/domain/activity.js';
import { t, setLanguage, signed } from '../js/lib/i18n.js';
import { parseHash, routeHash } from '../js/lib/router.js';
import ru from '../js/i18n/ru.js';
import en from '../js/i18n/en.js';

const SELF = '76561197960265729';
const ENEMY = '76561197960265730';
const base = {
  match_id: '1', season: 43, date: '20260928_120000', file: '43_v1_Test_20260928_120000.fwr', map: 'Stalactites',
  recorder_client_id: 1,
  players: [{ steam_id: SELF, name: 'Победа', client_id: 1, side: 1 }, { steam_id: ENEMY, name: 'Win', client_id: 2, side: 2 }],
  winner_side: 1, outcome: 'win', historical_ranks: { [SELF]: { rank: 50, score: 4300 } },
  rating_change: { steam_id: SELF, status: 'confirmed', delta: 26, before_score: 4300, after_score: 4326 },
};
const dateText = date => date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });

test('result and rating ownership', () => {
  assert.equal(outcome(base, SELF), 'win');
  assert.equal(outcome(base, ENEMY), 'loss');
  assert.equal(perspective(base, ENEMY)[1].role, 'you');
  assert.deepEqual(ratingChange(base, SELF), { kind: 'confirmed', value: 26 });
  assert.equal(ratingChange({ ...base, rating_change: { steam_id: SELF, status: 'confirmed', delta: 0 } }, SELF).value, 0);
  assert.equal(ratingChange({ ...base, rating_change: { status: 'unavailable', delta: null } }, SELF).value, null);
  assert.equal(ratingChange(base, ENEMY).kind, 'other');
  assert.equal(historicalRating(base, base.players[0]).rank, 50);
  assert.equal(modeLabel(base), '1v1');
});

test('a foreign recording is labelled as the recorder, not "you"', () => {
  const match = { recorder_client_id: 1, players: [{ client_id: 1, steam_id: SELF, side: 1 }, { client_id: 2, steam_id: ENEMY, side: 2 }] };
  assert.equal(perspective(match, '76561197960265732')[0].role, 'recorder');
  assert.equal(match.players[0].steam_id, SELF);
});

test('filters, renamed opponents and a long archive', () => {
  const ctx = { ownId: SELF, nameOf: id => (id === ENEMY ? 'Ironclad' : ''), dateText };
  assert.equal(filterMatches([base], { query: 'победа' }, ctx).length, 1);
  assert.equal(filterMatches([base], { season: 44 }, ctx).length, 0);
  assert.equal(filterMatches([base], { season: 43, result: 'loss' }, ctx).length, 0);
  assert.equal(filterMatches([base], { query: '28.09.2026', season: 43, result: 'win' }, ctx).length, 1);
  assert.equal(filterMatches([base], { query: 'ironclad' }, ctx).length, 1);
  assert.equal(filterMatches([base], { opponent: ENEMY }, ctx).length, 1);
  assert.equal(filterMatches(Array.from({ length: 180 }, () => base), {}, ctx).length, 180);
});

test('season summary: record, streaks, rating series', () => {
  const make = (id, result, delta, after, hour) => ({
    ...base, match_id: id, date: `20260928_${String(hour).padStart(2, '0')}0000`, played_at_ns: undefined,
    winner_side: result === 'win' ? 1 : result === 'loss' ? 2 : null, outcome: result,
    rating_change: delta === null ? { status: 'pending' } : { steam_id: SELF, status: 'confirmed', delta, after_score: after },
  });
  // Newest first, as in the store.
  const matches = [make('5', 'win', 20, 4360, 15), make('4', 'win', 25, 4340, 14), make('3', 'loss', -15, 4315, 13), make('2', 'draw', null, null, 12), make('1', 'win', 30, 4330, 11)];
  const stats = summarize(matches, SELF);
  assert.equal(stats.wins, 3);
  assert.equal(stats.losses, 1);
  assert.equal(stats.winRate, 0.75);
  assert.deepEqual(stats.streak, { type: 'win', count: 2 });
  assert.equal(stats.bestWinStreak, 2);
  assert.equal(stats.deltaSum, 60);
  assert.deepEqual(stats.series.map(p => p.score), [4330, 4315, 4340, 4360]);
  assert.equal(stats.peak, 4360);
  assert.equal(stats.form.at(-1).id, '5');
  assert.equal(opponentStats(matches, SELF)[0].total, 5);
  assert.equal(mapStats(matches, SELF)[0].wins, 3);
});

test('Steam ID validation', () => {
  assert.equal(validSteamId(SELF), true);
  assert.equal(validSteamId('11111111111111111'), false);
  assert.equal(validSteamId('123'), false);
  assert.equal(validSteamId(''), true);
});

test('online counters never turn unknown into zero', () => {
  const now = Date.now();
  assert.deepEqual(activityValues({ checked_at: now / 1000, status: 'ok', game_running: true, online_players: 642, ranked_waiting_players: 0 }, now), { online: 642, queue: 0, truncated: false });
  assert.deepEqual(activityValues({ checked_at: (now - 100000) / 1000, status: 'ok', online_players: 642, ranked_waiting_players: 3 }, now), { online: null, queue: null, truncated: false });
  assert.deepEqual(activityValues({ checked_at: now / 1000, status: 'partial', game_running: false, online_players: 642, ranked_waiting_players: null }, now), { online: 642, queue: null, truncated: false });
});

test('lobby filters, favorites, sorting and own-room exclusion', () => {
  const room = (id, extra = {}) => ({ id, name: 'Room ' + id, map: 'Stalactites', host: 'Win', own: false, ranked: false, in_progress: false, password: false, players: 1, max_players: 4, free_slots: 3, activity: { NetChange: 0, CoverageSeconds: 300 }, ...extra });
  const rows = [room('own', { own: true }), room('new', { activity: { IsNew: true, NetChange: 0, CoverageSeconds: 300 } }), room('full', { players: 4, free_slots: 0 }), room('playing', { in_progress: true }), room('ranked', { ranked: true }), room('password', { password: true }), room('unknown', { players: null, max_players: null, free_slots: null, password: null }), room('growth', { activity: { NetChange: 3, CoverageSeconds: 300 }, players: 3, free_slots: 1 })];
  const filter = extra => filterLobbies(rows, { tab: 'recruiting', query: '', sort: 'new', favorites: [], ...extra });
  assert.ok(!filter().some(r => r.own));
  assert.ok(!filter().some(r => ['full', 'playing', 'ranked'].includes(r.id)));
  assert.equal(filter()[0].id, 'new');
  assert.deepEqual(filter({ tab: 'watching' }).map(r => r.id).sort(), ['full', 'playing']);
  assert.deepEqual(filter({ tab: 'ranked' }).map(r => r.id), ['ranked']);
  assert.ok(!filter({ noPassword: true }).some(r => r.password !== false));
  assert.deepEqual(filter({ favoritesOnly: true, favorites: ['growth'] }).map(r => r.id), ['growth']);
  assert.equal(filter({ sort: 'growth' })[0].id, 'growth');
  assert.equal(filter({ query: 'no such host' }).length, 0);
  assert.ok(isWatchable(rows.find(r => r.id === 'full')));
  assert.equal(comparisonCandidate(rows).id, 'growth');
  assert.deepEqual(tabCounts(rows), { recruiting: 4, watching: 2, all: 6, ranked: 1 });
  assert.equal(activitySummary(rows[7], { fresh: true }).kind, 'unknown');
  assert.equal(activitySummary({ ...rows[7], activity: { SampleCount: 3, NetChange: 2, CoverageSeconds: 120 } }, { fresh: true }).kind, 'net');
});

test('router keeps old links and encodes ids', () => {
  assert.deepEqual(parseHash('#matches'), { view: 'matches', id: null });
  assert.deepEqual(parseHash('#/matches/a%2Fb'), { view: 'matches', id: 'a/b' });
  assert.deepEqual(parseHash('#/nowhere'), { view: 'overview', id: null });
  assert.equal(routeHash('matches', 'a/b'), '#/matches/a%2Fb');
});

test('RU/EN: plurals, minus sign and untouched nicknames', () => {
  setLanguage('ru');
  assert.equal(t('roomsCount', { count: 1 }), '1 комната');
  assert.equal(t('roomsCount', { count: 3 }), '3 комнаты');
  assert.equal(t('roomsCount', { count: 11 }), '11 комнат');
  assert.equal(signed(-18), '−18');
  assert.equal(t('resultWin'), 'Победа');
  setLanguage('en');
  assert.equal(t('roomsCount', { count: 1 }), '1 room');
  assert.equal(t('roomsCount', { count: 5 }), '5 rooms');
  assert.equal(t('resultWin'), 'Victory');
  assert.equal(base.players[0].name, 'Победа');
  setLanguage('ru');
});

test('dictionaries match and every key used in code exists', () => {
  assert.deepEqual(Object.keys(ru).sort(), Object.keys(en).sort());
  const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'js');
  const files = [];
  const walk = dir => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (name.endsWith('.js') && !path.includes('i18n')) files.push(path);
    }
  };
  walk(root);
  const missing = new Set();
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    for (const [, key] of source.matchAll(/\bt\('([a-zA-Z0-9]+)'/g)) if (!(key in ru)) missing.add(key);
    // Keys selected by lookup tables: t({ win: 'resultWin', ... }[x]) and ternaries t(a ? 'x' : 'y').
    for (const [, body] of source.matchAll(/\bt\((\{[^}]*\}|[^()]*\?[^()]*)/g)) {
      for (const [, key] of body.matchAll(/[?:]\s*'([a-zA-Z][a-zA-Z0-9]+)'/g)) if (!(key in ru)) missing.add(key);
    }
  }
  assert.deepEqual([...missing], []);
});
