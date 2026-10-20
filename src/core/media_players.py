"""Discover common installed media players without requiring a platform-specific package."""

import os
from pathlib import Path
import shutil
import sys


def discover_media_players():
    """Return (display name, launch path) pairs for common installed players."""
    found = {}

    def add(name, path):
        candidate = Path(path).expanduser()
        if candidate.exists():
            found[str(candidate).casefold()] = (name, str(candidate))

    if sys.platform == 'darwin':
        app_dirs = [Path('/Applications'), Path.home() / 'Applications']
        if Path('/System/Applications/QuickTime Player.app').exists():
            add('QuickTime Player', '/System/Applications/QuickTime Player.app')
        known = {
            'iina': 'IINA',
            'vlc': 'VLC',
            'mpv': 'mpv',
            'quicktime player': 'QuickTime Player',
        }
        for directory in app_dirs:
            try:
                for candidate in directory.iterdir():
                    label = candidate.stem.casefold()
                    if candidate.suffix == '.app' and label in known:
                        add(known[label], candidate)
            except OSError:
                continue
        for command, label in (('mpv', 'mpv'), ('vlc', 'VLC')):
            located = shutil.which(command)
            if located:
                add(label, located)
    elif os.name == 'nt':
        roots = [os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'),
                 os.environ.get('LOCALAPPDATA')]
        known = (
            ('VLC', ('VideoLAN', 'VLC', 'vlc.exe')),
            ('mpv', ('mpv', 'mpv.exe')),
            ('MPC-HC', ('MPC-HC', 'mpc-hc64.exe')),
            ('MPC-BE', ('MPC-BE x64', 'mpc-be64.exe')),
            ('PotPlayer', ('DAUM', 'PotPlayer', 'PotPlayerMini64.exe')),
        )
        for root in filter(None, roots):
            for label, parts in known:
                add(label, Path(root).joinpath(*parts))
        for command, label in (('vlc.exe', 'VLC'), ('mpv.exe', 'mpv'),
                               ('mpc-hc64.exe', 'MPC-HC'), ('mpc-be64.exe', 'MPC-BE')):
            located = shutil.which(command)
            if located:
                add(label, located)
        # Many Windows installers register their executable independently of its install folder.
        # Read both registry views so 32-bit and 64-bit player installs are discoverable.
        try:
            import winreg

            app_paths = (
                ('vlc.exe', 'VLC'), ('mpv.exe', 'mpv'),
                ('mpc-hc64.exe', 'MPC-HC'), ('mpc-hc.exe', 'MPC-HC'),
                ('mpc-be64.exe', 'MPC-BE'), ('PotPlayerMini64.exe', 'PotPlayer'),
                ('PotPlayerMini.exe', 'PotPlayer'),
            )
            hives = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
            views = (getattr(winreg, 'KEY_WOW64_64KEY', 0), getattr(winreg, 'KEY_WOW64_32KEY', 0))
            for hive in hives:
                for view in views:
                    for executable, label in app_paths:
                        key_name = rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}'
                        try:
                            with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                                registered, _ = winreg.QueryValueEx(key, None)
                            add(label, os.path.expandvars(registered.strip('"')))
                        except OSError:
                            continue
                    uninstall_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
                    try:
                        with winreg.OpenKey(hive, uninstall_path, 0, winreg.KEY_READ | view) as root:
                            count = winreg.QueryInfoKey(root)[0]
                            for index in range(count):
                                try:
                                    subkey_name = winreg.EnumKey(root, index)
                                    with winreg.OpenKey(root, subkey_name) as subkey:
                                        display_name = winreg.QueryValueEx(subkey, 'DisplayName')[0]
                                        try:
                                            icon = winreg.QueryValueEx(subkey, 'DisplayIcon')[0]
                                        except OSError:
                                            icon = ''
                                        try:
                                            install_location = winreg.QueryValueEx(subkey, 'InstallLocation')[0]
                                        except OSError:
                                            install_location = ''
                                except OSError:
                                    continue
                                title = str(display_name).casefold()
                                matches = (
                                    ('vlc', 'VLC', ('vlc.exe',)),
                                    ('mpv', 'mpv', ('mpv.exe',)),
                                    ('mpc-hc', 'MPC-HC', ('mpc-hc64.exe', 'mpc-hc.exe')),
                                    ('mpc-be', 'MPC-BE', ('mpc-be64.exe', 'mpc-be.exe')),
                                    ('potplayer', 'PotPlayer', ('PotPlayerMini64.exe', 'PotPlayerMini.exe')),
                                )
                                for token, label, executables in matches:
                                    if token in title:
                                        icon_path = os.path.expandvars(str(icon).split(',')[0].strip().strip('"'))
                                        if Path(icon_path).suffix.casefold() == '.exe' and Path(icon_path).is_file():
                                            add(label, icon_path)
                                        location = Path(os.path.expandvars(str(install_location))) if install_location else None
                                        if location and location.is_dir():
                                            for executable in executables:
                                                add(label, location / executable)
                                        break
                    except OSError:
                        continue
        except ImportError:
            pass
    else:
        for command, label in (('vlc', 'VLC'), ('mpv', 'mpv'), ('smplayer', 'SMPlayer'),
                               ('celluloid', 'Celluloid'), ('totem', 'Videos')):
            located = shutil.which(command)
            if located:
                add(label, located)

    return sorted(found.values(), key=lambda item: item[0].casefold())
