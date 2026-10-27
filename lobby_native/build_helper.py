"""Compile only the native lobby backend, never the old WPF interface."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
SOURCES = ['LobbyCore.cs', 'LobbyActions.cs', 'LobbyHistory.cs',
           'LobbyNotifications.cs', 'SteamLobbyReader.cs', 'ToastHost.cs', 'NextLobbyBridge.cs']


def compiler():
    return Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'


def references():
    # Toasts use WinRT through metadata and facades that ship with Windows;
    # nothing is downloaded or installed.
    windows = Path(os.environ['WINDIR'])
    framework = windows / 'Microsoft.NET/Framework64/v4.0.30319'
    return (['/r:System.Core.dll', '/r:System.Web.Extensions.dll'] +
            ['/r:' + str(windows / 'System32/WinMetadata' / name)
             for name in ('Windows.Foundation.winmd', 'Windows.Data.winmd', 'Windows.UI.winmd')] +
            ['/r:' + str(framework / name) for name in (
                'System.Runtime.dll', 'System.Runtime.WindowsRuntime.dll',
                'System.Runtime.InteropServices.WindowsRuntime.dll')])


def build():
    subprocess.run([str(compiler()), '/nologo', '/target:exe', '/platform:x64', '/optimize+',
                    '/main:FortsLobbyWatch.Program', '/out:' + str(ROOT / 'NextLobbyBridge.exe')] +
                   references() + [str(ROOT / name) for name in SOURCES], check=True)
    return ROOT / 'NextLobbyBridge.exe'


if __name__ == '__main__':
    print(build())
