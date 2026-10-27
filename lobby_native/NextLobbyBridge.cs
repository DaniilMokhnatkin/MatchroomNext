using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

namespace FortsLobbyWatch {
    // Only --worker/--once initialize Steamworks. The controller stays read-only
    // until a command arrives; it never starts Forts or Steam.
    static class Program {
        internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8000000 };
        static string Executable { get { return Assembly.GetExecutingAssembly().Location; } }
        static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }

        static int Worker(string[] args) {
            bool manual = args[0] == "--once";
            Snapshot result;
            try {
                var session = !manual && args.Length == 4
                    ? new GameSession(Int32.Parse(args[2]), Int64.Parse(args[3]), null) : GameSession.Detect();
                if (!manual && (session == null || !session.IsRunning())) result = Snapshot.Paused();
                else if (!GameSession.SteamRunning()) result = new Snapshot { Status = "error", Message = "Steam закрыт. Открой его вручную." };
                else {
                    Environment.SetEnvironmentVariable("SteamAppId", "410900");
                    Environment.SetEnvironmentVariable("SteamGameId", "410900");
                    result = manual
                        ? LobbyReader.ReadOnce(true, GameSession.SteamRunning, () => new SteamLobbyApi(SteamLobbyApi.Locate(null)))
                        : LobbyReader.Read(() => session.IsRunning(), () => new SteamLobbyApi(SteamLobbyApi.Locate(session)));
                }
            } catch (Exception ex) { result = new Snapshot { Status = "error", Message = ex.Message }; }
            result.Manual = manual;
            File.WriteAllText(Path.GetFullPath(args[1]), Json.Serialize(result), new UTF8Encoding(false));
            return 0;
        }

        [STAThread]
        static int Main(string[] args) {
            if (args.Length >= 2 && (args[0] == "--worker" || args[0] == "--once")) return Worker(args);
            if (args.Length == 3 && args[0] == "--toasts") return ToastHost.Run(args[1], Int32.Parse(args[2], CultureInfo.InvariantCulture));
            if (args.Length != 1 || args[0] != "--stdio") return 2;
            Console.InputEncoding = new UTF8Encoding(false);
            Console.OutputEncoding = new UTF8Encoding(false);
            using (var controller = new LobbyController()) {
                string line;
                while ((line = Console.ReadLine()) != null) {
                    try {
                        var command = Json.Deserialize<Dictionary<string, object>>(line);
                        string action = command.ContainsKey("command") ? Convert.ToString(command["command"]) : "status";
                        if (action == "quit") break;
                        object response;
                        if (action == "read" || action == "once") { controller.Read(action == "once"); response = controller.State(); }
                        else if (action == "notify") {
                            // Windows toasts need new-room detection without the chime.
                            controller.NotificationsEnabled = command.ContainsKey("enabled") && Convert.ToBoolean(command["enabled"]);
                            controller.SoundEnabled = command.ContainsKey("sound") ? Convert.ToBoolean(command["sound"]) : controller.NotificationsEnabled;
                            response = controller.State();
                        }
                        else if (action == "sound") { controller.TestSound(); response = new { ok = true }; }
                        else if (action == "join") response = controller.Join(command.ContainsKey("id") ? Convert.ToString(command["id"]) : "");
                        else if (action == "status") response = controller.State();
                        else throw new ArgumentException("Unknown command");
                        Console.WriteLine(Json.Serialize(response));
                    } catch (Exception ex) { Console.WriteLine(Json.Serialize(new { error = ex.Message })); }
                    Console.Out.Flush();
                }
            }
            return 0;
        }

        internal static Snapshot ReadChild(bool manual, GameSession session) {
            string directory = Path.Combine(Path.GetTempPath(), "matchroom-next-lobby-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string output = Path.Combine(directory, "snapshot.json");
            try {
                File.WriteAllText(Path.Combine(directory, "steam_appid.txt"), "410900", Encoding.ASCII);
                string arguments = manual ? "--once " + Quote(output)
                    : "--worker " + Quote(output) + " " + session.Pid + " " + session.Started;
                using (var child = Process.Start(new ProcessStartInfo(Executable, arguments) {
                    UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = directory,
                    RedirectStandardOutput = true, RedirectStandardError = true })) {
                    child.BeginOutputReadLine(); child.BeginErrorReadLine();
                    try {
                        if (!child.WaitForExit(28000)) { child.Kill(); child.WaitForExit(); throw new TimeoutException("Steam не ответил. Обнови список позже."); }
                        child.WaitForExit();
                        if (!File.Exists(output)) throw new InvalidOperationException("Помощник не вернул список лобби.");
                        var info = new FileInfo(output);
                        if (info.Length > 8000000) throw new InvalidOperationException("Ответ слишком большой.");
                        return Json.Deserialize<Snapshot>(File.ReadAllText(output));
                    } finally { if (!child.HasExited) { child.Kill(); child.WaitForExit(); } }
                }
            } finally { try { Directory.Delete(directory, true); } catch { } }
        }
    }

    sealed class LobbyController : IDisposable {
        readonly Func<GameSession> detect;
        readonly Func<bool> steamRunning;
        readonly Func<bool, GameSession, Snapshot> reader;
        readonly Action<string> launch;
        readonly LobbyHistory history = new LobbyHistory();
        readonly LobbyNotifications notifications = new LobbyNotifications();
        readonly NotificationChime chime = new NotificationChime();
        GameSession session;
        Snapshot snapshot;
        string state = "paused", message = "";
        long notificationSequence;
        string[] newNames = new string[0];
        public bool NotificationsEnabled { get { return notifications.Enabled; } set { notifications.Enabled = value; } }
        public bool SoundEnabled { get; set; }

        public LobbyController() : this(GameSession.Detect, GameSession.SteamRunning, Program.ReadChild,
            uri => Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true })) { }
        internal LobbyController(Func<GameSession> detect, Func<bool> steamRunning,
            Func<bool, GameSession, Snapshot> reader, Action<string> launch) {
            this.detect = detect; this.steamRunning = steamRunning; this.reader = reader; this.launch = launch;
        }
        bool UpdateSession() {
            var current = detect();
            bool same = session == null ? current == null : session.SameAs(current);
            if (!same) {
                session = current; snapshot = null; history.Reset(); notifications.Reset();
                newNames = new string[0]; state = current == null ? "paused" : "checking"; message = "";
            }
            return current != null;
        }
        public void Read(bool manual) {
            bool running = UpdateSession();
            if (!manual && !running) { state = "paused"; return; }
            // A manual one-shot is only a closed-game operation; running games
            // always get a live, session-guarded read.
            manual = manual && !running;
            var requested = session;
            Snapshot result;
            try { result = reader(manual, requested); }
            catch (Exception ex) { result = new Snapshot { Status = "error", Message = ex.Message, Manual = manual }; }
            UpdateSession();
            bool same = requested == null ? session == null : requested.SameAs(session);
            if (!same) return; // Never publish the previous game's response.
            message = result.Message ?? "";
            if (result.Status == "ok") {
                result.Manual = manual;
                snapshot = result; state = manual ? "manual" : "ok";
                if (!manual) {
                    history.Observe(result, DateTime.UtcNow);
                    var added = notifications.Observe(result);
                    if (added.Count > 0) { notificationSequence++; newNames = added.Select(row => row.Name).ToArray(); if (SoundEnabled) chime.Play(); }
                }
            } else state = result.Status == "paused" ? "paused" : "error";
        }
        public object State() {
            bool running = UpdateSession();
            DateTime now = DateTime.UtcNow;
            bool fresh = running && LobbyJoin.Fresh(snapshot, state, now);
            bool steam = steamRunning();
            string displayState = state == "ok" && !fresh ? "stale" : state;
            var rows = snapshot == null ? new List<Lobby>() : snapshot.Lobbies;
            var own = rows.FirstOrDefault(row => row.IsOwn);
            // Metadata never crosses into the browser, even after sanitation.
            // Typed public fields and history are sufficient for every UI action.
            return new {
                status = displayState, message = message, game_running = running,
                session = session == null ? null : session.Pid + ":" + session.Started,
                steam_running = steam, fresh = fresh, manual = snapshot != null && snapshot.Manual,
                checked_at = snapshot == null ? null : snapshot.CheckedAt,
                truncated = snapshot != null && snapshot.Truncated,
                online_players = snapshot == null ? null : snapshot.OnlinePlayers,
                self_id = snapshot == null ? null : snapshot.SelfId,
                notification_sequence = notificationSequence, new_names = newNames,
                notify = NotificationsEnabled,
                comparison = own == null ? null : history.Compare(own, snapshot, now, fresh),
                lobbies = rows.Select(row => new {
                    id = row.Id, name = row.Name, map = row.Map, host = row.HostName, owner_id = row.OwnerId,
                    host_afk = row.HostAfk, players = row.Players, max_players = row.MaxPlayers,
                    free_slots = row.FreeSlots, ranked = row.Ranked, in_progress = row.InProgress,
                    password = row.Password, own = row.IsOwn,
                    watchable = LobbyViews.Watchable(row),
                    can_join = fresh && steam && !row.IsOwn && LobbyJoin.ValidLobbyId(row.Id),
                    activity = history.Describe(row, now, fresh)
                }).ToArray()
            };
        }
        public object Join(string id) {
            UpdateSession();
            var captured = session;
            Func<bool> sameGame = () => captured != null && captured.SameAs(detect()) && captured.IsRunning();
            var row = snapshot == null ? null : snapshot.Lobbies.FirstOrDefault(item => item.Id == id);
            string result;
            bool ok = LobbyJoin.Request(row, snapshot, state, sameGame, steamRunning, launch, out result);
            return new { ok = ok, message = result };
        }
        public void TestSound() { chime.Play(); }
        public void Dispose() { chime.Dispose(); }
    }
}
