// Translation and locale-aware formatting.
// Dictionary values are strings with {placeholders} or plural objects keyed
// by Intl.PluralRules categories (ru: one/few/many, en: one/other).
import ru from '../i18n/ru.js';
import en from '../i18n/en.js';

const dictionaries = { ru, en };
let language = 'ru';
let plurals = new Intl.PluralRules('ru-RU');

export function setLanguage(value) {
  language = value === 'en' ? 'en' : 'ru';
  plurals = new Intl.PluralRules(locale());
  if (typeof document !== 'undefined') document.documentElement.lang = language;
}

export function getLanguage() { return language; }
export function locale() { return language === 'en' ? 'en-US' : 'ru-RU'; }

function fill(template, params) {
  return template.replace(/\{(\w+)\}/g, (whole, name) => {
    if (!params || !(name in params)) return whole;
    const value = params[name];
    return typeof value === 'number' ? number(value) : String(value);
  });
}

/** Translate `key`; `params.count` selects the plural form. */
export function t(key, params) {
  const entry = dictionaries[language][key] ?? dictionaries.ru[key];
  if (entry === undefined) return key;
  if (typeof entry === 'string') return fill(entry, params);
  const count = Number(params?.count ?? 0);
  const form = entry[plurals.select(count)] ?? entry.other ?? entry.many ?? entry.one;
  return fill(form, params);
}

export function has(key) { return key in dictionaries[language]; }

export function number(value, options) {
  return Number(value).toLocaleString(locale(), options);
}

/** Signed number with a true minus sign: +26, −18, 0. */
export function signed(value) {
  const n = Number(value);
  return (n > 0 ? '+' : n < 0 ? '−' : '') + number(Math.abs(n));
}

export function percent(value, digits = 0) {
  return Number(value).toLocaleString(locale(), { style: 'percent', maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function dateLong(date) {
  return date ? date.toLocaleDateString(locale(), { day: 'numeric', month: 'long', year: 'numeric' }) : '—';
}

export function dateShort(date) {
  return date ? date.toLocaleDateString(locale(), { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—';
}

export function dateCompact(date) {
  return date ? date.toLocaleDateString(locale(), { day: 'numeric', month: 'short' }) : '—';
}

export function time(date, seconds = false) {
  return date ? date.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) }) : '—';
}

export function dateTime(date) {
  return date ? date.toLocaleString(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
}

export function dayLabel(date, now = new Date()) {
  if (!date) return '—';
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === now.toDateString()) return t('today');
  if (date.toDateString() === yesterday.toDateString()) return t('yesterday');
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'long', ...(sameYear ? {} : { year: 'numeric' }) });
}

/** Compact duration: 45 с / 12 мин / 3 ч. */
export function duration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 60) return t('durationSec', { count: Math.floor(value) });
  if (value < 3600) return t('durationMin', { count: Math.floor(value / 60) });
  return t('durationHour', { count: Math.floor(value / 3600) });
}

/** Relative time for "updated 2 min ago" style labels. */
export function ago(date, now = Date.now()) {
  if (!date) return '—';
  const seconds = Math.round((now - date.getTime()) / 1000);
  if (seconds < 10) return t('justNow');
  return t('agoTemplate', { value: duration(seconds) });
}
