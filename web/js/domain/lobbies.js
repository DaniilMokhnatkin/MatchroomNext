// Pure lobby logic: filtering, sorting and activity summaries.

export function isWatchable(row) {
  return row.in_progress === true || row.free_slots === 0;
}

export function recruitRate(row) {
  const activity = row.activity || {};
  return activity.CoverageSeconds >= 30 ? Math.max(0, activity.NetChange || 0) / activity.CoverageSeconds : 0;
}

export function tabOf(row) {
  if (row.ranked === true) return 'ranked';
  return isWatchable(row) ? 'watching' : 'recruiting';
}

export function tabCounts(rows) {
  const counts = { recruiting: 0, watching: 0, all: 0, ranked: 0 };
  for (const row of rows) {
    if (row.own) continue;
    const tab = tabOf(row);
    counts[tab]++;
    if (tab !== 'ranked') counts.all++;
  }
  return counts;
}

export function filterLobbies(rows, { tab = 'recruiting', query = '', noPassword = false, favoritesOnly = false, favorites = [], sort = 'new' } = {}) {
  const q = query.trim().toLowerCase();
  const starred = new Set(favorites);
  const filtered = rows.filter(row => {
    if (row.own) return false;
    if (tab === 'ranked' ? row.ranked !== true : row.ranked === true) return false;
    if (tab === 'recruiting' && isWatchable(row)) return false;
    if (tab === 'watching' && !isWatchable(row)) return false;
    if (noPassword && row.password !== false) return false;
    if (favoritesOnly && !starred.has(row.id)) return false;
    return !q || [row.name, row.map, row.host].join(' ').toLowerCase().includes(q);
  });
  return filtered.sort((a, b) => {
    if (sort === 'slots') return (b.free_slots ?? -1) - (a.free_slots ?? -1) || (b.players ?? -1) - (a.players ?? -1);
    if (sort === 'growth') return recruitRate(b) - recruitRate(a) || (b.players ?? -1) - (a.players ?? -1);
    if (sort === 'name') return (a.name || '').localeCompare(b.name || '');
    return Number(!!b.activity?.IsNew) - Number(!!a.activity?.IsNew)
      || Number(starred.has(b.id)) - Number(starred.has(a.id))
      || (b.players ?? -1) - (a.players ?? -1)
      || (a.name || '').localeCompare(b.name || '');
  });
}

/** Another recruiting room to compare with the user's own lobby. */
export function comparisonCandidate(rows) {
  return rows
    .filter(row => !row.own && row.ranked !== true && !isWatchable(row) && row.players != null && row.max_players > 0 && row.players <= row.max_players)
    .sort((a, b) => recruitRate(b) - recruitRate(a) || b.players / b.max_players - a.players / a.max_players)[0];
}

/**
 * Describe observed occupancy changes as data; the view turns it into text.
 * kind: oneShot | paused | unknown | collecting | net | stalled | mixed | unchanged
 */
export function activitySummary(row, state) {
  const a = row.activity || {};
  if (state.manual) return { kind: 'oneShot' };
  if (!state.fresh) return { kind: 'paused' };
  if (!a.SampleCount) return { kind: 'unknown' };
  if (a.SampleCount === 1) return { kind: 'collecting' };
  if (a.NetChange) return { kind: 'net', value: a.NetChange, seconds: a.CoverageSeconds };
  if (a.UnchangedSeconds >= 300 && row.in_progress === false && row.free_slots > 0) return { kind: 'stalled', seconds: a.UnchangedSeconds };
  if (a.Gains || a.Losses) return { kind: 'mixed', gains: a.Gains || 0, losses: a.Losses || 0, seconds: a.CoverageSeconds };
  return { kind: 'unchanged', seconds: a.UnchangedSeconds };
}

export function samplesOf(row) {
  return (row.activity?.Samples || [])
    .filter(point => Number.isFinite(Date.parse(point.at)) && Number.isFinite(Number(point.players)))
    .map(point => ({ at: new Date(point.at), players: Number(point.players) }));
}

export function statusKey(row) {
  if (row.ranked === true) return 'lobbyRanked';
  if (row.in_progress === true) return 'lobbyInProgress';
  if (row.free_slots === 0) return 'lobbyFull';
  if (row.in_progress === false) return 'lobbyRecruiting';
  return 'lobbyUnknown';
}
