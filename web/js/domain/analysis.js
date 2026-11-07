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
