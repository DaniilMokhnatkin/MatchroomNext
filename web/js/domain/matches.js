// Pure match logic shared by the views and the Node tests. Nothing here
// touches the DOM or the store; callers pass the owner id and name lookup.

export const OBSERVER_TEAM = -3;

/** Assign every player a role relative to the archive owner. */
export function perspective(match, ownId = '') {
  const players = match.players || [];
  const local = players.find(p => ownId && p.steam_id === ownId)
    || players.find(p => String(p.client_id) === String(match.recorder_client_id))
    || players.find(p => p.role === 'self');
  return players.map(p => {
    let role = 'player';
    if (p === local) role = ownId && p.steam_id !== ownId ? 'recorder' : 'you';
    else if (p.team === OBSERVER_TEAM) role = 'observer';
    else if (local?.side && p.side) role = local.side === p.side ? 'ally' : 'opponent';
    return { ...p, role };
  });
}

/** [own player, main opponent, all players with roles]. */
export function pair(match, ownId = '') {
  const players = perspective(match, ownId);
  const left = players.find(p => p.role === 'you' || p.role === 'recorder') || players.find(p => p.side) || players[0];
  const right = players.find(p => p.role === 'opponent')
    || players.find(p => p !== left && p.side && p.side !== left?.side);
  return [left, right, players];
}

export function outcome(match, ownId = '') {
  if (match.outcome === 'draw') return 'draw';
  if (!match.winner_side) return 'unknown';
  const own = pair(match, ownId)[0];
  if (!own?.side) return 'unknown';
  return own.side === match.winner_side ? 'win' : 'loss';
}

/**
 * Rating change of the recording owner.
 * kind: confirmed | pending | none | other (calculated for another account).
 */
export function ratingChange(match, displayId = '') {
  const change = match.rating_change;
  if (displayId && change?.steam_id && change.steam_id !== displayId) return { kind: 'other', value: null };
  const delta = change?.delta;
  if (change?.status === 'confirmed' && delta !== null && delta !== undefined && Number.isFinite(Number(delta))) {
    return { kind: 'confirmed', value: Number(delta) };
  }
  return { kind: change?.status === 'pending' ? 'pending' : 'none', value: null };
}

export function deltaTone(value) {
  if (value === null || value === undefined) return 'neutral';
  return value > 0 ? 'win' : value < 0 ? 'loss' : 'neutral';
}

export function dateOf(match) {
  if (match.played_at_ns) {
    const date = new Date(Number(match.played_at_ns) / 1e6);
    if (!Number.isNaN(date.valueOf())) return date;
  }
  const raw = match.date || match.file?.match(/_(20\d{6}_\d{6})/)?.[1];
  if (!raw) return null;
  return new Date(Number(raw.slice(0, 4)), Number(raw.slice(4, 6)) - 1, Number(raw.slice(6, 8)),
    Number(raw.slice(9, 11)), Number(raw.slice(11, 13)), Number(raw.slice(13, 15)));
}

export function sortMatches(matches) {
  return [...matches].sort((a, b) => (Number(b.played_at_ns) || 0) - (Number(a.played_at_ns) || 0)
    || String(b.date || '').localeCompare(String(a.date || '')));
}

export function historicalRating(match, player) {
  return player?.steam_id ? match.historical_ranks?.[player.steam_id] || null : null;
}

export function boardKey(match, season) {
  return Number(season) + ':' + (match.players || []).map(p => p.steam_id).filter(Boolean).sort().join(',');
}

export function activePlayers(match) {
  return (match.players || []).filter(p => p.team !== OBSERVER_TEAM && p.side);
}

export function modeLabel(match) {
  const count = activePlayers(match).length;
  if (count === 2) return '1v1';
  if (count === 4) return '2v2';
  if (count === 6) return '3v3';
  if (count === 8) return '4v4';
  return count ? String(count) : '';
}

/**
 * Filter by free text, season and result. `nameOf` resolves the current
 * Steam name so a renamed opponent is still found by the new name.
 */
export function filterMatches(matches, { query = '', season = '', result = '', opponent = '' } = {}, { ownId = '', nameOf = () => '', dateText = () => '' } = {}) {
  const q = query.trim().toLowerCase();
  return matches.filter(match => {
    if (season && String(match.season) !== String(season)) return false;
    if (result && outcome(match, ownId) !== result) return false;
    if (opponent && !(match.players || []).some(p => p.steam_id === opponent)) return false;
    if (!q) return true;
    const haystack = [match.map, match.file, dateText(dateOf(match)),
      ...(match.players || []).flatMap(p => [p.name, p.steam_id, nameOf(p.steam_id)])].join(' ').toLowerCase();
    return haystack.includes(q);
  });
}

export function seasonsOf(matches) {
  return [...new Set(matches.map(m => Number(m.season)).filter(Number.isFinite))].sort((a, b) => b - a);
}

/* ---------- Statistics for the overview ---------- */

function scoreAfter(match) {
  const change = match.rating_change || {};
  if (change.after_score !== null && change.after_score !== undefined) return Number(change.after_score);
  if (change.before_score !== null && change.before_score !== undefined && change.delta !== null && change.delta !== undefined) {
    return Number(change.before_score) + Number(change.delta);
  }
  return null;
}

/** Aggregate a set of matches (newest first) from the owner's perspective. */
export function summarize(matches, ownId = '') {
  const results = matches.map(match => ({ match, result: outcome(match, ownId) }));
  const count = result => results.filter(r => r.result === result).length;
  const wins = count('win'), losses = count('loss'), draws = count('draw');
  const decided = wins + losses;

  // Current streak: consecutive wins or losses from the newest match.
  let streak = { type: null, count: 0 };
  for (const { result } of results) {
    if (result !== 'win' && result !== 'loss') break;
    if (!streak.type) streak = { type: result, count: 1 };
    else if (streak.type === result) streak.count++;
    else break;
  }

  let bestWinStreak = 0, run = 0;
  for (const { result } of [...results].reverse()) {
    run = result === 'win' ? run + 1 : 0;
    bestWinStreak = Math.max(bestWinStreak, run);
  }

  const deltas = [];
  const series = [];
  for (const match of [...matches].reverse()) {
    const own = pair(match, ownId)[0];
    const change = ratingChange(match, own?.steam_id);
    if (change.kind !== 'confirmed') continue;
    deltas.push(change.value);
    const score = scoreAfter(match);
    if (score !== null && Number.isFinite(score)) {
      series.push({ id: match.match_id, date: dateOf(match), score, delta: change.value });
    }
  }
  const gains = deltas.filter(d => d > 0), drops = deltas.filter(d => d < 0);
  const mean = list => list.length ? list.reduce((a, b) => a + b, 0) / list.length : null;
  const scores = series.map(p => p.score);

  return {
    total: matches.length,
    wins, losses, draws,
    unknown: count('unknown'),
    winRate: decided ? wins / decided : null,
    streak,
    bestWinStreak,
    deltaSum: deltas.length ? deltas.reduce((a, b) => a + b, 0) : null,
    confirmed: deltas.length,
    avgGain: mean(gains),
    avgDrop: mean(drops),
    peak: scores.length ? Math.max(...scores) : null,
    low: scores.length ? Math.min(...scores) : null,
    series,
    form: results.slice(0, 10).reverse().map(({ match, result }) => ({ id: match.match_id, result, date: dateOf(match), map: match.map })),
  };
}

/** Head-to-head table against each opponent, most frequent first. */
export function opponentStats(matches, ownId = '', nameOf = () => '') {
  const rows = new Map();
  for (const match of matches) {
    const [, rival] = pair(match, ownId);
    if (!rival) continue;
    const key = rival.steam_id || 'name:' + (rival.name || '?');
    const row = rows.get(key) || { key, steam_id: rival.steam_id || '', name: rival.name || '', total: 0, wins: 0, losses: 0, draws: 0, last: null, lastId: null };
    const result = outcome(match, ownId);
    row.total++;
    if (result === 'win') row.wins++;
    else if (result === 'loss') row.losses++;
    else if (result === 'draw') row.draws++;
    const date = dateOf(match);
    if (!row.last || (date && date > row.last)) { row.last = date; row.lastId = match.match_id; row.name = rival.name || row.name; }
    rows.set(key, row);
  }
  return [...rows.values()]
    .map(row => ({ ...row, name: nameOf(row.steam_id) || row.name, winRate: row.wins + row.losses ? row.wins / (row.wins + row.losses) : null }))
    .sort((a, b) => b.total - a.total || (b.last?.getTime() || 0) - (a.last?.getTime() || 0));
}

export function mapStats(matches, ownId = '') {
  const rows = new Map();
  for (const match of matches) {
    const name = match.map || '—';
    const row = rows.get(name) || { map: name, total: 0, wins: 0, losses: 0, draws: 0 };
    const result = outcome(match, ownId);
    row.total++;
    if (result === 'win') row.wins++;
    else if (result === 'loss') row.losses++;
    else if (result === 'draw') row.draws++;
    rows.set(name, row);
  }
  return [...rows.values()]
    .map(row => ({ ...row, winRate: row.wins + row.losses ? row.wins / (row.wins + row.losses) : null }))
    .sort((a, b) => b.total - a.total || a.map.localeCompare(b.map));
}

export function validSteamId(id) {
  if (!id) return true;
  if (!/^[0-9]{17}$/.test(id)) return false;
  const value = BigInt(id);
  return (value >> 32n) === 0x01100001n && (value & 0xffffffffn) !== 0n;
}

/** Steam ID of the original Matchroom author, shown with a credit badge. */
export const ORIGINAL_AUTHOR_ID = '76561198310546409';
