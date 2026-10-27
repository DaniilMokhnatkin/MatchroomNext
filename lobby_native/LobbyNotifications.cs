using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Media;
using System.Text;

namespace FortsLobbyWatch {
    public sealed class LobbyNotifications {
        readonly HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
        bool baseline;
        public bool Enabled { get; set; }
        public void Reset() { seen.Clear(); baseline = false; }
        public List<Lobby> Observe(Snapshot snapshot) {
            var added = new List<Lobby>();
            if (snapshot == null || snapshot.Manual || !LobbyJoin.Fresh(snapshot, snapshot.Status, DateTime.UtcNow)) return added;
            foreach (Lobby row in snapshot.Lobbies) {
                if (row == null || String.IsNullOrEmpty(row.Id) || !seen.Add(row.Id)) continue;
                if (baseline && Enabled && !snapshot.Truncated && !row.IsOwn && row.Ranked != true && !LobbyViews.Watchable(row)) added.Add(row);
            }
            baseline = true;
            return added;
        }
    }

    public sealed class NotificationPreferences {
        public bool NewLobbies { get; set; }
        static string Filename { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FortsLobbyWatch", "settings.json"); } }
        public static NotificationPreferences Load() {
            try { return Program.Json.Deserialize<NotificationPreferences>(File.ReadAllText(Filename)) ?? new NotificationPreferences(); }
            catch { return new NotificationPreferences(); }
        }
        public bool Save() {
            try {
                Directory.CreateDirectory(Path.GetDirectoryName(Filename));
                File.WriteAllText(Filename, Program.Json.Serialize(this), new UTF8Encoding(false));
                return true;
            } catch { return false; }
        }
    }

    public sealed class NotificationChime : IDisposable {
        readonly MemoryStream source;
        readonly SoundPlayer player;
        public NotificationChime() {
            source = new MemoryStream(CreateWave(), false);
            player = new SoundPlayer(source);
        }
        public void Play() { try { player.Play(); } catch { } }
        public void Stop() { try { player.Stop(); } catch { } }
        public void Dispose() { Stop(); player.Dispose(); source.Dispose(); }
        public static byte[] CreateWave() {
            const int rate = 22050;
            int samples = (int)(rate * 0.38);
            using (var buffer = new MemoryStream()) {
                using (var writer = new BinaryWriter(buffer, Encoding.ASCII)) {
                    writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(36 + samples * 2);
                    writer.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); writer.Write(16);
                    writer.Write((short)1); writer.Write((short)1); writer.Write(rate);
                    writer.Write(rate * 2); writer.Write((short)2); writer.Write((short)16);
                    writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(samples * 2);
                    for (int i = 0; i < samples; i++) {
                        double t = (double)i / rate, value = 0;
                        for (int note = 0; note < 2; note++) {
                            double age = t - note * 0.18;
                            if (age < 0 || age >= 0.20) continue;
                            double envelope = Math.Min(1, age / 0.012) * Math.Exp(-age * 18) * Math.Min(1, (0.20 - age) / 0.025);
                            double phase = 2 * Math.PI * (note == 0 ? 660 : 880) * age;
                            value += 0.065 * envelope * (Math.Sin(phase) + 0.12 * Math.Sin(phase * 2));
                        }
                        writer.Write((short)(32767 * value));
                    }
                    writer.Flush();
                    return buffer.ToArray();
                }
            }
        }
    }
}
