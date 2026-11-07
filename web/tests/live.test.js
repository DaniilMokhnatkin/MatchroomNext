import test from 'node:test';
import assert from 'node:assert/strict';
import { stakes, compareRows, edge, mapRecord, phaseIndex } from '../js/domain/live.js';
import { playable, filterGames, summarizeCasual, byMode, teammates, opponents } from '../js/domain/casual.js';

test('stakes follow round(32 x E) and 32 back for a win', () => {
  assert.deepEqual(stakes(0.41), { win: 19, loss: 13 });
  assert.deepEqual(stakes(0.5), { win: 16, loss: 16 });
  assert.equal(stakes(null), null);
  assert.equal(phaseIndex('select'), 1);
});

test('comparison rows pick the leader by direction', () => {
  const rows = compareRows(
    { internal_rating: 1214, wins: 26, losses: 12, top_pct: 3.7, efficiency: { slips: 1, performance: 1250 } },
    { internal_rating: 1288, wins: 31, losses: 9, top_pct: 1.1, efficiency: { slips: 4, performance: 1250 } });
  const byId = Object.fromEntries(rows.map(row => [row.id, row]));
  assert.equal(byId.internal.leader, 'b');
  assert.equal(byId.slips.leader, 'a');
  assert.equal(byId.topPct.leader, 'b');
  assert.equal(byId.performance.leader, null);
  assert.equal(byId.seasonRate.noteA, '26–12');
  assert.ok(byId.internal.share > 0.45 && byId.internal.share < 0.5);
  assert.equal(rows.find(row => row.id === 'careerRate'), undefined);
  assert.deepEqual(edge(rows), { a: 1, b: 3, total: 5 });
});

test('negative values keep a valid bar share', () => {
  const [row] = compareRows({ efficiency: { over_expected: -2 } }, { efficiency: { over_expected: 3 } });
  assert.equal(row.leader, 'b');
  assert.ok(row.share >= 0 && row.share <= 1);
});

test('map record ignores the 1v1 suffix', () => {
  assert.deepEqual(mapRecord({ maps: [{ map: 'Foundry', wins: 2, losses: 1 }] }, 'Foundry 1v1'), { wins: 2, losses: 1 });
  assert.equal(mapRecord({ maps: [] }, 'Foundry'), null);
});

const ME = '1';
const game = (id, result, mode, date, players = []) => ({
  id, result, mode, date, map: 'Pillars 3v3', duration: 600, multiplayer: true, self_id: ME,
  players: [{ steam_id: ME, role: 'self', name: 'Me' }, ...players],
});

test('lobby game stats', () => {
  const ally = { steam_id: '2', role: 'ally', name: 'Ally' };
  const foe = { steam_id: '3', role: 'opponent', name: 'Foe' };
  const games = [
    game('a', 'win', '3v3', '20261106_200000', [ally, foe]),
    game('b', 'win', '3v3', '20261105_200000', [ally, foe]),
    game('c', 'loss', '2v2', '20261001_200000', [foe]),
    game('d', 'unknown', '5v3', '20261001_100000'),
    { ...game('e', 'observer', '2v2', '20261001_100000'), result: 'observer' },
    { ...game('f', 'win', 'other', '20261001_100000'), multiplayer: false },
  ];
  const list = playable(games, ME);
  assert.equal(list.length, 4);
  const stats = summarizeCasual(list);
  assert.deepEqual([stats.wins, stats.losses, stats.unknown], [2, 1, 1]);
  assert.deepEqual(stats.streak, { type: 'win', count: 2 });
  assert.equal(stats.avgDuration, 600);
  assert.deepEqual(byMode(list).map(row => row.mode), ['2v2', '3v3', 'other']);
  assert.equal(filterGames(list, { mode: '3v3' }).length, 2);
  assert.equal(filterGames(list, { days: 7, now: new Date(2026, 10, 7).getTime() }).length, 2);
  assert.deepEqual(teammates(list).map(row => [row.name, row.total, row.wins]), [['Ally', 2, 2]]);
  assert.equal(opponents(list)[0].total, 3);
});

import { formatMetric, deviationText, deviationTone, dossierScope, internalSeries } from '../js/domain/analysis.js';

test('analysis metric formatting and scope', () => {
  assert.equal(formatMetric('time', 134), '2:14');
  assert.equal(formatMetric('ratio', 0.37), '0.37×');
  assert.equal(formatMetric('ratio', 59.6), '60×');
  assert.equal(formatMetric('int', null), '—');
  assert.equal(deviationText({ pct: -0.183 }), '−18%');
  assert.equal(deviationTone({ verdict: 'above' }), 'win');
  assert.equal(dossierScope({ matches: [{ mode: '1v1' }, { mode: '4v4' }, { mode: '3v3' }] }), 'team');
  assert.equal(dossierScope({ matches: [] }, 'duel'), 'duel');
  assert.equal(internalSeries([{ at: 1, internal: 1200 }, { at: 2, internal: null }]).length, 1);
});
