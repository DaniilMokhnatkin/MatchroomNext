// Live ranked match: phase order, points at stake and the you-vs-opponent rows.
// Pure functions; the view only formats what these return.

export const PHASES = ['queue', 'select', 'loading', 'playing', 'finished'];

/** Points the game books: round(32 x E) is taken at start, 32 back for a win. */
export function stakes(chance) {
  if (typeof chance !== 'number' || !Number.isFinite(chance)) return null;
  const loss = Math.round(32 * chance);
  return { win: 32 - loss, loss };
}

export function phaseIndex(phase) {
  return PHASES.indexOf(phase);
}

const num = value => (typeof value === 'number' && Number.isFinite(value) ? value : null);

function rate(wins, losses) {
  const w = num(wins), l = num(losses);
  return w !== null && l !== null && w + l > 0 ? w / (w + l) : null;
}

function formRate(form) {
  return Array.isArray(form) && form.length ? form.filter(Boolean).length / form.length : null;
}

/**
 * Comparison rows. `better` says which direction wins: 'high' or 'low'.
 * A row is kept only when at least one side has a value.
 */
export function compareRows(own, other, board = {}) {
  const a = own || {}, b = other || {};
  const ea = a.efficiency || {}, eb = b.efficiency || {};
  const rows = [
    { id: 'internal', kind: 'int', better: 'high', a: num(a.internal_rating), b: num(b.internal_rating) },
    { id: 'score', kind: 'int', better: 'high', a: num(board.a?.score ?? a.score), b: num(board.b?.score ?? b.score) },
    { id: 'performance', kind: 'int', better: 'high', a: num(ea.performance), b: num(eb.performance) },
    { id: 'overExpected', kind: 'signed', better: 'high', a: num(ea.over_expected), b: num(eb.over_expected) },
    { id: 'seasonRate', kind: 'rate', better: 'high', a: rate(a.wins, a.losses), b: rate(b.wins, b.losses),
      noteA: num(a.wins) !== null ? `${a.wins}–${a.losses}` : null, noteB: num(b.wins) !== null ? `${b.wins}–${b.losses}` : null },
    { id: 'careerRate', kind: 'rate', better: 'high', a: rate(a.career?.wins, a.career?.losses), b: rate(b.career?.wins, b.career?.losses) },
    { id: 'formRate', kind: 'rate', better: 'high', a: formRate(a.form), b: formRate(b.form) },
    { id: 'vsStronger', kind: 'rate', better: 'high', a: rate(ea.vs_stronger?.wins, (ea.vs_stronger?.games ?? 0) - (ea.vs_stronger?.wins ?? 0)),
      b: rate(eb.vs_stronger?.wins, (eb.vs_stronger?.games ?? 0) - (eb.vs_stronger?.wins ?? 0)) },
    { id: 'slips', kind: 'int', better: 'low', a: num(ea.slips), b: num(eb.slips) },
    { id: 'topPct', kind: 'pct', better: 'low', a: num(a.top_pct), b: num(b.top_pct) },
  ];
  return rows.filter(row => row.a !== null || row.b !== null).map(row => {
    let leader = null;
    if (row.a !== null && row.b !== null && row.a !== row.b) {
      leader = (row.better === 'high') === (row.a > row.b) ? 'a' : 'b';
    }
    // Share of the bar for side A; negative-capable values are shifted to stay readable.
    let share = 0.5;
    if (row.a !== null && row.b !== null) {
      let x = row.a, y = row.b;
      if (row.better === 'low') { x = 1 / Math.max(0.01, x + 0.5); y = 1 / Math.max(0.01, y + 0.5); }
      const floor = Math.min(x, y, 0);
      x -= floor; y -= floor;
      share = x + y > 0 ? x / (x + y) : 0.5;
    }
    return { ...row, leader, share };
  });
}

/** Overall edge: how many comparable rows each side leads. */
export function edge(rows) {
  const a = rows.filter(r => r.leader === 'a').length;
  const b = rows.filter(r => r.leader === 'b').length;
  return { a, b, total: rows.filter(r => r.a !== null && r.b !== null).length };
}

/** The opponent's record on the map of this match, if they ever played it. */
export function mapRecord(summary, map) {
  if (!summary || !map) return null;
  const clean = value => String(value || '').toLowerCase().replace(/\s*1v1$/, '').trim();
  const row = (summary.maps || []).find(item => clean(item.map) === clean(map));
  return row ? { wins: row.wins ?? 0, losses: row.losses ?? 0 } : null;
}
