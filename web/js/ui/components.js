// Shared UI building blocks. Every screen is assembled from these so that
// spacing, states and keyboard behaviour stay identical across the app.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';

export function button({ label, icon: iconName, variant = 'secondary', size = 'md', onClick, disabled = false, title, type = 'button', busy = false, kbd: shortcut, className = '', ...rest }) {
  return h('button', {
    type, class: ['btn', 'btn-' + variant, 'btn-' + size, { 'is-busy': busy }, className],
    disabled: disabled || busy, title, onClick, 'aria-busy': busy ? 'true' : null, ...rest,
  },
  iconName ? icon(iconName, busy && iconName === 'refresh' ? 'spin' : '') : null,
  label ? h('span', null, label) : null,
  shortcut ? kbd(shortcut) : null);
}

export function iconButton({ icon: iconName, label, onClick, active = false, disabled = false, tip = true, tipSide, className = '', busy = false, ...rest }) {
  return h('button', {
    type: 'button', class: ['icon-btn', { 'is-active': active }, className], 'aria-label': label,
    'data-tip': tip ? label : null, 'data-tip-side': tipSide, disabled: disabled || busy, onClick,
    'aria-pressed': active ? 'true' : null, ...rest,
  }, icon(iconName, busy ? 'spin' : ''));
}

export function kbd(value) {
  return h('kbd', { class: 'kbd' }, value);
}

export function chip(label, { tone = 'neutral', icon: iconName, dot = false, title, className = '' } = {}) {
  return h('span', { class: ['chip', 'chip-' + tone, className], title },
    dot ? h('span', { class: 'chip-dot', 'aria-hidden': 'true' }) : null,
    iconName ? icon(iconName, 'icon-sm') : null,
    label);
}

export function initials(name) {
  const clean = String(name || '?').trim();
  return (clean.match(/[\p{L}\p{N}]/gu) || ['?']).slice(0, 2).join('').toUpperCase();
}

const brokenImages = new Set();

/** Decorative image that disappears for good once its URL fails to load. */
export function picture({ src, className = '', alt = '' }) {
  if (!src || brokenImages.has(src)) return null;
  return h('img', { class: className, src, alt, onError() { brokenImages.add(src); this.remove(); } });
}

export function avatar({ name, src, size = 'md', tone = '' }) {
  const usable = src && !brokenImages.has(src);
  return h('span', { class: ['avatar', 'avatar-' + size, tone ? 'avatar-' + tone : ''], 'aria-hidden': 'true' },
    usable ? h('img', {
      src, alt: '', referrerpolicy: 'no-referrer', loading: 'lazy',
      onError() { brokenImages.add(src); this.remove(); },
    }) : null,
    h('span', { class: 'avatar-initials' }, initials(name)));
}

export function segmented({ items, value, onChange, label, size = 'md' }) {
  return h('div', { class: ['segmented', 'segmented-' + size], role: 'radiogroup', 'aria-label': label },
    items.map(item => h('button', {
      type: 'button', role: 'radio', key: item.id, class: { 'segment': true, 'is-active': item.id === value },
      'aria-checked': String(item.id === value), tabindex: item.id === value ? '0' : '-1',
      onClick: () => onChange(item.id),
      onKeydown(event) {
        const index = items.findIndex(i => i.id === value);
        const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        const next = items[(index + step + items.length) % items.length];
        onChange(next.id);
        requestAnimationFrame(() => this.parentElement?.querySelector('[aria-checked="true"]')?.focus());
      },
    }, item.label, item.count !== undefined && item.count !== null ? h('span', { class: 'segment-count num' }, item.count) : null)));
}

export function select({ value, options, onChange, label, className = '' }) {
  return h('label', { class: ['select', className] },
    h('span', { class: 'sr-only' }, label),
    h('select', { value, 'aria-label': label, onChange: event => onChange(event.target.value) },
      options.map(option => h('option', { value: option.value }, option.label))),
    icon('chevronDown', 'icon-sm select-caret'));
}

export function toggle({ checked, onChange, label, hint, disabled = false, id }) {
  return h('label', { class: ['toggle-row', { 'is-disabled': disabled }] },
    h('span', { class: 'toggle-text' }, h('strong', null, label), hint ? h('small', null, hint) : null),
    h('input', { type: 'checkbox', role: 'switch', class: 'switch', id, checked, disabled, onChange: event => onChange(event.target.checked) }));
}

export function checkChip({ checked, onChange, label, icon: iconName }) {
  return h('label', { class: ['check-chip', { 'is-on': checked }] },
    h('input', { type: 'checkbox', class: 'sr-only', checked, onChange: event => onChange(event.target.checked) }),
    iconName ? icon(iconName, 'icon-sm') : null,
    h('span', null, label));
}

export function searchField({ value, placeholder, onInput, label, id, shortcut }) {
  return h('label', { class: 'search-field' },
    icon('search', 'icon-sm'),
    h('input', {
      id, type: 'search', value, placeholder, 'aria-label': label || placeholder, autocomplete: 'off', spellcheck: 'false',
      onInput: event => onInput(event.target.value),
      onKeydown(event) { if (event.key === 'Escape' && this.value) { event.stopPropagation(); onInput(''); } },
    }),
    shortcut ? kbd(shortcut) : null);
}

export function panel({ title, caption, actions, children, className = '', tag = 'section', key, ...rest }) {
  return h(tag, { class: ['panel', className], key, ...rest },
    title || actions ? h('header', { class: 'panel-head' },
      h('div', { class: 'panel-title' }, h('h2', { class: 'caps' }, title), caption ? h('span', { class: 'panel-caption' }, caption) : null),
      actions ? h('div', { class: 'panel-actions' }, actions) : null) : null,
    h('div', { class: 'panel-body' }, children));
}

export function stat({ label, value, sub, tone = '', title }) {
  return h('div', { class: 'stat', title },
    h('span', { class: 'caps stat-label' }, label),
    h('strong', { class: ['stat-value', 'num', tone ? 'tone-' + tone : ''] }, value),
    sub ? h('span', { class: 'stat-sub' }, sub) : null);
}

export function callout({ tone = 'info', title, text, action, icon: iconName }) {
  const glyph = iconName || (tone === 'error' ? 'alert' : tone === 'warning' ? 'alert' : 'info');
  return h('div', { class: ['callout', 'callout-' + tone], role: tone === 'error' ? 'alert' : null },
    icon(glyph),
    h('div', { class: 'callout-text' }, title ? h('strong', null, title) : null, text ? h('p', null, text) : null),
    action || null);
}

export function emptyState({ icon: iconName = 'info', title, text, actions }) {
  return h('div', { class: 'empty' },
    h('span', { class: 'empty-icon' }, icon(iconName, 'icon-lg')),
    h('h3', null, title),
    text ? h('p', null, text) : null,
    actions ? h('div', { class: 'empty-actions' }, actions) : null);
}

/** Click-to-open explanation; replaces long always-visible disclaimers. */
export function hint(text, label, { end = false } = {}) {
  return h('details', { class: ['hint', { 'hint-end': end }], 'data-keep-open': '' },
    h('summary', { 'aria-label': label, 'data-tip': label }, icon('help', 'icon-sm')),
    h('div', { class: 'hint-pop', role: 'note' }, text));
}

export function skeleton(lines = 3) {
  return h('div', { class: 'skeleton', 'aria-hidden': 'true' },
    Array.from({ length: lines }, (_, index) => h('span', { class: 'skeleton-line', style: { '--w': (92 - index * 17) + '%' } })));
}

export function meter(fraction, tone = 'accent') {
  const value = Math.max(0, Math.min(1, Number(fraction) || 0));
  return h('span', { class: ['meter', 'meter-' + tone], 'aria-hidden': 'true' },
    h('span', { class: 'meter-fill', style: { '--value': String(value) } }));
}

export function externalLink({ href, label, demo, onDemo }) {
  if (demo) {
    return h('button', { type: 'button', class: 'link', onClick: onDemo }, label, icon('external', 'icon-sm'));
  }
  return h('a', { class: 'link', href, target: '_blank', rel: 'noopener noreferrer' }, label, icon('external', 'icon-sm'));
}
