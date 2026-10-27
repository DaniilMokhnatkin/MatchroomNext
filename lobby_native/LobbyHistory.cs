using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace FortsLobbyWatch {
    public sealed class LobbyActivity {
        public bool IsNew { get; set; }
        public string Text { get; set; }
        public string Hint { get; set; }
        public string Path { get; set; }
        public string Brush { get; set; }
        public int NetChange { get; set; }
        public double CoverageSeconds { get; set; }
        public double ObservedSeconds { get; set; }
        public double UnchangedSeconds { get; set; }
        public int Gains { get; set; }
        public int Losses { get; set; }
        public int SampleCount { get; set; }
        public object[] Samples { get; set; }
    }

    public sealed class LobbyHistory {
        sealed class Sample { public DateTime At; public int Players; }
        sealed class Entry {
            public DateTime FirstSeen, LastSeen, NewUntil, LastChange;
            public int? Capacity;
            public bool? InProgress;
            public List<Sample> Samples = new List<Sample>();
        }
        readonly Dictionary<string, Entry> entries = new Dictionary<string, Entry>(StringComparer.Ordinal);
        bool baseline;
        public void Reset() { entries.Clear(); baseline = false; }
        public void Observe(Snapshot snapshot, DateTime now) {
            if (snapshot == null || snapshot.Manual || !LobbyJoin.Fresh(snapshot, snapshot.Status, now)) return;
            DateTime at;
            if (!DateTime.TryParse(snapshot.CheckedAt, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out at)) return;
            at = at.ToUniversalTime();
            foreach (Lobby row in snapshot.Lobbies) {
                if (row == null || String.IsNullOrEmpty(row.Id)) continue;
                Entry entry;
                if (!entries.TryGetValue(row.Id, out entry)) {
                    entry = new Entry { FirstSeen = at, LastSeen = DateTime.MinValue,
                        NewUntil = baseline && !snapshot.Truncated && !row.IsOwn && row.Ranked != true && !LobbyViews.Watchable(row) ? at.AddSeconds(20) : DateTime.MinValue };
                    entries.Add(row.Id, entry);
                }
                if (at <= entry.LastSeen) continue;
                if ((at - entry.LastSeen).TotalSeconds > 45 || entry.Capacity != row.MaxPlayers || entry.InProgress != row.InProgress) entry.Samples.Clear();
                entry.LastSeen = at; entry.Capacity = row.MaxPlayers; entry.InProgress = row.InProgress;
                if (!row.Players.HasValue || !row.MaxPlayers.HasValue || row.MaxPlayers <= 0 || row.Players < 0 || row.Players > row.MaxPlayers) {
                    entry.Samples.Clear(); continue;
                }
                if (entry.Samples.Count == 0 || entry.Samples.Last().Players != row.Players.Value) entry.LastChange = at;
                entry.Samples.Add(new Sample { At = at, Players = row.Players.Value });
                DateTime cutoff = at.AddMinutes(-5);
                while (entry.Samples.Count > 2 && entry.Samples[1].At <= cutoff) entry.Samples.RemoveAt(0);
                while (entry.Samples.Count > 600) entry.Samples.RemoveAt(0);
            }
            baseline = true;
        }
        static string Duration(double seconds) {
            seconds = Math.Max(0, seconds);
            if (seconds < 60) return Math.Floor(seconds).ToString(CultureInfo.InvariantCulture) + " с";
            if (seconds < 3600) return Math.Floor(seconds / 60).ToString(CultureInfo.InvariantCulture) + " мин";
            return Math.Floor(seconds / 3600).ToString(CultureInfo.InvariantCulture) + " ч " + ((int)(seconds / 60) % 60) + " мин";
        }
        static string Signed(int value) { return value > 0 ? "+" + value : value < 0 ? "−" + Math.Abs(value) : "0"; }
        public LobbyActivity Describe(Lobby row, DateTime now, bool fresh) {
            var activity = new LobbyActivity { Text = fresh ? "История собирается" : "Наблюдение приостановлено", Brush = "#7E8FA4", Hint = "Изменения числа игроков по снимкам Forts. История хранится только во время текущего запуска игры." };
            Entry entry;
            if (row == null || String.IsNullOrEmpty(row.Id) || !entries.TryGetValue(row.Id, out entry)) return activity;
            activity.IsNew = fresh && now < entry.NewUntil && !row.IsOwn;
            string observed = "Впервые замечено: " + entry.FirstSeen.ToLocalTime().ToString("HH:mm:ss") + ". Это время наблюдения, а не создания комнаты.";
            if (!fresh) { activity.Hint += "\n" + observed; return activity; }
            if (entry.Samples.Count == 0) {
                activity.Text = "Число игроков неизвестно";
                activity.Hint += "\n" + observed;
                return activity;
            }
            List<Sample> samples = entry.Samples;
            double span = Math.Min(300, (entry.LastSeen - samples[0].At).TotalSeconds);
            activity.CoverageSeconds = span;
            activity.NetChange = samples.Last().Players - samples[0].Players;
            double unchanged = (entry.LastSeen - entry.LastChange).TotalSeconds;
            activity.ObservedSeconds = (entry.LastSeen - entry.FirstSeen).TotalSeconds;
            activity.UnchangedSeconds = unchanged;
            activity.SampleCount = samples.Count;
            activity.Samples = samples.Select(sample => (object)new { at = sample.At.ToString("o"), players = sample.Players }).ToArray();
            for (int index = 1; index < samples.Count; index++) {
                int delta = samples[index].Players - samples[index - 1].Players;
                if (delta > 0) activity.Gains += delta; else activity.Losses -= delta;
            }
            string change;
            if (samples.Count == 1) change = "История собирается";
            else if (activity.NetChange != 0) change = Signed(activity.NetChange) + " за " + Duration(span);
            else if (unchanged >= 300 && row.InProgress == false && row.FreeSlots > 0) change = "Набор стоит " + Duration(unchanged);
            else {
                int gains = 0, losses = 0;
                for (int i = 1; i < samples.Count; i++) {
                    int delta = samples[i].Players - samples[i - 1].Players;
                    if (delta > 0) gains += delta; else losses -= delta;
                }
                change = gains > 0 || losses > 0 ? "Изменения: +" + gains + " / −" + losses + " за " + Duration(span)
                    : "Без изменений " + Duration(unchanged);
            }
            activity.Text = change + " · Замечено " + Duration((entry.LastSeen - entry.FirstSeen).TotalSeconds) + " назад";
            activity.Brush = activity.NetChange > 0 ? "#98EDCB" : activity.NetChange < 0 ? "#E5BF7B" : "#7E8FA4";
            activity.Hint = observed + "\n" + change + "\nИзменения по снимкам; промежуточные входы и выходы между обновлениями могут быть не видны.\n"
                + String.Join("\n", samples.Where((sample, index) => index == 0 || sample.Players != samples[index - 1].Players).TakeLastCompat(10)
                    .Select(sample => sample.At.ToLocalTime().ToString("HH:mm:ss") + " · " + sample.Players + " / " + entry.Capacity));
            if (samples.Count >= 2) {
                int minimum = samples.Min(sample => sample.Players), maximum = samples.Max(sample => sample.Players);
                double interval = Math.Max(1, (samples.Last().At - samples[0].At).TotalSeconds);
                activity.Path = String.Join(" ", samples.Select((sample, index) => (index == 0 ? "M" : "L")
                    + (72 * (sample.At - samples[0].At).TotalSeconds / interval).ToString("0.##", CultureInfo.InvariantCulture) + ","
                    + (maximum == minimum ? 7 : 13 - 12.0 * (sample.Players - minimum) / (maximum - minimum)).ToString("0.##", CultureInfo.InvariantCulture)));
            }
            return activity;
        }
        public string Compare(Lobby own, Snapshot snapshot, DateTime now, bool fresh) {
            if (!fresh) return "Сравнение приостановлено до обновления списка.";
            if (own.InProgress == true) return "Твой матч уже идёт · другие комнаты остаются в списке ниже.";
            var candidates = snapshot.Lobbies.Where(row => !row.IsOwn && row.Ranked != true && !LobbyViews.Watchable(row)
                && row.Players >= 0 && row.MaxPlayers > 0 && row.Players <= row.MaxPlayers).Select(row => new { Row = row, Activity = Describe(row, now, true) }).ToList();
            if (candidates.Count == 0) return "Других лобби с набором сейчас нет.";
            var candidate = candidates.OrderByDescending(item => item.Activity.CoverageSeconds >= 30 && item.Activity.NetChange > 0
                    ? item.Activity.NetChange / item.Activity.CoverageSeconds : 0)
                .ThenByDescending(item => (double)item.Row.Players.Value / item.Row.MaxPlayers.Value).ThenBy(item => item.Row.Name).First();
            return "В другой комнате: " + candidate.Row.Name + " · " + candidate.Row.Occupancy + " · " + candidate.Activity.Text;
        }
    }
    static class HistoryEnumerable {
        public static IEnumerable<T> TakeLastCompat<T>(this IEnumerable<T> source, int count) {
            var values = source.ToList(); return values.Skip(Math.Max(0, values.Count - count));
        }
    }
}
