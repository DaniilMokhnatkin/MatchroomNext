using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;

namespace FortsLobbyWatch {
    public enum LobbyTab { Recruiting, Watching, All, Ranked }

    public static class LobbyViews {
        public static bool Watchable(Lobby row) { return row.InProgress == true || row.FreeSlots == 0; }
        public static List<Lobby> Filter(Snapshot snapshot, LobbyTab tab, string query, bool noPassword) {
            if (snapshot == null) return new List<Lobby>();
            query = (query ?? "").Trim();
            return snapshot.Lobbies.Where(row =>
                (tab == LobbyTab.Ranked ? row.Ranked == true : row.Ranked != true)
                && (tab != LobbyTab.Recruiting || !Watchable(row))
                && (tab != LobbyTab.Watching || Watchable(row))
                && (!noPassword || row.Password == false)
                && (query.Length == 0 || (row.Name + " " + row.Map + " " + row.HostName).IndexOf(query, StringComparison.CurrentCultureIgnoreCase) >= 0))
                .OrderByDescending(row => row.IsOwn).ThenByDescending(row => row.Players ?? -1).ThenBy(row => row.Name).ToList();
        }
    }

    public static class LobbyJoin {
        public static bool ValidLobbyId(string text) {
            ulong id;
            return UInt64.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out id)
                && (id >> 56) == 1 && ((id >> 52) & 15) == 8
                && (((id >> 32) & 0x40000) != 0) && (uint)id != 0;
        }
        public static string UriFor(Lobby row) {
            if (row == null || !ValidLobbyId(row.Id)) throw new ArgumentException("Неверный идентификатор лобби.");
            ulong owner;
            string host = UInt64.TryParse(row.OwnerId, NumberStyles.None, CultureInfo.InvariantCulture, out owner)
                && (owner >> 32) == 0x01100001 && (uint)owner != 0 ? owner.ToString(CultureInfo.InvariantCulture) : "0";
            return "steam://joinlobby/410900/" + row.Id + "/" + host;
        }
        public static bool Fresh(Snapshot snapshot, string state, DateTime now) {
            DateTime at;
            return snapshot != null && !snapshot.Manual && state == "ok" && snapshot.Status == "ok"
                && DateTime.TryParse(snapshot.CheckedAt, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out at)
                && (now - at.ToUniversalTime()).TotalSeconds >= -5 && (now - at.ToUniversalTime()).TotalSeconds <= 45;
        }
        public static bool Request(Lobby row, Snapshot snapshot, string state, Func<bool> gameRunning,
                                   Func<bool> steamRunning, Action<string> launch, out string message) {
            if (snapshot != null && snapshot.Manual) { message = "Это разовый снимок. Запусти Forts вручную и дождись автоматического обновления перед входом."; return false; }
            if (!gameRunning()) { message = "Forts закрыта. Запусти её вручную, чтобы войти в комнату."; return false; }
            if (!steamRunning()) { message = "Steam закрыт. Открой его вручную."; return false; }
            if (!Fresh(snapshot, state, DateTime.UtcNow)) { message = "Список устарел. Обнови его перед входом."; return false; }
            Lobby target = snapshot.Lobbies.FirstOrDefault(item => row != null && item.Id == row.Id);
            if (target == null || !ValidLobbyId(target.Id)) { message = "Комната больше не доступна в текущем списке."; return false; }
            if (target.IsOwn) { message = "Это твоё лобби."; return false; }
            string uri = UriFor(target);
            // Recheck at the actual hand-off. No protocol action is made while Forts/Steam are closed.
            if (!gameRunning() || !steamRunning()) { message = "Forts или Steam закрылись. Вход отменён."; return false; }
            try {
                launch(uri);
                message = "Запрос на вход передан Forts. Переключись в игру.";
                return true;
            } catch (Exception ex) { message = "Не удалось передать вход в Steam: " + ex.Message; return false; }
        }
    }
}
