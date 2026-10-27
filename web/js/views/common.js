// Formatting helpers shared by several views.
import { h } from '../lib/dom.js';
import { t, signed, number } from '../lib/i18n.js';
import { chip } from '../ui/components.js';

export const RESULT_TONE = { win: 'win', loss: 'loss', draw: 'neutral', unknown: 'neutral' };

export function resultText(result) {
  return t({ win: 'resultWin', loss: 'resultLoss', draw: 'resultDraw', unknown: 'resultUnknown' }[result] || 'resultUnknown');
}

export function resultLetter(result) {
  return t({ win: 'letterWin', loss: 'letterLoss', draw: 'letterDraw', unknown: 'letterUnknown' }[result] || 'letterUnknown');
}

export function resultChip(result) {
  return chip(resultText(result), { tone: RESULT_TONE[result] || 'neutral', dot: true });
}

/** Short delta text for lists: +26, −18, … (pending) or — (no data). */
export function deltaShort(change) {
  if (change.kind === 'confirmed') return signed(change.value);
  if (change.kind === 'pending') return '…';
  return '—';
}

export function deltaTitle(change) {
  if (change.kind === 'confirmed') return t('pointsDelta', { value: signed(change.value), count: Math.abs(change.value) });
  if (change.kind === 'pending') return t('deltaPending');
  if (change.kind === 'other') return t('deltaOtherAccount');
  return t('deltaNone');
}

export function deltaTone(change) {
  if (change.kind !== 'confirmed') return change.kind === 'pending' ? 'accent' : 'neutral';
  return change.value > 0 ? 'win' : change.value < 0 ? 'loss' : 'neutral';
}

export function roleText(role) {
  return t({ you: 'roleYou', recorder: 'roleRecorder', opponent: 'roleOpponent', ally: 'roleAlly', observer: 'roleObserver' }[role] || 'rolePlayer');
}

export function rankText(rank, score) {
  if (!rank) return '—';
  return '№ ' + number(rank) + (score !== null && score !== undefined ? ' · ' + number(score) : '');
}

/** Row of view-level controls placed above the content. */
export function toolbar(left, right) {
  return h('div', { class: 'toolbar' },
    h('div', { class: 'toolbar-group' }, left),
    right ? h('div', { class: 'toolbar-group toolbar-end' }, right) : null);
}
