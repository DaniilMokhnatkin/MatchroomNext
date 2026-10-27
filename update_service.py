import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen

REPOSITORY = 'DaniilMokhnatkin/MatchroomNext'
API = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
MAX_SIZE = 100 * 1024 * 1024


def version(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)(?:\.(\d+))?', value)
    if not match:
        raise ValueError('Invalid release version')
    return tuple(int(v or 0) for v in match.groups())


def check(current):
    request = Request(API, headers={'User-Agent': 'Matchroom-Next', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=15) as response:
        release = json.load(response)
    tag = release['tag_name']
    newer = version(tag) > version(current.removeprefix('next-'))
    if not newer:
        return {'available': False, 'version': tag}
    expected = f'Matchroom.Next.{tag}.exe'
    assets = [a for a in release['assets'] if a.get('name') == expected]
    if len(assets) != 1:
        raise ValueError('Release executable is missing')
    asset = assets[0]
    digest = asset.get('digest', '')
    url = asset.get('browser_download_url', '')
    prefix = f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
    if not url.startswith(prefix) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Release checksum or URL is invalid')
    if not 1024 <= asset.get('size', 0) <= MAX_SIZE:
        raise ValueError('Release size is invalid')
    return {'available': True, 'version': tag, 'url': url, 'digest': digest[7:], 'size': asset['size']}


def install(current, executable, parent_pid):
    if os.name != 'nt' or not executable or not parent_pid:
        raise ValueError('Updates require the portable Windows application')
    target = Path(executable).resolve()
    if target.suffix.lower() != '.exe' or not target.is_file():
        raise ValueError('Application executable not found')
    release = check(current)
    if not release['available']:
        raise ValueError('Already up to date')
    staged = target.with_name(target.name + '.update')
    digest = hashlib.sha256()
    try:
        req = Request(release['url'], headers={'User-Agent': 'Matchroom-Next'})
        with urlopen(req, timeout=30) as response, staged.open('wb') as output:
            total = 0
            while chunk := response.read(65536):
                total += len(chunk)
                if total > release['size']:
                    raise ValueError('Unexpected download size')
                digest.update(chunk)
                output.write(chunk)
        if total != release['size'] or digest.hexdigest() != release['digest']:
            raise ValueError('Update checksum mismatch')
        # EncodedCommand prevents paths from being interpreted as shell syntax.
        def quote(value): return "'" + str(value).replace("'", "''") + "'"
        script = f"""$ErrorActionPreference = 'Stop'
$target = {quote(target)}
$staged = {quote(staged)}
$backup = $target + '.previous'
try {{
  Wait-Process -Id {int(parent_pid)} -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Force }}
  Move-Item -LiteralPath $target -Destination $backup
  try {{ Move-Item -LiteralPath $staged -Destination $target }} catch {{ Move-Item -LiteralPath $backup -Destination $target; throw }}
  Start-Process -FilePath $target
}} catch {{
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show('Matchroom Next update failed: ' + $_.Exception.Message)
}}
"""
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                         creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return release['version']
