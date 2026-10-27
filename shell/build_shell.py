"""Compile the native shell with the C# compiler that ships with Windows."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent


def compiler():
    return Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'


def build(service_exe, output, version='0.4.0.0'):
    """Embed the Python service and the WebView2 libraries into one EXE."""
    lib = ROOT / 'lib'
    framework = compiler().parent
    command = [
        str(compiler()), '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/langversion:5',
        '/out:' + str(output),
        '/win32icon:' + str(PROJECT / 'assets/matchroom-next.ico'),
        '/win32manifest:' + str(ROOT / 'app.manifest'),
        '/r:System.dll', '/r:System.Core.dll', '/r:System.Drawing.dll', '/r:System.Windows.Forms.dll',
        '/r:System.Web.Extensions.dll',
        '/r:' + str(lib / 'Microsoft.Web.WebView2.Core.dll'),
        '/r:' + str(lib / 'Microsoft.Web.WebView2.WinForms.dll'),
        '/resource:' + str(service_exe) + ',service.exe',
        '/resource:' + str(lib / 'Microsoft.Web.WebView2.Core.dll') + ',core.dll',
        '/resource:' + str(lib / 'Microsoft.Web.WebView2.WinForms.dll') + ',winforms.dll',
        '/resource:' + str(lib / 'WebView2Loader.dll') + ',loader.dll',
        '/lib:' + str(framework),
        str(ROOT / 'MatchroomShell.cs'),
    ]
    subprocess.run(command, check=True)
    return Path(output)
