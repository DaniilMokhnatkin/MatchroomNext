// Matchroom Next native shell.
//
// A small WinForms window that hosts the web client in WebView2 and owns the
// Python service process. It replaces the old "Edge in app mode" window:
//   * one real process and window of our own (taskbar, icon, single instance);
//   * the service starts and stops with the window, over a pipe, so it can
//     never be left running or started twice;
//   * links leave the app in the normal browser, nothing else can navigate it.
// Everything the shell needs is embedded in the EXE and unpacked on first run
// into MatchroomNextData\runtime. C# 5 only: it is compiled by the csc that
// ships with Windows.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace MatchroomNext
{
    static class Program
    {
        public const string Title = "Matchroom Next";
        public static string DataDir;
        public static string RuntimeDir;
        public static bool Demo;

        [STAThread]
        static int Main(string[] args)
        {
            string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
            DataDir = Path.Combine(exeDir, "MatchroomNextData");
            Directory.CreateDirectory(DataDir);
            Demo = Array.IndexOf(args, "--demo") >= 0;

            string key = Hex(SHA256.Create().ComputeHash(Encoding.UTF8.GetBytes(DataDir.ToLowerInvariant()))).Substring(0, 24);
            bool first;
            Mutex mutex = new Mutex(true, "Local\\MatchroomNextShell-" + key + (Demo ? "-demo" : ""), out first);
            EventWaitHandle activate = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\MatchroomNextActivate-" + key + (Demo ? "-demo" : ""));
            if (!first)
            {
                // Already running: bring that window forward and leave.
                activate.Set();
                return 0;
            }

            try
            {
                RuntimeDir = Runtime.Unpack(DataDir);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Не удалось распаковать файлы программы:\n" + ex.Message + "\n\nПапка должна быть доступна для записи.", Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 2;
            }
            AppDomain.CurrentDomain.AssemblyResolve += Runtime.Resolve;
            Runtime.PrepareNativeSearchPath(RuntimeDir);
            return Run(args, activate);
        }

        // Kept out of Main so the WebView2 assemblies are only needed after the resolver is set.
        [MethodImpl(MethodImplOptions.NoInlining)]
        static int Run(string[] args, EventWaitHandle activate)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (MainForm form = new MainForm(args, activate))
            {
                Application.Run(form);
                return form.ExitCode;
            }
        }

        public static string Hex(byte[] bytes)
        {
            StringBuilder text = new StringBuilder();
            foreach (byte b in bytes) text.Append(b.ToString("x2"));
            return text.ToString();
        }
    }

    // Embedded files: the Python service and the WebView2 libraries.
    static class Runtime
    {
        static readonly string[][] Files = new string[][]
        {
            new string[] { "service.exe", "MatchroomService.exe" },
            new string[] { "core.dll", "Microsoft.Web.WebView2.Core.dll" },
            new string[] { "winforms.dll", "Microsoft.Web.WebView2.WinForms.dll" },
            new string[] { "loader.dll", "WebView2Loader.dll" },
        };

        public static string ServiceExe;

        public static string Unpack(string dataDir)
        {
            Assembly self = Assembly.GetExecutingAssembly();
            string build;
            using (Stream service = self.GetManifestResourceStream("service.exe"))
            {
                // The folder name follows the service build, so a new build unpacks fresh files.
                byte[] hash = SHA256.Create().ComputeHash(service);
                build = Program.Hex(hash).Substring(0, 12);
            }
            string root = Path.Combine(dataDir, "runtime");
            string dir = Path.Combine(root, build);
            Directory.CreateDirectory(dir);
            foreach (string[] file in Files)
            {
                string target = Path.Combine(dir, file[1]);
                using (Stream source = self.GetManifestResourceStream(file[0]))
                {
                    if (File.Exists(target) && new FileInfo(target).Length == source.Length) continue;
                    string temporary = target + ".tmp";
                    using (FileStream output = File.Create(temporary)) source.CopyTo(output);
                    if (File.Exists(target)) File.Delete(target);
                    File.Move(temporary, target);
                }
            }
            ServiceExe = Path.Combine(dir, "MatchroomService.exe");
            CleanOldBuilds(root, build);
            return dir;
        }

        static void CleanOldBuilds(string root, string keep)
        {
            try
            {
                foreach (string old in Directory.GetDirectories(root))
                    if (!string.Equals(Path.GetFileName(old), keep, StringComparison.OrdinalIgnoreCase))
                        try { Directory.Delete(old, true); } catch (Exception) { }
            }
            catch (Exception) { }
        }

        public static Assembly Resolve(object sender, ResolveEventArgs args)
        {
            string name = new AssemblyName(args.Name).Name + ".dll";
            string path = Path.Combine(Program.RuntimeDir, name);
            return File.Exists(path) ? Assembly.LoadFrom(path) : null;
        }

        [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
        static extern bool SetDllDirectory(string path);

        public static void PrepareNativeSearchPath(string dir)
        {
            SetDllDirectory(dir); // WebView2Loader.dll is found here
        }
    }

    class MainForm : Form
    {
        readonly string[] args;
        readonly EventWaitHandle activate;
        readonly WebView2 web = new WebView2();
        readonly Label splash = new Label();
        Process service;
        string url;
        bool coreReady, navigated, closing, serviceFailed;
        public int ExitCode;

        static readonly Color Background = Color.FromArgb(15, 18, 21);

        public MainForm(string[] args, EventWaitHandle activate)
        {
            this.args = args;
            this.activate = activate;
            Text = Program.Title;
            BackColor = Background;
            MinimumSize = new Size(640, 480);
            StartPosition = FormStartPosition.Manual;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
            ApplyPlacement();

            splash.Text = "Matchroom Next";
            splash.Dock = DockStyle.Fill;
            splash.TextAlign = ContentAlignment.MiddleCenter;
            splash.ForeColor = Color.FromArgb(139, 150, 161);
            splash.Font = new Font("Bahnschrift", 14f, FontStyle.Regular);
            Controls.Add(splash);

            web.Dock = DockStyle.Fill;
            web.DefaultBackgroundColor = Background;
            web.Visible = false;
            Controls.Add(web);
            web.BringToFront();
            splash.BringToFront();
        }

        [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            // Dark title bar to match the app (Windows 10 2004+ / 11); ignored where unsupported.
            try
            {
                int dark = 1, caption = 15 | (18 << 8) | (21 << 16);
                DwmSetWindowAttribute(Handle, 20, ref dark, 4);
                DwmSetWindowAttribute(Handle, 35, ref caption, 4);
            }
            catch (Exception) { }
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            Thread listener = new Thread(delegate ()
            {
                while (activate.WaitOne()) { if (closing) return; BeginInvoke(new MethodInvoker(Raise)); }
            });
            listener.IsBackground = true;
            listener.Start();
            StartService();
            InitWebView();
        }

        // ---- placement -----------------------------------------------------
        void ApplyPlacement()
        {
            Rectangle bounds = new Rectangle(0, 0, 1360, 860);
            bool maximized = false, saved = false;
            try
            {
                string file = Path.Combine(Program.DataDir, "window.json");
                if (File.Exists(file))
                {
                    Dictionary<string, object> v = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(file));
                    Rectangle r = new Rectangle(Convert.ToInt32(v["x"]), Convert.ToInt32(v["y"]), Convert.ToInt32(v["width"]), Convert.ToInt32(v["height"]));
                    maximized = v.ContainsKey("maximized") && Convert.ToBoolean(v["maximized"]);
                    foreach (Screen screen in Screen.AllScreens)
                    {
                        Rectangle overlap = Rectangle.Intersect(screen.WorkingArea, r);
                        if (overlap.Width >= 200 && overlap.Height >= 120) { bounds = r; saved = true; break; }
                    }
                }
            }
            catch (Exception) { }
            if (!saved)
            {
                Rectangle area = Screen.PrimaryScreen.WorkingArea;
                bounds.Width = Math.Min(bounds.Width, area.Width - 40);
                bounds.Height = Math.Min(bounds.Height, area.Height - 40);
                bounds.X = area.Left + (area.Width - bounds.Width) / 2;
                bounds.Y = area.Top + (area.Height - bounds.Height) / 2;
            }
            Bounds = bounds;
            if (maximized) WindowState = FormWindowState.Maximized;
        }

        void SavePlacement()
        {
            try
            {
                Rectangle r = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                string json = "{\"x\":" + r.X + ",\"y\":" + r.Y + ",\"width\":" + r.Width + ",\"height\":" + r.Height +
                    ",\"maximized\":" + (WindowState == FormWindowState.Maximized ? "true" : "false") + "}";
                string file = Path.Combine(Program.DataDir, "window.json");
                File.WriteAllText(file + ".tmp", json);
                if (File.Exists(file)) File.Delete(file);
                File.Move(file + ".tmp", file);
            }
            catch (Exception) { }
        }

        void Raise()
        {
            if (IsDisposed) return;
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Show();
            // A topmost flip is the reliable way to come forward from the background.
            TopMost = true;
            TopMost = false;
            Activate();
        }

        // ---- service -------------------------------------------------------
        void StartService()
        {
            List<string> arguments = new List<string>();
            if (Program.Demo) arguments.Add("--demo"); // must come first: the service dispatches on it
            arguments.Add("--shell");
            arguments.Add("--parent-pid");
            arguments.Add(Process.GetCurrentProcess().Id.ToString());
            if (!Program.Demo)
            {
                arguments.Add("--no-tray");
                arguments.Add("--installer-path");
                arguments.Add(Quote(Application.ExecutablePath));
                arguments.Add("--data-dir");
                arguments.Add(Quote(Program.DataDir));
                foreach (string a in args) arguments.Add(Quote(a));
            }

            ProcessStartInfo info = new ProcessStartInfo(Runtime.ServiceExe, string.Join(" ", arguments.ToArray()));
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardInput = true;
            info.RedirectStandardOutput = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.WorkingDirectory = Program.DataDir;
            try
            {
                service = Process.Start(info);
            }
            catch (Exception ex)
            {
                Fail("Не удалось запустить службу Matchroom:\n" + ex.Message);
                return;
            }
            Thread reader = new Thread(ReadService);
            reader.IsBackground = true;
            reader.Start();
        }

        static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        void ReadService()
        {
            try
            {
                string line;
                while ((line = service.StandardOutput.ReadLine()) != null)
                {
                    string text = line;
                    if (text.StartsWith("READY ")) Post(delegate { url = text.Substring(6).Trim(); TryNavigate(); });
                    else if (text.StartsWith("OPEN ")) Post(delegate { OpenRoute(text.Substring(5).Trim()); });
                    else if (text.StartsWith("ERROR already-running"))
                    {
                        serviceFailed = true;
                        Post(delegate
                        {
                            Fail("Matchroom Next уже запущен: возможно, другая версия или окно в браузере.\n\nЗавершите её (Настройки → Завершить Next или Диспетчер задач) и откройте программу снова.");
                        });
                    }
                }
            }
            catch (Exception) { }
            service.WaitForExit();
            if (!closing && !serviceFailed)
            {
                Post(delegate
                {
                    if (service.ExitCode == 0) Close();
                    else Fail("Служба Matchroom остановилась (код " + service.ExitCode + ").\nПодробности: MatchroomNextData\\matchroom.log");
                });
            }
        }

        void Post(MethodInvoker action)
        {
            if (!IsDisposed && IsHandleCreated) BeginInvoke(action);
        }

        void OpenRoute(string route)
        {
            Raise();
            if (!navigated || !coreReady) return;
            string safe = Regex.Replace(route, "[^a-zA-Z0-9/_-]", "");
            if (safe.Length > 0) web.CoreWebView2.ExecuteScriptAsync("location.hash='#/" + safe + "'");
        }

        void Fail(string message)
        {
            if (closing) return;
            closing = true;
            ExitCode = 1;
            MessageBox.Show(this, message, Program.Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }

        // ---- web view ------------------------------------------------------
        async void InitWebView()
        {
            try
            {
                string profile = Path.Combine(Program.DataDir, "webview2");
                CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, profile, null);
                await web.EnsureCoreWebView2Async(environment);
            }
            catch (WebView2RuntimeNotFoundException)
            {
                if (MessageBox.Show(this, "Для окна нужен Microsoft Edge WebView2 Runtime. Он есть в Windows 11 и Edge, но здесь не найден.\n\nОткрыть страницу загрузки?",
                    Program.Title, MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
                    Process.Start("https://go.microsoft.com/fwlink/p/?LinkId=2124703");
                Fail("Окно не запущено без WebView2 Runtime.");
                return;
            }
            catch (Exception ex)
            {
                Fail("Не удалось создать окно: " + ex.Message);
                return;
            }

            CoreWebView2 core = web.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.IsSwipeNavigationEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.NewWindowRequested += delegate (object s, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; OpenExternal(e.Uri); };
            core.NavigationStarting += delegate (object s, CoreWebView2NavigationStartingEventArgs e)
            {
                if (!IsAppUrl(e.Uri)) { e.Cancel = true; OpenExternal(e.Uri); }
            };
            core.NavigationCompleted += delegate
            {
                if (!web.Visible) { web.Visible = true; splash.Visible = false; }
            };
            core.ProcessFailed += delegate (object s, CoreWebView2ProcessFailedEventArgs e)
            {
                if (e.ProcessFailedKind == CoreWebView2ProcessFailedKind.BrowserProcessExited ||
                    e.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessExited)
                    BeginInvoke(new MethodInvoker(delegate { if (!closing) web.CoreWebView2.Reload(); }));
            };
            coreReady = true;
            TryNavigate();
        }

        bool IsAppUrl(string uri)
        {
            if (uri.StartsWith("about:") || uri.StartsWith("blob:") || uri.StartsWith("data:")) return true;
            return url != null && uri.StartsWith(url, StringComparison.OrdinalIgnoreCase);
        }

        static void OpenExternal(string uri)
        {
            Uri parsed;
            if (Uri.TryCreate(uri, UriKind.Absolute, out parsed) && parsed.Scheme == Uri.UriSchemeHttps)
                try { Process.Start(parsed.AbsoluteUri); } catch (Exception) { }
        }

        void TryNavigate()
        {
            if (navigated || !coreReady || url == null) return;
            navigated = true;
            web.CoreWebView2.Navigate(url);
        }

        // ---- shutdown ------------------------------------------------------
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            SavePlacement();
            base.OnFormClosing(e);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            closing = true;
            Hide();
            try { activate.Set(); } catch (Exception) { }
            StopService();
            base.OnFormClosed(e);
        }

        void StopService()
        {
            if (service == null) return;
            try
            {
                if (!service.HasExited)
                {
                    try { service.StandardInput.Close(); } catch (Exception) { }
                    if (!service.WaitForExit(8000)) service.Kill();
                }
            }
            catch (Exception) { }
        }
    }
}
