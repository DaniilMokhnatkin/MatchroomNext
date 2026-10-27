using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace FortsLobbyWatch {
    // The DLL is loaded only by a guarded, short-lived child process.
    // These bindings only initialize, read lobby metadata and shut down Steamworks.
    public sealed class SteamLobbyApi : ILobbyApi, IPlayerCountApi {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Ansi, ExactSpelling = true)]
        static extern IntPtr GetProcAddress(IntPtr library, string name);
        [DllImport("kernel32.dll")]
        static extern bool FreeLibrary(IntPtr library);

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        [return: MarshalAs(UnmanagedType.I1)] delegate bool InitFn();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void VoidFn();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate IntPtr InterfaceFn();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate uint AppIdFn(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate ulong IdFn(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void FilterFn(IntPtr self, int value);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate ulong SearchFn(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        [return: MarshalAs(UnmanagedType.I1)] delegate bool CompletedFn(IntPtr self, ulong call, [MarshalAs(UnmanagedType.I1)] out bool failed);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        [return: MarshalAs(UnmanagedType.I1)] delegate bool ResultFn(IntPtr self, ulong call, out uint result, int size, int callback, [MarshalAs(UnmanagedType.I1)] out bool failed);
        [StructLayout(LayoutKind.Sequential, Pack = 4)]
        struct CurrentPlayers { public byte Success; public int Players; }
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        [return: MarshalAs(UnmanagedType.I1)] delegate bool PlayersResultFn(IntPtr self, ulong call, out CurrentPlayers result, int size, int callback, [MarshalAs(UnmanagedType.I1)] out bool failed);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate ulong LobbyFn(IntPtr self, int index);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int LobbyCountFn(IntPtr self, ulong lobby);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        [return: MarshalAs(UnmanagedType.I1)] delegate bool DataFn(IntPtr self, ulong lobby, int index, [Out] byte[] key, int keySize, [Out] byte[] value, int valueSize);

        IntPtr library, matchmaking, utils, user, stats;
        bool initialized;
        InitFn init;
        VoidFn shutdown, callbacks;
        AppIdFn appId;
        IdFn selfId;
        FilterFn distance, limit;
        SearchFn search;
        CompletedFn completed;
        ResultFn result;
        PlayersResultFn playersResult;
        SearchFn playerCount;
        LobbyFn lobbyByIndex;
        LobbyCountFn dataCount, members, memberLimit;
        DataFn data;

        T Bind<T>(string name) where T : class {
            IntPtr address = GetProcAddress(library, "SteamAPI_" + name);
            if (address == IntPtr.Zero) throw new InvalidOperationException("DLL установленной Forts не поддерживает " + name + ".");
            return Marshal.GetDelegateForFunctionPointer(address, typeof(T)) as T;
        }

        public SteamLobbyApi(string path) {
            if (!Path.IsPathRooted(path) || !File.Exists(path)) throw new FileNotFoundException("Не найдена Steamworks DLL установленной Forts.");
            library = LoadLibraryEx(Path.GetFullPath(path), IntPtr.Zero, 0x00000100 | 0x00001000);
            if (library == IntPtr.Zero) throw new InvalidOperationException("Не удалось загрузить Steamworks DLL Forts (Windows " + Marshal.GetLastWin32Error() + ").");
            try {
                init = Bind<InitFn>("Init"); shutdown = Bind<VoidFn>("Shutdown"); callbacks = Bind<VoidFn>("RunCallbacks");
                appId = Bind<AppIdFn>("ISteamUtils_GetAppID"); selfId = Bind<IdFn>("ISteamUser_GetSteamID");
                distance = Bind<FilterFn>("ISteamMatchmaking_AddRequestLobbyListDistanceFilter");
                limit = Bind<FilterFn>("ISteamMatchmaking_AddRequestLobbyListResultCountFilter");
                search = Bind<SearchFn>("ISteamMatchmaking_RequestLobbyList");
                completed = Bind<CompletedFn>("ISteamUtils_IsAPICallCompleted");
                result = Bind<ResultFn>("ISteamUtils_GetAPICallResult");
                lobbyByIndex = Bind<LobbyFn>("ISteamMatchmaking_GetLobbyByIndex");
                dataCount = Bind<LobbyCountFn>("ISteamMatchmaking_GetLobbyDataCount");
                members = Bind<LobbyCountFn>("ISteamMatchmaking_GetNumLobbyMembers");
                memberLimit = Bind<LobbyCountFn>("ISteamMatchmaking_GetLobbyMemberLimit");
                data = Bind<DataFn>("ISteamMatchmaking_GetLobbyDataByIndex");
            } catch { Dispose(); throw; }
        }

        public bool Initialize() {
            initialized = init();
            if (!initialized) return false;
            matchmaking = Bind<InterfaceFn>("SteamMatchmaking_v009")();
            utils = Bind<InterfaceFn>("SteamUtils_v010")();
            user = Bind<InterfaceFn>("SteamUser_v021")();
            if (matchmaking == IntPtr.Zero || utils == IntPtr.Zero || user == IntPtr.Zero)
                throw new InvalidOperationException("Steamworks не вернул необходимые интерфейсы.");
            // Optional: missing player-count support must not prevent reading rooms.
            try {
                stats = Bind<InterfaceFn>("SteamUserStats_v012")();
                playerCount = Bind<SearchFn>("ISteamUserStats_GetNumberOfCurrentPlayers");
                playersResult = Bind<PlayersResultFn>("ISteamUtils_GetAPICallResult");
            } catch { stats = IntPtr.Zero; }
            return true;
        }
        public uint AppId { get { return appId(utils); } }
        public ulong SelfId { get { return selfId(user); } }
        public ulong StartSearch(int count) {
            distance(matchmaking, 3); // Worldwide, including distant regions.
            limit(matchmaking, count);
            return search(matchmaking);
        }
        public bool PollSearch(ulong call, out uint count) {
            count = 0;
            callbacks();
            bool failed;
            if (!completed(utils, call, out failed)) return false;
            if (failed || !result(utils, call, out count, 4, 510, out failed) || failed)
                throw new InvalidOperationException("Не удалось прочитать ответ Steam. Повторим автоматически.");
            return true;
        }
        public ulong StartPlayerCount() { return stats == IntPtr.Zero ? 0 : playerCount(stats); }
        public bool PollPlayerCount(ulong call, out int? players) {
            players = null;
            callbacks();
            bool failed;
            if (!completed(utils, call, out failed)) return false;
            CurrentPlayers value;
            if (!failed && playersResult(utils, call, out value, Marshal.SizeOf(typeof(CurrentPlayers)), 1107, out failed)
                && !failed && value.Success == 1 && value.Players >= 0) players = value.Players;
            return true;
        }
        static string Utf8(byte[] bytes) {
            int end = Array.IndexOf(bytes, (byte)0);
            return Encoding.UTF8.GetString(bytes, 0, end < 0 ? bytes.Length : end);
        }
        public RawLobby GetLobby(int index) {
            ulong id = lobbyByIndex(matchmaking, index);
            if (id == 0) return null;
            var row = new RawLobby { Id = id, SteamMembers = members(matchmaking, id), SteamLimit = memberLimit(matchmaking, id) };
            int count = Math.Min(512, Math.Max(0, dataCount(matchmaking, id)));
            for (int i = 0; i < count; i++) {
                var key = new byte[256]; var value = new byte[16384];
                if (data(matchmaking, id, i, key, key.Length, value, value.Length)) row.Data[Utf8(key)] = Utf8(value);
            }
            return row;
        }
        public void Dispose() {
            if (initialized) { shutdown(); initialized = false; }
            if (library != IntPtr.Zero) { FreeLibrary(library); library = IntPtr.Zero; }
        }

        public static string Locate(GameSession session) {
            if (session != null && !String.IsNullOrEmpty(session.Executable)) {
                string active = Path.Combine(Path.GetDirectoryName(session.Executable), "steam_api64.dll");
                if (File.Exists(active)) return active;
            }
            var roots = new List<string>();
            try {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam")) {
                    string root = key == null ? null : key.GetValue("SteamPath") as string;
                    if (!String.IsNullOrEmpty(root)) roots.Add(root);
                }
            } catch { }
            roots.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Steam"));
            foreach (string root in roots.ToArray()) {
                try {
                    string text = File.ReadAllText(Path.Combine(root, "steamapps", "libraryfolders.vdf"));
                    foreach (Match match in Regex.Matches(text, "\"path\"\\s+\"([^\"]+)\"")) roots.Add(match.Groups[1].Value.Replace(@"\\", @"\"));
                } catch { }
            }
            foreach (string root in roots) {
                string path = Path.Combine(root, "steamapps", "common", "Forts", "steam_api64.dll");
                if (File.Exists(path)) return path;
            }
            throw new FileNotFoundException("Не найдена установленная Forts. Открой игру через Steam.");
        }
    }
}
