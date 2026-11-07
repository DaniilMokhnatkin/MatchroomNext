// Match analysis metrics shared by the replay and dossier screens.

// Order is the column order. `better` drives colours of deviations from a player's norm.
export const METRICS = [
  { id: 'apm', kind: 'int', better: 'high' },
  { id: 'cpm', kind: 'int', better: 'high' },
  { id: 'dpm', kind: 'int', better: 'high' },
  { id: 'damage_ratio', kind: 'ratio', better: 'high' },
  { id: 'floated_metal_pm', kind: 'int', better: 'low' },
  { id: 'floated_energy_pm', kind: 'int', better: 'low' },
  { id: 'devices_lost_pm', kind: 'dec', better: 'low' },
  { id: 'first_weapon', kind: 'time', better: 'low' },
];

export function formatMetric(kind, value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (kind === 'ratio') return value >= 10 ? Math.round(value) + '×' : value.toFixed(value >= 1 ? 1 : 2) + '×';
  if (kind === 'dec') return value.toFixed(1);
  if (kind === 'time') {
    const total = Math.round(value);
    return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
  }
  if (kind === 'pct') return Math.round(value * 100) + '%';
  return Math.round(value).toLocaleString();
}

/** Tone of a deviation: good means better than the player's norm. */
export function deviationTone(deviation) {
  if (!deviation) return 'neutral';
  return deviation.verdict === 'above' ? 'win' : deviation.verdict === 'below' ? 'loss' : 'neutral';
}

export function deviationText(deviation) {
  if (!deviation || deviation.pct === null || deviation.pct === undefined) return '';
  const pct = Math.round(deviation.pct * 100);
  return (pct > 0 ? '+' : pct < 0 ? '−' : '±') + Math.abs(pct) + '%';
}

/** Pick the class (duels or team games) with more analysed games, unless chosen. */
export function dossierScope(dossier, chosen) {
  if (chosen === 'duel' || chosen === 'team') return chosen;
  const duel = (dossier.matches || []).filter(m => m.mode === '1v1').length;
  const team = (dossier.matches || []).length - duel;
  return duel >= team ? 'duel' : 'team';
}

export function scopeMatches(dossier, scope) {
  return (dossier.matches || []).filter(m => (m.mode === '1v1') === (scope === 'duel'));
}

/** Points for the internal rating chart, oldest first. */
export function internalSeries(ratings) {
  return (ratings || []).filter(r => typeof r.internal === 'number')
    .map((r, index) => ({ id: String(index), date: new Date(r.at * 1000), score: r.internal, delta: null }));
}

export function stampDate(seconds) {
  return typeof seconds === 'number' ? new Date(seconds * 1000) : null;
}

/** Keep the player's side and starting fort first, without guessing in spectator replays. */
export function replaySides(data, owner) {
  const own = owner ? data.players.find(player => player.steam_id === owner && [1, 2].includes(player.side)) : null;
  return (own ? [own.side, 3 - own.side] : [1, 2]).map(side => {
    const players = data.players.filter(player => player.side === side);
    const forts = data.teams.filter(team => team.side === side).map(team => ({
      ...team,
      players: players.filter(player => player.starting_team === team.team)
        .sort((a, b) => Number(b === own) - Number(a === own)),
    })).sort((a, b) => Number(b.players.includes(own)) - Number(a.players.includes(own)) || a.team - b.team);
    return { side, role: !own ? null : side === own.side ? 'yourReplaySide' : 'otherReplaySide', players, forts };
  });
}

export function replaySeries(forts, metric, frames) {
  const series = forts.map(fort => fort.series?.[metric] || []).filter(values => values.length);
  const totals = frames.map((_, index) => {
    const samples = series.map(values => values[index]).filter(Number.isFinite);
    return samples.length ? samples.reduce((a, b) => a + b, 0) : null;
  });
  if (metric !== 'reactor_health') return totals;
  // The replay adds the health of every reactor. Its raw value can exceed
  // 100 even within one fort, so use the highest observed total as the scale.
  const peak = Math.max(0, ...totals.filter(Number.isFinite));
  return totals.map(value => peak > 0 && value !== null ? value / peak * 100 : null);
}
