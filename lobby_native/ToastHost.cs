using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Windows.Data.Xml.Dom;
using Windows.UI.Notifications;

namespace FortsLobbyWatch {
    // Windows toasts for Next (--toasts). One JSON command per stdin line:
    // {"command":"show","tag":"radar","xml":"<toast .../>","minutes":10}.
    // Replies are {"shown":tag,"setting":...} or {"error":...}; clicks arrive
    // later as {"activated":launch}. Python builds the XML and validates the
    // launch value again, so this process never opens anything by itself.
    static class ToastHost {
        [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int processId);
        static readonly object output = new object();

        static void Send(object value) {
            lock (output) { Console.WriteLine(Program.Json.Serialize(value)); Console.Out.Flush(); }
        }

        static string Setting(ToastNotifier notifier) {
            try { return notifier.Setting.ToString(); } catch { return "Unknown"; }
        }

        public static int Run(string appId, int owner) {
            Console.InputEncoding = new UTF8Encoding(false);
            Console.OutputEncoding = new UTF8Encoding(false);
            // Main is STA without a message pump; toast events need a free-threaded caller.
            var worker = new Thread(() => Loop(appId, owner));
            worker.SetApartmentState(ApartmentState.MTA);
            worker.Start();
            worker.Join();
            return 0;
        }

        static void Loop(string appId, int owner) {
            var notifier = ToastNotificationManager.CreateToastNotifier(appId);
            // Shown toasts stay referenced so their click events keep arriving.
            var shown = new Dictionary<string, ToastNotification>(StringComparer.Ordinal);
            string line;
            while ((line = Console.ReadLine()) != null) {
                try {
                    var command = Program.Json.Deserialize<Dictionary<string, object>>(line);
                    string action = command.ContainsKey("command") ? Convert.ToString(command["command"]) : "";
                    if (action == "quit") break;
                    if (action != "show") throw new ArgumentException("Unknown command");
                    string tag = Convert.ToString(command["tag"]);
                    var xml = new XmlDocument();
                    xml.LoadXml(Convert.ToString(command["xml"]));
                    var toast = new ToastNotification(xml) {
                        Tag = tag, Group = "matchroom-next",
                        ExpirationTime = DateTimeOffset.Now.AddMinutes(Convert.ToDouble(command["minutes"]))
                    };
                    toast.Activated += (sender, args) => {
                        // The click grants this process the foreground; pass it on to Next.
                        if (owner > 0) AllowSetForegroundWindow(owner);
                        var activated = args as ToastActivatedEventArgs;
                        Send(new { activated = activated == null ? "" : activated.Arguments });
                    };
                    toast.Failed += (sender, args) => Send(new { failed = tag });
                    notifier.Show(toast);
                    shown[tag] = toast;
                    Send(new { shown = tag, setting = Setting(notifier) });
                } catch (Exception ex) { Send(new { error = ex.Message }); }
            }
        }
    }
}
