// Ordinary lobby games: records by mode, map, teammate and opponent.

export const MODES = ['1v1', '2v2', '3v3', '4v4'];

export function gameDate(game) {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(game?.date || '');
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null;
}

/** Multiplayer games recorded from our seat (not as an observer). */
export function playable(games, ownId) {
  return (games || []).filter(game => game.multiplayer && game.result !== 'observer'
    && (!ownId || !game.self_id || game.self_id === ownId));
}

export function modeOf(game) {
  return MODES.includes(game.mode) ? game.mode : 'other';
}

export function filterGames(games, { mode = '', days = 0, now = Date.now() } = {}) {
  const since = days ? now - days * 86400000 : 0;
  return games.filter(game => {
    if (mode && modeOf(game) !== mode) return false;
    if (since) {
      const date = gameDate(game);
      if (!date || date.getTime() < since) return false;
    }
    return true;
  });
}

function record(rows) {
  const wins = rows.filter(g => g.result === 'win').length;
  const losses = rows.filter(g => g.result === 'loss').length;
  return { total: rows.length, wins, losses, unknown: rows.length - wins - losses,
    winRate: wins + losses ? wins / (wins + losses) : null };
}

export function summarizeCasual(games) {
  const base = record(games);
  const timed = games.filter(g => (g.result === 'win' || g.result === 'loss') && g.duration > 0);
  const avgDuration = timed.length ? timed.reduce((s, g) => s + g.duration, 0) / timed.length : null;
  let streak = { type: null, count: 0 };
  for (const game of games) {
    if (game.result !== 'win' && game.result !== 'loss') continue;
    if (!streak.type) streak = { type: game.result, count: 1 };
    else if (streak.type === game.result) streak.count++;
    else break;
  }
  const form = games.filter(g => g.result === 'win' || g.result === 'loss').slice(0, 15).map(g => ({ id: g.id, result: g.result, map: g.map, date: gameDate(g) }));
  return { ...base, avgDuration, streak, form };
}

function groupBy(games, keyOf) {
  const groups = new Map();
  for (const game of games) {
    for (const key of [].concat(keyOf(game)).filter(Boolean)) {
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(game);
    }
  }
  return groups;
}

export function byMode(games) {
  return [...groupBy(games, modeOf)].map(([mode, rows]) => ({ mode, ...record(rows) }))
    .sort((a, b) => [...MODES, 'other'].indexOf(a.mode) - [...MODES, 'other'].indexOf(b.mode));
}

export function byMap(games) {
  return [...groupBy(games, g => g.map || '—')].map(([map, rows]) => ({ map, ...record(rows) }))
    .sort((a, b) => b.total - a.total || a.map.localeCompare(b.map));
}

function people(games, role) {
  const names = new Map();
  const groups = groupBy(games, game => game.players
    .filter(p => p.steam_id && p.role === role)
    .map(p => { names.set(p.steam_id, p.name); return p.steam_id; }));
  return [...groups].map(([steamId, rows]) => ({ steam_id: steamId, name: names.get(steamId) || '', ...record(rows) }))
    .sort((a, b) => b.total - a.total || (b.winRate ?? 0) - (a.winRate ?? 0));
}

export const teammates = games => people(games, 'ally');
export const opponents = games => people(games, 'opponent');
