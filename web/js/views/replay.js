// One replay in full: every player's numbers against their own norm, plus the
// match curves (economy, damage, devices) and what each side built and lost.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, dateLong, time, duration, getLanguage } from '../lib/i18n.js';
import { routeHash } from '../lib/router.js';
import { METRICS, formatMetric, deviationTone, deviationText, stampDate } from '../domain/analysis.js';
import { nameOf, loadReplay } from '../app/controller.js';
import { panel, avatar, chip, emptyState, skeleton, button, hint } from '../ui/components.js';
import { seriesChart } from '../ui/charts.js';

function commanderName(state, slug) {
  const names = state.boot.commanderNames || {};
  return names[slug]?.[getLanguage()] || names[slug]?.en || (slug || '').replace(/^commander-[^-]+-/, '').replaceAll('-', ' ');
}

function displayName(state, player) {
  return nameOf(player.steam_id, state) || player.name || player.names?.[0]?.name || t('player');
}

export function formBadge(form) {
  if (!form || form === 'usual') return form === 'usual' ? h('span', { class: 'form-badge', 'data-form': 'usual' }, t('formUsual')) : null;
  return h('span', { class: 'form-badge', 'data-form': form, title: t(form === 'peak' ? 'formPeakHint' : 'formOffHint') },
    icon(form === 'peak' ? 'trendUp' : 'trendDown', 'icon-sm'), t(form === 'peak' ? 'formPeak' : 'formOff'));
}

function metricCell(metric, player) {
  const value = player.metrics?.[metric.id];
  const deviation = player.deviation?.[metric.id];
  const norm = player.norm?.[metric.id];
  return h('td', { class: 'num-col metric-cell', title: norm ? t('normValue', { value: formatMetric(metric.kind, norm.mean), count: norm.n }) : t('normMissing') },
    h('span', { class: 'num' }, formatMetric(metric.kind, value)),
    deviation ? h('small', { class: ['metric-dev', 'tone-' + deviationTone(deviation)] }, deviationText(deviation)) : null);
}

function playersTable(state, data) {
  return panel({
    title: t('playersAnalysis'), caption: t('playersAnalysisCaption'), className: 'analysis-players',
    actions: hint(t('normHint'), t('aboutData'), { end: true }),
    children: h('div', { class: 'table-scroll' }, h('table', { class: 'data-table analysis-table' },
      h('thead', null, h('tr', null,
        h('th', { scope: 'col' }, t('player')),
        METRICS.map(metric => h('th', { scope: 'col', class: 'num-col', key: metric.id, title: t('metricHint_' + metric.id) }, t('metric_' + metric.id))),
        h('th', { scope: 'col' }, t('formCol')))),
      [1, 2].map(side => h('tbody', { key: 'side' + side, class: 'side-group', 'data-side': side },
        h('tr', { class: 'side-row' }, h('th', { colspan: String(METRICS.length + 2), scope: 'colgroup' },
          h('span', { class: 'side-label', 'data-side': side }, t('sideN', { n: side })),
          data.winner_side === side ? chip(t('winner'), { tone: 'win', icon: 'trophy' }) : null,
          data.commanders?.[side] ? h('span', { class: 'muted' }, commanderName(state, data.commanders[side])) : null)),
        data.players.filter(player => player.side === side).map(player => h('tr', { key: player.steam_id || player.name },
          h('td', null, h('span', { class: 'cell-player' },
            avatar({ name: displayName(state, player), src: state.profiles[player.steam_id]?.avatar, size: 'sm' }),
            player.steam_id
              ? h('a', { class: 'cell-link ellipsis', href: routeHash('player', player.steam_id) }, displayName(state, player))
              : h('span', { class: 'ellipsis' }, displayName(state, player)))),
          METRICS.map(metric => metricCell(metric, player)),
          h('td', null, formBadge(player.form)))))))),
  });
}

const whole = value => number(Math.round(value));

function charts(data) {
  const frames = data.series_frames || [];
  const bySide = side => data.teams.filter(team => team.side === side);
  const sum = (side, key) => {
    const teams = bySide(side);
    if (!teams.length) return [];
    return frames.map((_, i) => teams.reduce((total, team) => total + (team.series?.[key]?.[i] || 0), 0));
  };
  const chart = (key, titleKey, format) => h('div', { class: 'analysis-chart', key },
    h('h3', { class: 'caps' }, t(titleKey)),
    seriesChart([
      { label: t('sideN', { n: 1 }), values: sum(1, key), tone: 'team-1' },
      { label: t('sideN', { n: 2 }), values: sum(2, key), tone: 'team-2' },
    ], frames, { label: t(titleKey), format, endLabel: minutes => t('minutesShort', { n: Math.round(minutes) }) }));
  return panel({
    title: t('matchCurves'), caption: t('everyFiveSeconds'), className: 'analysis-curves',
    children: h('div', { class: 'analysis-charts' },
      chart('metal', 'chartMetal', whole), chart('energy', 'chartEnergy', whole),
      chart('floated_metal', 'chartFloatedMetal', whole), chart('damage_dealt', 'chartDamage', whole),
      chart('devices', 'chartDevices', whole), chart('reactor_health', 'chartReactor', value => Math.round(value) + '%')),
  });
}

function teamFacts(data) {
  const label = (items) => items?.length ? items.map(([name, count]) => `${name} ×${count}`).join(', ') : '—';
  return panel({
    title: t('builtAndLost'), className: 'analysis-teams',
    children: h('div', { class: 'team-facts' }, data.teams.map(team => h('div', { class: 'team-fact', key: team.team, 'data-side': team.side },
      h('h3', null, h('span', { class: 'side-label', 'data-side': team.side }, t('sideN', { n: team.side })),
        h('span', { class: 'muted num' }, t('teamN', { n: team.team }))),
      h('dl', null,
        h('div', null, h('dt', null, t('devicesBuilt')), h('dd', { class: 'num' }, `${team.devices_built} (${t('weaponsN', { n: team.weapons_built })})`)),
        h('div', null, h('dt', null, t('devicesLost')), h('dd', { class: 'num' }, `${team.devices_lost} (${t('weaponsN', { n: team.weapons_lost })})`)),
        h('div', null, h('dt', null, t('floatedTotal')), h('dd', { class: 'num' }, `${number(team.floated_metal)} / ${number(team.floated_energy)}`)),
        h('div', null, h('dt', null, t('strutsLost')), h('dd', { class: 'num' }, number(team.struts_lost))),
        h('div', null, h('dt', null, t('damageDealt')), h('dd', { class: 'num' }, number(team.damage))),
        h('div', null, h('dt', null, t('firstWeapon')), h('dd', { class: 'num' }, formatMetric('time', team.first_weapon)))),
      h('p', { class: 'team-list' }, h('strong', null, t('mostBuilt') + ': '), label(team.top_builds)),
      h('p', { class: 'team-list' }, h('strong', null, t('mostLost') + ': '), label(team.top_losses))))),
  });
}

export function replayHeadline(state) {
  const entry = state.replays[state.route.id];
  if (!entry?.data) return '';
  const { map, mode } = entry.data;
  return map && mode && map.includes(mode) ? map : [map, mode].filter(Boolean).join(' · ');
}

export function replayView(state, ctx) {
  const id = state.route.id;
  const entry = state.replays[id];
  if (!entry || entry.loading) return h('div', { class: 'panel' }, skeleton(5));
  if (entry.error) {
    return emptyState({
      icon: 'activity', title: t(entry.error === 'pending' ? 'analysisPendingTitle' : 'analysisFailedTitle'),
      text: entry.error === 'pending' ? t('analysisPendingText', { count: state.app.players_status?.pending || 0 }) : entry.error,
      actions: [button({ label: t('refresh'), icon: 'refresh', onClick: () => loadReplay(id, true) })],
    });
  }
  const data = entry.data;
  const date = stampDate(data.played);
  return h('div', { class: 'analysis' },
    h('header', { class: 'panel analysis-hero' },
      h('div', null,
        h('span', { class: 'caps' }, [data.ranked ? t('rankedGame') : t('lobbyGame'), data.mode, data.season ? t('seasonN', { n: data.season }) : null].filter(Boolean).join(' · ')),
        h('h2', null, data.map || 'Forts'),
        h('p', { class: 'muted' }, [date ? dateLong(date) + ' · ' + time(date) : null, t('lengthValue', { value: duration(Math.round(data.duration)) })].filter(Boolean).join(' · '))),
      h('div', { class: 'analysis-hero-side' },
        data.winner_side ? chip(t('sideWon', { n: data.winner_side }), { tone: 'accent', icon: 'trophy' }) : chip(t('resultUnknown')),
        data.truncated ? chip(t('truncated'), { tone: 'loss' }) : null,
        button({ label: t('back'), icon: 'arrowLeft', variant: 'ghost', size: 'sm', onClick: () => history.back() }))),
    playersTable(state, data),
    charts(data),
    teamFacts(data),
    h('p', { class: 'analysis-credit muted' }, icon('info', 'icon-sm'), t('analysisCredit')));
}
