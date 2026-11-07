import test from 'node:test';
import assert from 'node:assert/strict';
import { replaySides, replaySeries } from '../js/domain/analysis.js';
import { nearestReplayFrame } from '../js/ui/charts.js';

test('starting lineups keep allies together even when players moved after their fort fell', () => {
  const match = {
    players: [
      { steam_id: 'me', side: 2, team: 102, starting_team: 202 },
      { steam_id: 'ally', side: 2, team: 102, starting_team: 202 },
      { steam_id: 'enemy', side: 1, team: 101, starting_team: 101 },
      { steam_id: 'unknown', side: 2, team: 102 },
    ],
    teams: [{ team: 101, side: 1 }, { team: 102, side: 2 }, { team: 202, side: 2 }],
  };
  const groups = replaySides(match, 'me');
  assert.deepEqual(groups.map(g => g.side), [2, 1]);
  assert.deepEqual(groups[0].forts.map(f => [f.team, f.players.map(p => p.steam_id)]), [[202, ['me', 'ally']], [102, []]]);
  assert.deepEqual(replaySides(match, 'spectator').map(g => g.side), [1, 2]);
});

test('reactor values represent remaining recorded health with multiple reactors in a fort', () => {
  const forts = [{ series: { reactor_health: [200, 100, 0] } }, { series: { reactor_health: [100, 50, 0] } }];
  assert.deepEqual(replaySeries(forts, 'reactor_health', [0, 128, 256]), [100, 50, 0]);
  assert.deepEqual(replaySeries([forts[0]], 'reactor_health', [0, 128, 256]), [100, 50, 0]);
});

test('hover snaps by time when the retained replay samples are unevenly spaced', () => {
  const frames = [-1, 128, 256, 510, 900, 925];
  assert.equal(nearestReplayFrame(frames, 880), 4);
  assert.equal(nearestReplayFrame(frames, 920), 5);
  assert.equal(nearestReplayFrame(frames, 0), 0);
});
