using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Threading;

namespace FortsLobbyWatch {
    public sealed class RawLobby {
        public ulong Id;
        public int SteamMembers;
        public int SteamLimit;
        public Dictionary<string, string> Data = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    }

    public sealed class Lobby {
        public string Id { get; set; }
        public string Name { get; set; }
        public string Map { get; set; }
        public string Version { get; set; }
        public string OwnerId { get; set; }
        public string HostName { get; set; }
        public bool? HostAfk { get; set; }
        public int? Players { get; set; }
        public int? MaxPlayers { get; set; }
        public bool? Ranked { get; set; }
        public bool? InProgress { get; set; }
        public bool? Password { get; set; }
        public bool IsOwn { get; set; }
        public Dictionary<string, string> Metadata { get; set; }

        public int? FreeSlots {
            get { return Players.HasValue && MaxPlayers.HasValue ? (int?)Math.Max(0, MaxPlayers.Value - Players.Value) : null; }
        }
        public string Occupancy {
            get { return (Players.HasValue ? Players.Value.ToString() : "—") + " / " + (MaxPlayers.HasValue ? MaxPlayers.Value.ToString() : "—"); }
        }

        static string Value(Dictionary<string, string> data, params string[] keys) {
            foreach (string key in keys) {
                string value;
                if (data.TryGetValue(key, out value) && !String.IsNullOrWhiteSpace(value)) return value.Trim();
            }
            return "";
        }
        public static bool? Flag(string value) {
            switch ((value ?? "").Trim().ToLowerInvariant()) {
                case "1": case "true": case "yes": return true;
                case "0": case "false": case "no": return false;
                default: return null;
            }
        }
        static int? Count(string text) {
            int result;
            return Int32.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out result) && result >= 0 && result <= 1000 ? (int?)result : null;
        }
        public static Lobby FromRaw(RawLobby raw, ulong self) {
            var data = new Dictionary<string, string>(raw.Data, StringComparer.OrdinalIgnoreCase);
            string[] parts = Value(data, "players").Split('/');
            int? players = Count(parts[0]);
            int? maximum = Count(Value(data, "maxplayers"));
            if (!maximum.HasValue && parts.Length == 2) maximum = Count(parts[1]);
            if (maximum == 0) maximum = null;
            ulong owner;
            string ownerText = Value(data, "ownerId");
            if (!UInt64.TryParse(ownerText, out owner) || (owner >> 32) != 0x01100001 || (uint)owner == 0) ownerText = "";
            bool? password = null;
            string passwordValue;
            if (data.TryGetValue("password", out passwordValue)) {
                password = String.IsNullOrEmpty(passwordValue) ? (bool?)false : (Flag(passwordValue) ?? true);
                // Public metadata may contain a password or hash. Keep only its presence.
                data["password"] = password == true ? "1" : "0";
            } else if (data.ContainsKey("name") && data.ContainsKey("players") && data.ContainsKey("maxplayers")
                       && Flag(Value(data, "ranked")).HasValue && Flag(Value(data, "inProgress")).HasValue) {
                // Live Forts metadata omits the password key for unprotected rooms.
                // Incomplete/unknown metadata still keeps the access flag unknown.
                password = false;
            }
            string name = Value(data, "name", "lobbyname", "gamename");
            return new Lobby {
                Id = raw.Id.ToString(CultureInfo.InvariantCulture),
                Name = name.Length == 0 ? "Лобби " + raw.Id.ToString(CultureInfo.InvariantCulture) : name,
                Map = Value(data, "map", "mapname"),
                Version = Value(data, "version"), OwnerId = ownerText, HostName = Value(data, "owner"), HostAfk = Flag(Value(data, "afk")),
                Players = players, MaxPlayers = maximum, Password = password,
                Ranked = Flag(Value(data, "ranked")), InProgress = Flag(Value(data, "inProgress")),
                IsOwn = ownerText.Length > 0 && self != 0 && owner == self, Metadata = data
            };
        }
    }

    public sealed class Snapshot {
        public string Status { get; set; }
        public string Message { get; set; }
        public string CheckedAt { get; set; }
        public string SelfId { get; set; }
        public bool Truncated { get; set; }
        public bool SteamInitialized { get; set; }
        public bool Manual { get; set; }
        public int ReportedCount { get; set; }
        public int? OnlinePlayers { get; set; }
        public List<Lobby> Lobbies { get; set; }
        public Snapshot() { Lobbies = new List<Lobby>(); }
        public static Snapshot Paused() {
            return new Snapshot { Status = "paused", Message = "Forts закрыта. Запусти игру вручную — список появится автоматически." };
        }
    }

    public interface ILobbyApi : IDisposable {
        bool Initialize();
        uint AppId { get; }
        ulong SelfId { get; }
        ulong StartSearch(int limit);
        bool PollSearch(ulong call, out uint count);
        RawLobby GetLobby(int index);
    }

    public interface IPlayerCountApi {
        ulong StartPlayerCount();
        bool PollPlayerCount(ulong call, out int? players);
    }

    public static class LobbyReader {
        public const int Limit = 250;
        sealed class GameClosed : Exception { }
        static void Guard(Func<bool> running) { if (!running()) throw new GameClosed(); }

        public static Snapshot ReadOnce(bool userRequested, Func<bool> steamRunning, Func<ILobbyApi> createApi) {
            if (!userRequested) return Snapshot.Paused();
            Snapshot snapshot = Read(steamRunning, createApi);
            snapshot.Manual = true;
            if (snapshot.Status == "paused") { snapshot.Status = "error"; snapshot.Message = "Steam закрыт. Открой его вручную и повтори разовый запрос."; }
            else if (snapshot.Message != null) snapshot.Message = snapshot.Message.Replace("Повторим автоматически.", "Повтори запрос кнопкой.");
            return snapshot;
        }

        public static Snapshot Read(Func<bool> running, Func<ILobbyApi> createApi) {
            if (!running()) return Snapshot.Paused();
            bool initialized = false;
            try {
                Guard(running);
                using (ILobbyApi api = createApi()) {
                    Guard(running);
                    if (!api.Initialize()) return new Snapshot { Status = "error", Message = "Steam недоступен. Открой Steam с аккаунтом, которому принадлежит Forts." };
                    initialized = true;
                    Guard(running);
                    if (api.AppId != 410900) throw new InvalidOperationException("Steam вернул контекст другой игры; запрос отменён.");
                    ulong self = api.SelfId;
                    ulong handle = api.StartSearch(Limit);
                    if (handle == 0) throw new InvalidOperationException("Steam отклонил запрос списка лобби.");
                    var clock = Stopwatch.StartNew();
                    var onlineApi = api as IPlayerCountApi;
                    ulong onlineHandle = 0;
                    Guard(running);
                    try { if (onlineApi != null) onlineHandle = onlineApi.StartPlayerCount(); } catch { }
                    int? onlinePlayers = null;
                    bool onlineDone = onlineHandle == 0, listDone = false;
                    uint count = 0;
                    while (true) {
                        Guard(running);
                        if (!listDone) listDone = api.PollSearch(handle, out count);
                        Guard(running);
                        if (!onlineDone) {
                            if (clock.Elapsed.TotalSeconds >= 5) { onlineDone = true; onlinePlayers = null; }
                            else {
                                try { onlineDone = onlineApi.PollPlayerCount(onlineHandle, out onlinePlayers); }
                                catch { onlineDone = true; onlinePlayers = null; }
                            }
                        }
                        if (listDone && onlineDone) break;
                        if (!listDone && clock.Elapsed.TotalSeconds > 22) throw new TimeoutException("Steam не ответил на запрос лобби. Повторим автоматически.");
                        Thread.Sleep(50);
                    }
                    Guard(running);
                    var result = new Snapshot { Status = "ok", SelfId = self.ToString(CultureInfo.InvariantCulture),
                        ReportedCount = (int)Math.Min(count, Int32.MaxValue), Truncated = count >= Limit,
                        CheckedAt = DateTime.UtcNow.ToString("o"), SteamInitialized = true,
                        OnlinePlayers = onlineDone && onlinePlayers >= 0 ? onlinePlayers : null };
                    var seen = new HashSet<ulong>();
                    for (int i = 0; i < Math.Min(count, Limit); i++) {
                        Guard(running);
                        RawLobby raw = api.GetLobby(i);
                        if (raw != null && raw.Id != 0 && seen.Add(raw.Id)) result.Lobbies.Add(Lobby.FromRaw(raw, self));
                    }
                    Guard(running);
                    return result;
                }
            } catch (GameClosed) {
                var result = Snapshot.Paused(); result.SteamInitialized = initialized; return result;
            } catch (Exception ex) {
                if (!running()) { var result = Snapshot.Paused(); result.SteamInitialized = initialized; return result; }
                return new Snapshot { Status = "error", Message = ex.Message, SteamInitialized = initialized };
            }
        }
    }

    public sealed class GameSession {
        public int Pid { get; private set; }
        public long Started { get; private set; }
        public string Executable { get; private set; }
        public GameSession(int pid, long started, string executable) { Pid = pid; Started = started; Executable = executable; }
        public bool IsRunning() {
            try {
                using (Process process = Process.GetProcessById(Pid)) {
                    return !process.HasExited && String.Equals(process.ProcessName, "Forts", StringComparison.OrdinalIgnoreCase)
                        && process.StartTime.ToUniversalTime().Ticks == Started;
                }
            } catch { return false; }
        }
        public bool SameAs(GameSession other) { return other != null && Pid == other.Pid && Started == other.Started; }
        public static GameSession Detect() {
            Process[] processes;
            try { processes = Process.GetProcessesByName("Forts"); } catch { return null; }
            GameSession result = null;
            foreach (Process process in processes) {
                using (process) {
                    try {
                        if (process.HasExited || result != null) continue;
                        long started = process.StartTime.ToUniversalTime().Ticks;
                        string path = null;
                        try { path = process.MainModule.FileName; } catch { }
                        result = new GameSession(process.Id, started, path);
                    } catch { }
                }
            }
            return result;
        }
        public static bool SteamRunning() {
            try {
                Process[] processes = Process.GetProcessesByName("steam");
                bool found = processes.Length > 0;
                foreach (Process process in processes) process.Dispose();
                return found;
            } catch { return false; }
        }
    }
}
