// Forts-wide counters shown in the top bar. Stale or invalid numbers become
// null so the UI shows "—" instead of a misleading zero.

export function activityTime(value) {
  if (value === null || value === undefined) return 0;
  return typeof value === 'number' ? value * 1000 : Date.parse(value) || 0;
}

export function activityValues(activity, now = Date.now()) {
  const valid = value => value !== null && value !== undefined && Number.isFinite(Number(value)) && Number(value) >= 0;
  const age = now - activityTime(activity?.checked_at);
  const fresh = age >= -5000 && age <= 90000;
  return {
    online: fresh && valid(activity?.online_players) ? Number(activity.online_players) : null,
    queue: fresh && activity?.status === 'ok' && activity.game_running !== false && valid(activity.ranked_waiting_players)
      ? Number(activity.ranked_waiting_players) : null,
    truncated: !!activity?.truncated,
  };
}
