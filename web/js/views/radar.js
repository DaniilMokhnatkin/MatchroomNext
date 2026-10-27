// Ranked radar: who among strong players shows confirmed activity.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, signed, time } from '../lib/i18n.js';
import { store, ownerId, isDemo, refreshRadar, setRadarEnabled, toast } from '../app/controller.js';
import { panel, chip, segmented, searchField, button, emptyState, hint, externalLink } from '../ui/components.js';
import { toolbar } from './common.js';

const TIERS = ['', 'fpl', 'top20', 'top80', 'current'];
const EVIDENCE_ORDER = { queue: 0, recent: 1, playing: 2, unknown: 3 };
const EVIDENCE_TONE = { queue: 'loss', recent: 'accent', playing: 'win', unknown: 'neutral' };

const clock = value => value ? time(new Date(value * 1000)) : '—';

function isPaused(radar) { return ['paused', 'disabled'].includes(radar.status); }

export function tierText(tier) {
  return t({ fpl: 'tierFpl', top20: 'tierTop20', top80: 'tierTop80', current: 'tierCurrent' }[tier] || 'tierCurrent');
}

function evidenceText(evidence) {
  return t({ queue: 'evidenceQueue', recent: 'evidenceRecent', playing: 'evidencePlaying' }[evidence] || 'evidenceUnknown');
}

/** Headline used by the radar page and the overview "live" panel. */
export function radarHeadline(state) {
  const radar = state.radar;
  const paused = isPaused(radar);
  const level = paused ? 'unknown' : radar.level || 'unknown';
  const active = (radar.players || []).filter(p => p.steam_id !== ownerId(state) && p.evidence && p.evidence !== 'unknown');
  let title, hintText;
  if (radar.status === 'disabled') { title = t('radarDisabled'); hintText = t('radarDisabledHint'); }
  else if (radar.status === 'paused') { title = t('radarPaused'); hintText = t('radarPausedHint'); }
  else if (radar.status === 'checking' && level === 'unknown') { title = t('radarChecking'); hintText = t('radarCheckingHint'); }
  else {
    title = t({ queue: 'radarQueue', recent: 'radarRecent', playing: 'radarPlaying', unknown: 'radarQuiet' }[level]);
    hintText = t({ queue: 'radarQueueHint', recent: 'radarRecentHint', playing: 'radarPlayingHint', unknown: 'radarQuietHint' }[level]);
  }
  return {
    title, hint: hintText, level, paused,
    tone: paused ? 'neutral' : { queue: 'loss', recent: 'accent', playing: 'win', unknown: 'neutral' }[level],
    names: !paused && active.length ? active.slice(0, 4).map(p => p.name || p.steam_id).join(', ') : '',
    active,
  };
}

function playerLink(state, player) {
  return externalLink({
    href: 'https://steamcommunity.com/profiles/' + encodeURIComponent(player.steam_id),
    label: player.name || player.steam_id,
    demo: isDemo(state), onDemo: () => toast(t('demoUnsupported')),
  });
}

function statusPanel(state) {
  const radar = state.radar;
  const head = radarHeadline(state);
  const unavailable = radar.status === 'partial' || radar.status === 'unavailable';
  return h('section', { class: 'status-panel panel', 'data-tone': head.tone },
    h('span', { class: 'status-beacon', 'aria-hidden': 'true' }, icon('radar', 'icon-lg')),
    h('div', { class: 'status-text', 'aria-live': 'polite' },
      h('h2', null, head.title),
      h('p', null, head.hint),
      head.names ? h('p', { class: 'status-names' }, icon('activity', 'icon-sm'), head.names) : null),
    h('dl', { class: 'status-meta' },
      h('div', null, h('dt', { class: 'caps' }, t('watched')), h('dd', { class: 'num' }, number((radar.players || []).length))),
      h('div', null, h('dt', { class: 'caps' }, t('season')), h('dd', { class: 'num' }, radar.season || state.app.active_season || '—')),
      h('div', null, h('dt', { class: 'caps' }, t('checked')), h('dd', { class: 'num' }, clock(radar.checked_at))),
      unavailable ? h('div', null, h('dt', { class: 'caps' }, 'Steam'), h('dd', { class: 'tone-loss' }, t('steamUnavailable'))) : null));
}

function rosterTable(state, players, paused) {
  const own = ownerId(state);
  const ui = state.radarUi;
  const visible = players.slice(0, ui.limit);
  if (!players.length) return emptyState({ icon: 'search', title: t('radarNoPlayers'), text: t('radarNoPlayersText') });
  return [
    h('table', { class: 'data-table radar-table' },
      h('thead', null, h('tr', null,
        h('th', { scope: 'col' }, t('player')),
        h('th', { scope: 'col' }, t('tier')),
        h('th', { scope: 'col', class: 'num-col' }, t('place')),
        h('th', { scope: 'col', class: 'num-col' }, t('score')),
        h('th', { scope: 'col' }, t('signal')))),
      h('tbody', null, visible.map(player => {
        const evidence = paused ? 'unknown' : player.evidence || 'unknown';
        return h('tr', { key: player.steam_id, 'data-evidence': evidence },
          h('td', null, h('span', { class: 'cell-player' }, playerLink(state, player), player.steam_id === own ? chip(t('roleYou'), { tone: 'accent' }) : null)),
          h('td', null, h('span', { class: 'muted' }, tierText(player.tier || 'current'))),
          h('td', { class: 'num-col num' }, player.rank ? '№ ' + number(player.rank) : '—'),
          h('td', { class: 'num-col num muted' }, player.score !== null && player.score !== undefined ? number(player.score) : '—'),
          h('td', null, evidence === 'unknown' ? h('span', { class: 'dim' }, '—') : chip(evidenceText(evidence), { tone: EVIDENCE_TONE[evidence], dot: true })));
      }))),
    players.length > ui.limit ? button({
      label: t('showMore', { count: Math.min(60, players.length - ui.limit) }), variant: 'ghost', size: 'sm', className: 'list-more',
      onClick: () => store.merge('radarUi', { limit: ui.limit + 60 }),
    }) : null,
  ];
}

export function radarView(state) {
  const radar = state.radar;
  const ui = state.radarUi;
  const paused = isPaused(radar);
  const players = radar.players || [];
  const query = ui.query.trim().toLowerCase();
  const filtered = players
    .filter(p => (!ui.tier || p.tier === ui.tier) && (!query || (p.name + ' ' + p.steam_id).toLowerCase().includes(query)))
    .sort((a, b) => (EVIDENCE_ORDER[a.evidence] ?? 3) - (EVIDENCE_ORDER[b.evidence] ?? 3) || (a.rank || 99999) - (b.rank || 99999));
  const tiers = TIERS
    .map(tier => ({ id: tier, label: tier ? tierText(tier) : t('allPlayers'), count: tier ? players.filter(p => p.tier === tier).length : players.length }))
    .filter(item => item.id !== 'current' || item.count);
  const byId = new Map(players.map(p => [p.steam_id, p]));

  return h('div', { class: 'radar' },
    toolbar(
      [h('span', { class: 'toolbar-note muted' }, t('radarIntro')), hint(t('radarExplanation'), t('howRadarWorks'))],
      [
        h('label', { class: 'radar-switch' },
          h('span', null, t('radarSwitch')),
          h('input', { type: 'checkbox', role: 'switch', class: 'switch', checked: radar.enabled !== false, 'aria-label': t('radarSwitch'), onChange: event => setRadarEnabled(event.target.checked) })),
        button({ label: ui.refreshing ? t('updating') : t('refresh'), icon: 'refresh', busy: ui.refreshing, disabled: radar.enabled === false, onClick: refreshRadar }),
      ]),
    statusPanel(state),
    h('div', { class: 'radar-grid' },
      panel({
        title: t('watchedPlayers'), className: 'roster-panel',
        actions: searchField({ value: ui.query, placeholder: t('findPlayer'), onInput: value => store.merge('radarUi', { query: value, limit: 60 }) }),
        children: [
          segmented({ items: tiers, value: ui.tier, label: t('tier'), size: 'sm', onChange: tier => store.merge('radarUi', { tier, limit: 60 }) }),
          rosterTable(state, filtered, paused),
        ],
      }),
      h('div', { class: 'radar-side' },
        panel({
          title: t('scoreChanges'),
          children: (radar.events || []).length ? h('ul', { class: 'feed' }, radar.events.slice(0, 15).map(event => h('li', { class: 'feed-row', key: event.steam_id + ':' + event.detected_at },
            h('time', { class: 'num muted' }, clock(event.detected_at)),
            h('span', { class: 'ellipsis' }, byId.get(event.steam_id)?.name || event.steam_id),
            h('span', { class: ['num', event.delta > 0 ? 'tone-win' : event.delta < 0 ? 'tone-loss' : 'tone-neutral'] }, signed(event.delta)))))
            : h('p', { class: 'panel-empty' }, t('radarNoEvents')),
        }),
        panel({
          title: t('currentTop'),
          children: (radar.top || []).length ? h('ol', { class: 'top-list' }, radar.top.map(row => {
            const player = { ...byId.get(row.steam_id), ...row };
            return h('li', { class: 'top-row', key: row.steam_id },
              h('span', { class: 'num top-rank' }, player.rank || '—'),
              h('span', { class: 'ellipsis' }, playerLink(state, player)),
              h('span', { class: 'num muted' }, player.score !== null && player.score !== undefined ? number(player.score) : '—'));
          })) : h('p', { class: 'panel-empty' }, t('radarNoTop')),
        }))));
}
