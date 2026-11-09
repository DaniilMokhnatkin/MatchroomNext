// Application state and every action that talks to the local API.
// Views read the store and call these functions; they never fetch directly.
import { createStore } from '../lib/store.js';
import { api } from '../lib/api.js';
import { t, setLanguage, getLanguage } from '../lib/i18n.js';
import { sortMatches, boardKey, pair, validSteamId } from '../domain/matches.js';
import { VIEWS, routeHash } from '../lib/router.js';

const POLL_MS = 1500;
const FULL_SYNC_MS = 30000;
const BOARD_TTL_MS = 60000;

export const store = createStore({
  boot: {},
  route: { view: 'overview', id: null },
  app: {},
  matches: [],
  revision: null,
  loaded: false,
  connection: 'ok',
  profiles: {},
  boards: {},
  busy: {},
  ownerBoard: null,
  encounters: {},
  radar: { status: 'checking', enabled: true, players: [], top: [], events: [] },
  lobbies: { status: 'checking', lobbies: [], favorites: [] },
  matchesUi: { query: '', season: '', result: '', opponent: '', limit: 80, tab: 'summary' },
  overviewUi: { season: null },
  radarUi: { tier: '', query: '', limit: 60, refreshing: false },
  casual: { revision: null, games: [], loaded: false },
  dossiers: {},
  replays: {},
  replayChartUi: {},
  liveUi: { view: 'auto' },
  dossierUi: { scope: 'auto', limit: 40 },
  casualUi: { mode: '', days: 0, limit: 40 },
  lobbyUi: { tab: 'recruiting', query: '', noPassword: false, favoritesOnly: false, sort: 'new', busy: false, expanded: {} },
  ui: { dialog: null, menu: false, toasts: [], dragging: false, palette: { query: '', index: 0 }, importing: 0, accountInput: '', accountError: '' },
  settings: null,
});

let lastFullSync = 0;
let lastStateBody = '';
let polling = false;
let welcomed = false;
let lobbyNoticeSeen = null;
let notificationSeen = null;
let toastSeq = 0;
let ownerRequest = 0;
let casualLoading = false;
let liveSeen = null;

/* ---------- Selectors ---------- */

export const ownerId = (state = store.get()) => state.app.owner_steam_id || '';
export const nameOf = (id, state = store.get()) => (id && state.profiles[id]?.name) || '';
export function liveNameOf(state, live, id) {
  if (!id) return '';
  const own = id === (live.self_id || ownerId(state));
  const intel = live.intel;
  const names = [intel?.steam_names?.[id], nameOf(id, state),
    own ? live.self_name : live.opponent?.name,
    own ? intel?.self?.name : intel?.opponent?.name, intel?.names?.[id]];
  return names.find(name => typeof name === 'string' && name.trim()
    && !/^(?:\[unknown\]|\d{17})$/i.test(name.trim()))?.trim() || '';
}
export const isDemo = (state = store.get()) => !!state.app.demo;

export function selectedMatch(state = store.get()) {
  const { matches, route } = state;
  return matches.find(m => m.match_id === route.id) || matches[0] || null;
}

export function playerName(player, state = store.get()) {
  return nameOf(player?.steam_id, state) || player?.name || t('player');
}

/** Owner rating for the top bar: the newest of the stored snapshot and the live board. */
export function ownerRating(state = store.get()) {
  const id = ownerId(state);
  const board = state.ownerBoard?.leaderboard;
  const row = board?.players?.[id];
  const snapshot = state.app.owner_last_rating;
  const snapshotNewer = snapshot && snapshot.observed_at * 1000 > (Date.parse(board?.checked_at) || 0);
  const rating = snapshotNewer ? snapshot : row || snapshot;
  const checkedAt = rating?.observed_at ? new Date(rating.observed_at * 1000) : board?.checked_at ? new Date(board.checked_at) : null;
  return rating?.rank ? { rank: rating.rank, score: rating.score ?? null, checkedAt } : { rank: null, score: null, checkedAt };
}

/* ---------- Toasts and dialogs ---------- */

export function toast(text, { tone = 'info', action = null, timeout = 5000 } = {}) {
  if (!text) return;
  const id = ++toastSeq;
  store.merge('ui', ui => ({ toasts: [...ui.toasts.slice(-3), { id, text, tone, action }] }));
  if (timeout) setTimeout(() => dismissToast(id), timeout);
}

export function dismissToast(id) {
  store.merge('ui', ui => ({ toasts: ui.toasts.filter(item => item.id !== id) }));
}

export function openDialog(name, extra = {}) {
  store.merge('ui', { dialog: name, menu: false, ...extra });
}

export function closeDialog() {
  store.merge('ui', { dialog: null });
}

export function toggleMenu(open = !store.get().ui.menu) {
  store.merge('ui', { menu: open });
  if (open) loadAccountProfiles();
}

function failure(error, fallbackKey = 'requestFailed') {
  toast(error?.message || t(fallbackKey), { tone: 'error' });
}

/* ---------- State sync ---------- */

export function applyState(next) {
  const before = store.get();
  const oldOwner = ownerId(before);
  const oldSeason = before.app.active_season;
  const oldFirst = before.matches[0]?.match_id;
  const { matches, ranked_radar: radar, lobbies, ...app } = next;
  const patch = { app: { ...before.app, ...app }, revision: next.revision ?? before.revision, loaded: true, connection: 'ok' };

  if (next.language && next.language !== getLanguage()) setLanguage(next.language);

  let newMatch = null;
  if (matches) {
    patch.matches = sortMatches(matches);
    patch.encounters = {};
    const first = patch.matches[0]?.match_id;
    if (oldFirst && first && first !== oldFirst) newMatch = first;
  }
  if (radar) patch.radar = radar;
  const phase = app.live_match?.phase;
  if (phase !== before.app.live_match?.phase && ['select', 'finished'].includes(phase)) {
    patch.liveUi = { view: 'auto' };
  }
  if (lobbies) {
    patch.lobbies = lobbies;
    announceLobbies(lobbies);
  }
  if (app.notifications) followNotification(app.notifications.open);
  store.set(patch);
  if (newMatch) {
    toast(t('newMatch'), { tone: 'accent', timeout: 12000, action: { label: t('open'), href: '#/matches/' + encodeURIComponent(newMatch) } });
  }

  const state = store.get();
  followLive(state);
  if (app.casual_status && app.casual_status.revision !== state.casual.revision) loadCasual();
  if (oldOwner !== ownerId(state) || oldSeason !== state.app.active_season) {
    store.set({ ownerBoard: null });
    loadOwner();
  }
  if (!welcomed && !state.app.onboarding_complete && !state.app.demo) {
    welcomed = true;
    openDialog('welcome');
  }
  if (state.route.view === 'matches') loadMatchSteam();
}

/* ---------- Live match and lobby games ---------- */

function followLive(state) {
  const live = state.app.live_match;
  const id = live?.opponent?.steam_id || null;
  if (id && ['select', 'loading', 'playing'].includes(live.phase)) loadProfiles([id, live.self_id].filter(Boolean));
  if (!id || id === liveSeen) {
    if (!id && live?.phase !== 'select' && live?.phase !== 'loading' && live?.phase !== 'playing') liveSeen = null;
    return;
  }
  liveSeen = id;
  if (state.route.view !== 'live') {
    const name = liveNameOf(state, live, id) || t('liveNameUnavailable');
    toast(t('liveFoundToast', { name }), { tone: 'accent', timeout: 15000, action: { label: t('open'), href: '#/live' } });
  }
}

export async function loadCasual() {
  if (casualLoading) return;
  casualLoading = true;
  try {
    const since = store.get().casual.revision;
    const value = await api('/api/casual' + (since ? '?since=' + encodeURIComponent(since) : ''));
    store.set({ casual: { revision: value.revision, games: value.games ?? store.get().casual.games, status: value.status, loaded: true } });
  } catch {
    // The next state poll retries.
  } finally {
    casualLoading = false;
  }
}

/* ---------- Player dossiers and replay analysis ---------- */

export async function loadDossier(id, force = false) {
  const cached = store.get().dossiers[id];
  if (!id || (cached && !force && (cached.loading || Date.now() - (cached.at || 0) < 60000))) return;
  store.merge('dossiers', { [id]: { ...(cached || {}), loading: true } });
  try {
    const data = await api('/api/player?id=' + encodeURIComponent(id));
    store.merge('dossiers', { [id]: { data, at: Date.now(), loading: false } });
    loadProfiles([id, ...(data.board?.opponents || []).map(row => row.steam_id)].slice(0, 12));
  } catch (error) {
    store.merge('dossiers', { [id]: { error: error.message || t('requestFailed'), at: Date.now(), loading: false } });
  }
}

export async function loadReplay(id, force = false) {
  const cached = store.get().replays[id];
  if (!id || (cached && !force && (cached.loading || cached.data || Date.now() - (cached.at || 0) < 15000))) return;
  store.merge('replays', { [id]: { loading: true } });
  const query = /^[0-9a-f]{40}$/.test(id) ? 'key=' + id : 'file=' + encodeURIComponent(id);
  try {
    const data = await api('/api/replay-analysis?' + query);
    store.merge('replays', { [id]: { data, at: Date.now(), loading: false } });
    loadProfiles(data.players.map(player => player.steam_id).filter(Boolean));
  } catch (error) {
    store.merge('replays', { [id]: { error: error.status === 404 ? 'pending' : (error.message || t('requestFailed')), at: Date.now(), loading: false } });
  }
}

export async function loadReplayPlayerCurves(id) {
  const cached = store.get().replays[id];
  if (!cached?.data || cached.data.player_curves || cached.curvesState) return;
  store.merge('replays', { [id]: { ...cached, curvesState: 'loading' } });
  try {
    const data = await api('/api/replay-analysis?key=' + encodeURIComponent(cached.data.key) + '&series=1',
      undefined, { timeout: 100000 });
    if (store.get().replays[id]?.data !== cached.data) return;
    store.merge('replays', { [id]: { ...cached, data, at: Date.now(), curvesState: 'done' } });
  } catch {
    if (store.get().replays[id]?.data !== cached.data) return;
    store.merge('replays', { [id]: { ...cached, curvesState: 'error' } });
  }
}

export async function setFortsTools(enabled) {
  try {
    const live = await api('/api/live/settings', { fortstools: !!enabled });
    store.merge('app', { live_match: live });
  } catch (error) {
    failure(error);
  }
}

function announceLobbies(lobbies) {
  const seq = lobbies.notification_sequence || 0;
  if (lobbyNoticeSeen === null) { lobbyNoticeSeen = seq; return; }
  if (seq > lobbyNoticeSeen) {
    lobbyNoticeSeen = seq;
    if (lobbies.fresh && lobbies.notify && lobbies.new_names?.length) {
      toast(t('newRoomsNotice', { names: lobbies.new_names.slice(0, 3).join(', ') }), { tone: 'accent' });
    }
  }
}

/** A click on a Windows notification: show its section once. */
function followNotification(open) {
  const seq = open?.seq || 0;
  if (notificationSeen === null) { notificationSeen = seq; return; }
  if (seq <= notificationSeen) return;
  notificationSeen = seq;
  if (VIEWS.includes(open.route)) location.hash = routeHash(open.route);
}

export async function sync(force = false) {
  const state = store.get();
  if (state.connection === 'stopped' || polling) return;
  polling = true;
  try {
    const full = force || Date.now() - lastFullSync > FULL_SYNC_MS || !state.revision;
    // The focus flag keeps Windows notifications away while Next is in front.
    const query = new URLSearchParams({ focus: document.hasFocus() ? '1' : '0' });
    if (!full) query.set('since', state.revision);
    const response = await api('/api/state?' + query, undefined, { raw: true });
    const body = await response.text();
    if (!response.ok) throw new Error(body);
    // Most polls return exactly the previous answer; skip the render then.
    if (body === lastStateBody && store.get().connection === 'ok') return;
    lastStateBody = body;
    const next = JSON.parse(body);
    if (next.matches) lastFullSync = Date.now();
    applyState(next);
  } catch {
    if (store.get().connection !== 'stopped') store.set({ connection: 'lost', loaded: true });
  } finally {
    polling = false;
  }
}

export function startPolling() {
  const loop = async () => {
    if (store.get().connection === 'stopped') return;
    await sync();
    setTimeout(loop, POLL_MS);
  };
  loop();
  window.addEventListener('focus', () => sync(true));
  window.addEventListener('blur', () => sync());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(true); });
  setInterval(() => { if (!document.hidden) loadOwner(); }, 60000);
}

/* ---------- Steam data ---------- */

function mergeProfiles(profiles) {
  if (!profiles || !Object.keys(profiles).length) return;
  store.set(state => {
    const next = { ...state.profiles };
    for (const [id, profile] of Object.entries(profiles)) {
      if (profile.status === 'ok' || next[id]?.status !== 'ok') next[id] = profile;
    }
    return { profiles: next };
  });
}

export async function loadMatchSteam(force = false) {
  const state = store.get();
  const match = selectedMatch(state);
  if (!match) return;
  const season = state.app.active_season;
  const ids = (match.players || []).map(p => p.steam_id).filter(Boolean);
  const key = boardKey(match, season);
  if (!ids.length || !season || state.busy[key]) return;
  const cached = state.boards[key];
  if (!force && cached && Date.now() - (cached._received || 0) < BOARD_TTL_MS) return;
  store.merge('busy', { [key]: true });
  try {
    const data = await api('/api/steam?ids=' + ids.join(',') + '&season=' + season + (force ? '&refresh=1' : ''));
    mergeProfiles(data.profiles);
    store.merge('boards', { [key]: { ...data.leaderboard, _received: Date.now() } });
    if (force) toast(t(data.leaderboard?.status === 'ok' ? 'refreshed' : 'refreshFailed'), { tone: data.leaderboard?.status === 'ok' ? 'success' : 'error' });
  } catch (error) {
    store.merge('boards', { [key]: { status: 'error', message: error.message, _received: Date.now() } });
    if (force) failure(error, 'refreshFailed');
  } finally {
    store.merge('busy', { [key]: false });
  }
}

export async function loadOwner(force = false) {
  const state = store.get();
  const id = ownerId(state);
  const season = state.app.active_season;
  if (!id || !season) return;
  const serial = ++ownerRequest;
  store.merge('busy', { owner: true });
  try {
    const data = await api('/api/steam?ids=' + id + '&leader_id=' + id + '&season=' + season + (force ? '&refresh=1' : ''));
    const now = store.get();
    if (serial !== ownerRequest || ownerId(now) !== id || now.app.active_season !== season) return;
    mergeProfiles(data.profiles);
    store.set({ ownerBoard: data });
    if (force) toast(t('ratingRefreshed'), { tone: 'success' });
  } catch (error) {
    if (serial === ownerRequest && force) failure(error, 'refreshFailed');
  } finally {
    if (serial === ownerRequest) store.merge('busy', { owner: false });
  }
}

export async function retryProfile(id) {
  try {
    const data = await api('/api/profile?id=' + id + '&retry=1');
    mergeProfiles({ [id]: data.profiles?.[id] || { status: 'unavailable' } });
  } catch (error) {
    failure(error, 'refreshFailed');
  }
}

const profileRetryAt = new Map();

/** Fetch missing profiles on demand and allow a later retry after a temporary failure. */
export async function loadProfiles(ids) {
  const state = store.get();
  const now = Date.now();
  const missing = [...new Set(ids)].filter(id => id && state.profiles[id]?.status !== 'ok'
    && now >= (profileRetryAt.get(id) || 0)).slice(0, 24);
  missing.forEach(id => profileRetryAt.set(id, Infinity));
  for (let start = 0; start < missing.length; start += 4) {
    const batch = missing.slice(start, start + 4);
    const found = {};
    await Promise.all(batch.map(async id => {
      try {
        const data = await api('/api/profile?id=' + id);
        if (data.profiles?.[id]) found[id] = data.profiles[id];
      } catch { /* a missing avatar is not worth a toast */ }
      if (found[id]?.status === 'ok') profileRetryAt.delete(id);
      else profileRetryAt.set(id, Date.now() + 1000 * (found[id]?.retry_in || 120));
    }));
    mergeProfiles(found);
  }
}

function loadAccountProfiles() {
  loadProfiles(store.get().app.accounts || []);
}

export async function loadEncounters(match) {
  const state = store.get();
  const [left, right] = pair(match, ownerId(state));
  if (!left?.steam_id || !right?.steam_id) return;
  const key = [left.steam_id, right.steam_id, match.match_id, state.revision].join(':');
  if (state.encounters[key]) return;
  store.merge('encounters', { [key]: { status: 'loading' } });
  try {
    const data = await api('/api/encounters?own=' + left.steam_id + '&opponent=' + right.steam_id + '&exclude=' + encodeURIComponent(match.match_id));
    store.merge('encounters', { [key]: { status: 'ok', ...data } });
  } catch {
    store.merge('encounters', { [key]: { status: 'error' } });
  }
}

export function encountersKey(match, state = store.get()) {
  const [left, right] = pair(match, ownerId(state));
  if (!left?.steam_id || !right?.steam_id) return null;
  return [left.steam_id, right.steam_id, match.match_id, state.revision].join(':');
}

/* ---------- Settings, account, language ---------- */

export async function setRemoteLanguage(language) {
  try {
    applyState(await api('/api/language', { language }));
  } catch (error) {
    failure(error);
  }
}

export async function toggleWatch() {
  const watch = store.get().app.watch || { enabled: false, folders: [] };
  try {
    applyState(await api('/api/watch', { enabled: !watch.enabled, folders: watch.folders || [] }));
  } catch (error) {
    failure(error);
  }
}

export async function chooseAccount(mode, id = '') {
  if (mode === 'manual' && (!id || !validSteamId(id))) throw new Error(t('invalidId'));
  applyState(await api('/api/account', { mode, steam_id: id }));
  store.merge('ui', { menu: false });
  toast(t('accountChanged'), { tone: 'success' });
}

export async function hideAccount(id, hidden = true) {
  try {
    applyState(await api('/api/account/hide', { steam_id: id, hidden }));
    toast(t(hidden ? 'accountHidden' : 'accountRestored'), { tone: 'success' });
  } catch (error) {
    failure(error);
  }
}

export function openSettings() {
  const state = store.get();
  store.set({
    settings: {
      watchEnabled: !!state.app.watch?.enabled,
      folders: (state.app.watch?.folders || []).join('\n'),
      myId: ownerId(state),
      radarEnabled: state.radar.enabled !== false,
      windowsNotifications: !!state.app.notifications?.enabled,
      fortsTools: state.app.live_match?.fortstools?.enabled !== false,
      error: '',
      saving: false,
    },
  });
  openDialog('settings');
}

export async function saveSettings() {
  const draft = store.get().settings;
  if (!draft) return;
  const id = draft.myId.trim();
  const folders = draft.folders.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  store.merge('settings', { saving: true, error: '' });
  let partial = false;
  try {
    if (!validSteamId(id)) throw new Error(t('invalidId'));
    await api('/api/watch', { enabled: draft.watchEnabled, folders });
    partial = true;
    await api('/api/owner', { steam_id: id });
    await api('/api/radar', { enabled: draft.radarEnabled, notify: !!draft.windowsNotifications });
    if (store.get().app.live_match) await api('/api/live/settings', { fortstools: draft.fortsTools !== false });
    await sync(true);
    closeDialog();
    toast(t('saved'), { tone: 'success' });
  } catch (error) {
    store.merge('settings', { error: (error.message || t('requestFailed')) + (partial ? ' ' + t('saveFailed') : '') });
  } finally {
    store.merge('settings', { saving: false });
  }
}

export async function completeOnboarding(openSettingsAfter = false) {
  try {
    if (!isDemo()) applyState(await api('/api/onboarding', { complete: true }));
    closeDialog();
    if (openSettingsAfter) openSettings();
  } catch (error) {
    failure(error);
  }
}

export async function quit() {
  try {
    await api('/api/stop', {});
  } catch { /* the server may close the connection while stopping */ }
  closeDialog();
  store.set({ connection: 'stopped' });
}

export async function toggleDemoEmpty() {
  try {
    applyState(await api('/api/demo-scenario', { empty: !store.get().app.demo_empty }));
  } catch (error) {
    failure(error);
  }
}

/* ---------- Replays ---------- */

export function addFiles(fileList, router) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  if (isDemo()) { toast(t('demoUnsupported'), { tone: 'info' }); return; }
  store.merge('ui', ui => ({ importing: ui.importing + files.length }));
  (async () => {
    let last = null;
    for (const file of files) {
      try {
        if (!file.name.toLowerCase().endsWith('.fwr')) throw new Error(t('needFwr'));
        const response = await fetch('/api/analyze?name=' + encodeURIComponent(file.name), {
          method: 'POST',
          headers: { 'X-Forts-Token': store.get().boot.token, 'Content-Type': 'application/octet-stream' },
          body: file,
          signal: AbortSignal.timeout(60000),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || t('importFailed'));
        last = result.match_id;
        toast(t('importDone', { name: file.name }), { tone: 'success' });
      } catch (error) {
        toast(file.name + ': ' + error.message, { tone: 'error' });
      } finally {
        store.merge('ui', ui => ({ importing: Math.max(0, ui.importing - 1) }));
      }
    }
    if (last) {
      await sync(true);
      store.merge('matchesUi', { query: '', season: '', result: '', opponent: '' });
      router?.go('matches', last);
    }
  })();
}

/* ---------- Radar ---------- */

export async function setRadarEnabled(enabled) {
  const before = store.get().radar;
  store.set({ radar: { ...before, enabled, status: enabled ? 'checking' : 'disabled' } });
  try {
    applyState(await api('/api/radar', { enabled, notify: !!before.notify }));
  } catch (error) {
    store.set({ radar: before });
    failure(error);
  }
}

export async function refreshRadar() {
  if (store.get().radarUi.refreshing) return;
  store.merge('radarUi', { refreshing: true });
  try {
    const previous = Number(store.get().radar.checked_at) || 0;
    let radar = await api('/api/radar?refresh=1');
    store.set({ radar });
    if (!['disabled', 'paused'].includes(radar.status)) {
      const deadline = Date.now() + 35000;
      while ((Number(radar.checked_at) || 0) <= previous && Date.now() < deadline && store.get().connection !== 'stopped') {
        await new Promise(resolve => setTimeout(resolve, 1000));
        radar = await api('/api/radar');
        store.set({ radar });
        if (['disabled', 'paused'].includes(radar.status)) break;
      }
      const ok = (Number(radar.checked_at) || 0) > previous;
      toast(t(ok ? 'refreshed' : 'refreshFailed'), { tone: ok ? 'success' : 'error' });
    }
  } catch (error) {
    failure(error, 'refreshFailed');
  } finally {
    store.merge('radarUi', { refreshing: false });
  }
}

/* ---------- Lobbies ---------- */

async function lobbyCall(path, body, { busy = false } = {}) {
  if (busy) store.merge('lobbyUi', { busy: true });
  try {
    const lobbies = await api(path, body);
    store.set({ lobbies });
    return lobbies;
  } catch (error) {
    failure(error);
    return null;
  } finally {
    if (busy) store.merge('lobbyUi', { busy: false });
  }
}

export function refreshLobbies() {
  const { lobbies, lobbyUi } = store.get();
  if (lobbyUi.busy || lobbies.busy) return;
  lobbyCall('/api/lobbies/refresh', { once: !lobbies.game_running }, { busy: true });
}

export async function joinLobby(id) {
  if (isDemo()) { toast(t('demoUnsupported')); return; }
  store.merge('lobbyUi', { busy: true });
  try {
    await api('/api/lobbies/join', { id });
    toast(t('joinSent'), { tone: 'success' });
  } catch (error) {
    failure(error);
  } finally {
    store.merge('lobbyUi', { busy: false });
  }
}

export async function restartGame() {
  if (isDemo()) { toast(t('demoUnsupported')); return; }
  store.merge('lobbyUi', { restarting: true, confirmRestart: false });
  try {
    await api('/api/lobbies/restart-game', {});
    toast(t('gameStarting'), { tone: 'success' });
  } catch (error) {
    failure(error);
  } finally {
    store.merge('lobbyUi', { restarting: false });
  }
}

export const forgetLastLobby = () => lobbyCall('/api/lobbies/settings', { forget_last: true });
export const toggleFavorite = id => lobbyCall('/api/lobbies/settings', { favorite: id });
export const setLobbyNotify = value => lobbyCall('/api/lobbies/settings', { notify: value });
export const lobbyScenario = mode => lobbyCall('/api/lobbies/demo', { mode });

export async function testSound() {
  if (isDemo()) { toast(t('demoUnsupported')); return; }
  try { await api('/api/lobbies/sound', {}); } catch (error) { failure(error); }
}

export async function testNotification() {
  store.merge('settings', { testing: true });
  try {
    const notifications = await api('/api/notifications/test', {});
    store.merge('app', { notifications });
    toast(t('notificationSent'), { tone: 'success' });
  } catch (error) {
    failure(error);
  } finally {
    store.merge('settings', { testing: false });
  }
}

export async function exportLobbies() {
  try {
    const snapshot = await api('/api/lobbies/export');
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'matchroom-next-lobbies-' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (error) {
    failure(error);
  }
}

export async function copyText(value, message) {
  try {
    await navigator.clipboard.writeText(value);
    toast(message, { tone: 'success' });
  } catch {
    toast(value);
  }
}

/* ---------- App window ---------- */

/** Remember the window size and position so the next launch restores it. */
export function trackWindow() {
  let last = '';
  const report = keepalive => {
    const rect = {
      x: window.screenX, y: window.screenY, width: window.outerWidth, height: window.outerHeight,
      maximized: window.outerWidth >= screen.availWidth && window.outerHeight >= screen.availHeight,
    };
    const value = JSON.stringify(rect);
    if (value === last || store.get().connection === 'stopped') return;
    last = value;
    api('/api/window', rect, { keepalive }).catch(() => {});
  };
  let timer = null;
  window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(() => report(false), 800); });
  window.addEventListener('pagehide', () => report(true));
  setInterval(() => report(false), 5000);
}

export async function checkUpdate() {
  store.merge('settings', { updateBusy: true, updateMessage: '', updateAvailable: false });
  try {
    const result = await api('/api/update');
    store.merge('settings', { updateAvailable: result.available, updateMessage: result.available ? t('updateAvailable') + ' ' + result.version : t('updateCurrent') });
  } catch { store.merge('settings', { updateMessage: t('updateCheckFailed') }); }
  finally { store.merge('settings', { updateBusy: false }); }
}
export async function installUpdate() {
  store.merge('settings', { updateBusy: true, updateMessage: t('updateDownloading') });
  try { await api('/api/update', {}, { timeout: 180000 }); }
  catch { store.merge('settings', { updateBusy: false, updateMessage: t('updateInstallFailed') }); }
}
