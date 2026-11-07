// One replay in full: every player's numbers against their own norm, plus the
// match curves (economy, damage, devices) and what each side built and lost.
import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { t, number, dateLong, time, duration, getLanguage } from '../lib/i18n.js';
import { routeHash } from '../lib/router.js';
import { METRICS, formatMetric, deviationTone, deviationText, stampDate, replaySides, replaySeries } from '../domain/analysis.js';
import { nameOf, ownerId, store, loadReplay, loadReplayPlayerCurves } from '../app/controller.js';
import { panel, avatar, chip, emptyState, skeleton, button, hint, select, segmented } from '../ui/components.js';
import { seriesChart } from '../ui/charts.js';

function commanderName(state, slug) {
  const names = state.boot.commanderNames || {};
  return names[slug]?.[getLanguage()] || names[slug]?.en || (slug || '').replace(/^commander-[^-]+-/, '').replaceAll('-', ' ');
}

function displayName(state, player) {
  return nameOf(player.steam_id, state) || player.name || player.names?.[0]?.name || t('player');
}

export function formBadge(form) {
  const labels = { life: 'formLife', above: 'formAbove', usual: 'formUsual', below: 'formBelow', rough: 'formRough' };
  if (!Object.hasOwn(labels, form)) return h('span', { class: 'muted', title: t('normMissing') }, '—');
  return h('span', { class: 'form-badge', 'data-form': form, title: t('formScaleHint') }, t(labels[form]));
}

function metricCell(metric, player) {
  const value = player.metrics?.[metric.id];
  const deviation = player.deviation?.[metric.id];
  const norm = player.norm?.[metric.id];
  return h('td', { class: 'num-col metric-cell', title: norm ? t('normValue', { value: formatMetric(metric.kind, norm.mean), count: norm.n }) : t('normMissing') },
    h('span', { class: 'num' }, formatMetric(metric.kind, value)),
    deviation ? h('small', { class: ['metric-dev', 'tone-' + deviationTone(deviation)] }, deviationText(deviation)) : null);
}

const sideLabel = group => group.role ? t(group.role) : t('sideN', { n: group.side });

function playersTable(state, data, groups) {
  return panel({
    title: t('playersAnalysis'), caption: t('playersAnalysisCaption'), className: 'analysis-players',
    actions: hint(t('normHint') + ' ' + t('formScaleHint'), t('aboutData'), { end: true }),
    children: [h('p', { class: 'replay-metric-note muted' }, t('actionMetricsHint')),
      h('div', { class: 'table-scroll' }, h('table', { class: 'data-table analysis-table' },
      h('thead', null, h('tr', null,
        h('th', { scope: 'col' }, t('player')),
        METRICS.map(metric => h('th', { scope: 'col', class: 'num-col', key: metric.id, title: t('metricHint_' + metric.id) }, t('metric_' + metric.id))),
        h('th', { scope: 'col' }, t('formCol')))),
      groups.map(({ side, role, players }) => h('tbody', { key: 'side' + side, class: 'side-group', 'data-side': side },
        h('tr', { class: 'side-row' }, h('th', { colspan: String(METRICS.length + 2), scope: 'colgroup' },
          h('span', { class: 'side-label', 'data-side': side }, sideLabel({ side, role })),
          data.winner_side === side ? chip(t('winner'), { tone: 'win', icon: 'trophy' }) : null,
          data.commanders?.[side] ? h('span', { class: 'muted' }, commanderName(state, data.commanders[side])) : null)),
        players.map(player => h('tr', { key: player.steam_id || player.name },
          h('td', null, h('span', { class: 'cell-player' },
            avatar({ name: displayName(state, player), src: state.profiles[player.steam_id]?.avatar, size: 'sm' }),
            player.steam_id
              ? h('a', { class: 'cell-link ellipsis', href: routeHash('player', player.steam_id) }, displayName(state, player))
              : h('span', { class: 'ellipsis' }, displayName(state, player)))),
          METRICS.map(metric => metricCell(metric, player)),
          h('td', null, formBadge(player.form))))))))],
  });
}

const numeric = value => Number.isFinite(value) ? number(value, { maximumFractionDigits: 1 }) : '—';
const fortName = fort => fort.team === fort.side ? t('fortUnnumbered')
  : t('teamN', { n: fort.team % 100 === fort.side ? Math.floor(fort.team / 100) : fort.team });
const playerChoices = groups => groups.flatMap(group => group.players.filter(player => player.steam_id));
const FORT_CHARTS = [
  ['metal', 'chartMetal'], ['energy', 'chartEnergy'], ['floated_metal', 'chartFloatedMetal'],
  ['damage_dealt', 'chartDamage'], ['devices', 'chartDevices'], ['reactor_health', 'chartReactor'],
];

function charts(state, entry, groups) {
  const id = state.route.id;
  const { data } = entry;
  const ui = state.replayChartUi[id] || {};
  const mode = ui.mode || 'sides';
  const update = change => store.merge('replayChartUi', current => ({ [id]: { ...current[id], ...change } }));
  const frames = data.series_frames || [];
  const players = playerChoices(groups);
  const forts = groups.flatMap(group => group.forts);
  const player = players.find(p => p.steam_id === ui.player) || players.find(p => p.steam_id === ownerId(state)) || players[0];
  const fort = forts.find(f => String(f.team) === ui.fort) || forts[0];
  const chart = (key, titleKey, lines, scopeKey = '') => {
    const label = t(titleKey);
    const reactor = key === 'reactor_health';
    return h('div', { class: 'analysis-chart', key: mode + ':' + key },
      h('h3', { title: reactor ? t('chartReactorHint') : null }, label),
      seriesChart(lines, frames, {
        label, format: reactor ? value => numeric(value) + '%' : numeric, axisMax: reactor ? 100 : undefined,
        sig: JSON.stringify([id, entry.at, mode, key, scopeKey, getLanguage(), lines.map(line => line.label)]),
      }));
  };
  const tone = side => 'team-' + side;
  const chartModes = [
    { id: 'sides', label: t('chartSides') }, { id: 'players', label: t('chartPlayers') }, { id: 'forts', label: t('chartForts') },
  ];
  const controls = h('div', { class: 'replay-chart-controls' },
    segmented({ items: chartModes, value: mode, label: t('chartScope'), size: 'sm', onChange: next => {
      update({ mode: next });
      if (next === 'players') loadReplayPlayerCurves(id);
    } }),
    mode === 'players' && player ? select({
      label: t('chartPlayerPick'), value: player.steam_id, onChange: value => update({ player: value }),
      options: players.map(p => ({ value: p.steam_id, label: displayName(state, p) })),
    }) : null,
    mode === 'forts' && fort ? select({
      label: t('chartFortPick'), value: String(fort.team), onChange: value => update({ fort: value }),
      options: groups.flatMap(group => group.forts.map(f => ({
        value: String(f.team), label: [sideLabel(group), fortName(f), ...f.players.map(p => displayName(state, p))].join(' · '),
      }))),
    }) : null);
  let content;
  if (mode === 'players') {
    const series = data.player_curves?.[player?.steam_id];
    if (series && Object.values(series).some(values => values.some(Number.isFinite))) {
      content = [['apm', 'metric_apm'], ['damage', 'chartPlayerDamage']].map(([key, title]) =>
        chart(key, title, [{ label: displayName(state, player), tone: tone(player.side), values: series[key] }], player.steam_id));
    } else {
      content = h('p', { class: 'panel-empty', role: 'status' },
        t(entry.curvesState === 'loading' ? 'chartPlayerLoading' : 'chartPlayerMissing'));
    }
  } else if (mode === 'forts') {
    content = fort ? FORT_CHARTS.map(([key, title]) =>
      chart(key, title, [{ label: fortName(fort), tone: tone(fort.side), values: replaySeries([fort], key, frames) }], fort.team)) : null;
  } else {
    content = FORT_CHARTS.map(([key, title]) => chart(key, title, groups.map(group => ({
      label: sideLabel(group), tone: tone(group.side), values: replaySeries(group.forts, key, frames),
    }))));
  }
  return panel({
    title: t('matchCurves'), caption: t('inspectReplayChart'), className: 'analysis-curves',
    children: [controls, Array.isArray(content) ? h('div', { class: 'analysis-charts', 'data-chart-mode': mode }, content) : content],
  });
}

function teamFacts(state, groups) {
  const fact = (key, value, sub = '', title = '') => h('div', null,
    h('dt', { title }, t(key)), h('dd', { class: 'num' }, value, sub ? h('small', { class: 'muted' }, sub) : null));
  const breakdown = (key, items) => h('div', null, h('h5', null, t(key)),
    items?.length ? h('ul', { class: 'fort-build-list' }, items.map(([name, count]) =>
      h('li', { key: name }, h('span', null, name), h('span', { class: 'num muted' }, '×' + number(count))))) : h('span', { class: 'muted' }, '—'));
  return panel({
    title: t('builtAndLost'), caption: t('fortLineup'), className: 'analysis-teams',
    children: h('div', { class: 'team-facts' }, groups.filter(group => group.forts.length).map(group =>
      h('section', { class: 'fort-side', key: group.side, 'data-side': group.side },
        h('h3', null, h('span', { class: 'side-label', 'data-side': group.side }, sideLabel(group)),
          group.role ? h('small', { class: 'muted' }, t('sideN', { n: group.side })) : null),
        group.forts.map(fort => h('article', { class: 'team-fact', key: fort.team, 'data-fort': fort.team },
          h('header', null,
            h('h4', { title: t('fortReplayId', { id: fort.team }) }, fortName(fort)),
            fort.players.length ? h('ul', { class: 'fort-players' }, fort.players.map(p => {
              const name = displayName(state, p);
              return h('li', { key: p.steam_id || p.name },
                p.steam_id ? h('a', { href: routeHash('player', p.steam_id), class: 'fort-player-name' }, name) : h('span', null, name),
                p.steam_id && p.steam_id === ownerId(state) ? chip(t('roleYou'), { tone: 'accent' }) : null);
            })) : h('p', { class: 'muted' }, t('fortLineupMissing'))),
          h('dl', null,
            fact('devicesBuilt', numeric(fort.devices_built), t('weaponsN', { n: fort.weapons_built })),
            fact('devicesLost', numeric(fort.devices_lost), t('weaponsN', { n: fort.weapons_lost })),
            fact('damageDealt', numeric(fort.damage)),
            fact('metalLostTotal', numeric(fort.floated_metal), '', t('storageOverflowHint')),
            fact('energyLostTotal', numeric(fort.floated_energy), '', t('storageOverflowHint')),
            fact('firstWeapon', formatMetric('time', fort.first_weapon))),
          h('details', { class: 'fort-details', 'data-keep-open': '' },
            h('summary', null, t('fortBreakdown')),
            h('div', { class: 'fort-breakdown' },
              breakdown('mostBuilt', fort.top_builds), breakdown('mostLost', fort.top_losses),
              h('p', { class: 'muted' }, t('strutsLost') + ': ', h('strong', { class: 'num' }, numeric(fort.struts_lost)))))))))),
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
  const groups = replaySides(data, ownerId(state));
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
    playersTable(state, data, groups),
    charts(state, entry, groups),
    teamFacts(state, groups),
    h('p', { class: 'analysis-credit muted' }, icon('info', 'icon-sm'), t('analysisCredit')));
}
