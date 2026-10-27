// Lightweight charts: SVG for the shapes, HTML for text so labels never
// stretch with the container. Hover state lives on the element itself and
// the data-sig attribute keeps background re-renders from resetting it.
import { h, s } from '../lib/dom.js';
import { number, signed, dateCompact, time } from '../lib/i18n.js';

let gradientSeq = 0;

/** Rating progression; `points` are chronological {id, date, score, delta}. */
export function ratingChart(points, { onSelect, label, sig = '' } = {}) {
  const W = 1000, H = 300;
  const scores = points.map(p => p.score);
  let lo = Math.min(...scores), hi = Math.max(...scores);
  const pad = Math.max(12, (hi - lo) * 0.12);
  lo -= pad; hi += pad;
  const x = i => points.length === 1 ? W / 2 : (i / (points.length - 1)) * W;
  const y = v => H - ((v - lo) / (hi - lo)) * H;
  const coords = points.map((p, i) => [x(i), y(p.score)]);
  const line = coords.map(([cx, cy], i) => (i ? 'L' : 'M') + cx.toFixed(1) + ',' + cy.toFixed(1)).join(' ');
  const area = line + ` L${coords.at(-1)[0].toFixed(1)},${H} L${coords[0][0].toFixed(1)},${H} Z`;
  const gradient = 'rating-fill-' + (++gradientSeq);

  const marker = h('span', { class: 'chart-marker' });
  const rule = h('span', { class: 'chart-rule' });
  const tip = h('div', { class: 'chart-tip', role: 'status' });

  let selected = points.length - 1;
  const show = index => {
    selected = Math.max(0, Math.min(points.length - 1, index));
    const point = points[selected];
    const left = (coords[selected][0] / W) * 100;
    const top = (coords[selected][1] / H) * 100;
    marker.style.setProperty('--x', left + '%');
    marker.style.setProperty('--y', top + '%');
    rule.style.setProperty('--x', left + '%');
    tip.style.setProperty('--x', left + '%');
    tip.classList.toggle('is-right', left > 70);
    tip.replaceChildren(
      h('span', { class: 'chart-tip-date' }, point.date ? dateCompact(point.date) + ' · ' + time(point.date) : '—'),
      h('strong', { class: 'num' }, number(point.score)),
      h('span', { class: ['num', point.delta > 0 ? 'tone-win' : point.delta < 0 ? 'tone-loss' : 'tone-neutral'] }, signed(point.delta)));
  };

  const plot = h('div', {
    class: 'chart-plot', tabindex: '0', role: 'img', 'aria-label': label,
    onPointermove(event) {
      const rect = this.getBoundingClientRect();
      const ratio = (event.clientX - rect.left) / rect.width;
      show(Math.round(ratio * (points.length - 1)));
      this.classList.add('is-hover');
    },
    onPointerleave() { this.classList.remove('is-hover'); show(points.length - 1); },
    onClick() { if (onSelect && points[selected]) onSelect(points[selected].id); },
    onKeydown(event) {
      const moves = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity };
      if (event.key in moves) {
        event.preventDefault();
        const step = moves[event.key];
        show(Number.isFinite(step) ? selected + step : step < 0 ? 0 : points.length - 1);
        this.classList.add('is-hover');
      } else if (event.key === 'Enter' && onSelect) {
        onSelect(points[selected].id);
      }
    },
  },
  s('svg', { class: 'chart-svg', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
    s('defs', null, s('linearGradient', { id: gradient, x1: '0', y1: '0', x2: '0', y2: '1' },
      s('stop', { offset: '0%', 'stop-color': 'var(--accent)', 'stop-opacity': '.28' }),
      s('stop', { offset: '100%', 'stop-color': 'var(--accent)', 'stop-opacity': '0' }))),
    [0.25, 0.5, 0.75].map(f => s('line', { class: 'chart-grid', x1: 0, x2: W, y1: H * f, y2: H * f })),
    s('path', { class: 'chart-area', d: area, fill: `url(#${gradient})` }),
    s('path', { class: 'chart-line', d: line })),
  rule, marker, tip);

  show(selected);

  return h('div', { class: 'chart', 'data-sig': 'rating:' + sig },
    h('div', { class: 'chart-axis chart-axis-y' },
      h('span', { class: 'num' }, number(Math.round(hi - pad))),
      h('span', { class: 'num' }, number(Math.round(lo + pad)))),
    plot,
    h('div', { class: 'chart-axis chart-axis-x' },
      h('span', null, points[0]?.date ? dateCompact(points[0].date) : ''),
      h('span', null, points.at(-1)?.date ? dateCompact(points.at(-1).date) : '')));
}

/** Tiny occupancy line for lobby rows. */
export function sparkline(values, { max, tone = 'accent' } = {}) {
  if (values.length < 2) return h('span', { class: 'sparkline is-empty', 'aria-hidden': 'true' });
  const W = 80, H = 24;
  const top = Math.max(max || 0, ...values, 1);
  const d = values.map((v, i) => (i ? 'L' : 'M') + ((i / (values.length - 1)) * W).toFixed(1) + ',' + (H - 2 - (v / top) * (H - 4)).toFixed(1)).join(' ');
  return s('svg', { class: 'sparkline sparkline-' + tone, viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
    s('path', { d }));
}

/** Larger occupancy history with a keyboard-accessible readout. */
export function occupancyChart(samples, { max, label, format }) {
  const W = 600, H = 120;
  const top = Math.max(max || 0, ...samples.map(p => p.players), 1);
  const first = samples[0].at.getTime(), last = samples.at(-1).at.getTime();
  const coords = samples.map(p => [((p.at.getTime() - first) / Math.max(1, last - first)) * W, H - 6 - (p.players / top) * (H - 12)]);
  const d = coords.map(([cx, cy], i) => (i ? 'L' : 'M') + cx.toFixed(1) + ',' + cy.toFixed(1)).join(' ');
  const marker = h('span', { class: 'chart-marker' });
  const readout = h('p', { class: 'chart-readout num' });
  let selected = samples.length - 1;
  const show = index => {
    selected = Math.max(0, Math.min(samples.length - 1, index));
    marker.style.setProperty('--x', (coords[selected][0] / W) * 100 + '%');
    marker.style.setProperty('--y', (coords[selected][1] / H) * 100 + '%');
    readout.textContent = format(samples[selected]);
  };
  const plot = h('div', {
    class: 'chart-plot chart-plot-sm', tabindex: '0', role: 'img', 'aria-label': label,
    onPointermove(event) {
      const rect = this.getBoundingClientRect();
      const xPos = ((event.clientX - rect.left) / rect.width) * W;
      let best = 0;
      coords.forEach(([cx], i) => { if (Math.abs(cx - xPos) < Math.abs(coords[best][0] - xPos)) best = i; });
      show(best);
    },
    onKeydown(event) {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        show(selected + (event.key === 'ArrowRight' ? 1 : -1));
      }
    },
  },
  s('svg', { class: 'chart-svg', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
    s('line', { class: 'chart-grid', x1: 0, x2: W, y1: H - 6, y2: H - 6 }),
    s('path', { class: 'chart-line chart-line-step', d })),
  marker);
  show(selected);
  return h('div', { class: 'occupancy' }, plot, readout);
}
