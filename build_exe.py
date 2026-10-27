"""Build the portable Next EXE without packaging user data.

Two steps: PyInstaller builds the Python service (a console program so the
shell can read its output), then the native shell is compiled with the
service and the WebView2 libraries embedded. The result is one file.
"""
from pathlib import Path
import hashlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'shell'))
VERSION = 'v0.10'
NAME = f'Matchroom Next {VERSION}'
SERVICE_NAME = 'MatchroomService'
# (source, destination inside the bundle). Client tests are not shipped.
RESOURCES = [
    ('web/index.html', 'web'), ('web/css', 'web/css'), ('web/js', 'web/js'),
    ('commander_names.json', '.'), ('ranked_watchlist.json', '.'), ('forts_icon.png', '.'),
    ('assets', 'assets'), ('lobby_native/NextLobbyBridge.exe', 'lobby_native'),
]


def build_service():
    subprocess.run([sys.executable, str(ROOT / 'lobby_native/build_helper.py')], check=True)
    out = ROOT / 'build_exe' / 'service'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onefile', '--console', '--name', SERVICE_NAME,
               '--distpath', str(out), '--workpath', str(ROOT / 'build_exe' / 'work'),
               '--specpath', str(ROOT / 'build_exe'),
               '--exclude-module', 'pystray', '--exclude-module', 'PIL', '--exclude-module', 'tkinter',
               '--hidden-import', 'app_server', '--hidden-import', 'app_window',
               '--hidden-import', 'windows_notify', '--hidden-import', 'steam_service']
    icon = ROOT / 'assets' / 'matchroom-next.ico'
    if icon.exists():
        command.extend(['--icon', str(icon)])
    for source, destination in RESOURCES:
        command.extend(['--add-data', str(ROOT / source) + ':' + destination])
    command.append(str(ROOT / 'desktop_app.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    return out / (SERVICE_NAME + '.exe')


def main():
    service = build_service()
    import build_shell
    executable = ROOT / 'dist' / (NAME + '.exe')
    executable.parent.mkdir(exist_ok=True)
    build_shell.build(service, executable)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (executable.parent / (NAME + '.sha256.txt')).write_text(
        digest + '  ' + executable.name + '\n', encoding='utf-8')
    print('Built:', executable)
    print('Size:', executable.stat().st_size, 'bytes')
    print('SHA-256:', digest)
    print('Unsigned portable build; Windows SmartScreen may warn.')


if __name__ == '__main__':
    main()
