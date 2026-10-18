#!/usr/bin/env python3
"""Install or link desktop applications while preserving personal runtime data."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home(), help='Target home directory; useful for staging')
    parser.add_argument('--skip-deps', action='store_true', help='Do not create or update the Python environment')
    parser.add_argument('--link', action='store_true', help='Run source directly from this checkout using symlinks')
    parser.add_argument('--link-config', action='store_true', help='Link the full reference i3, bar and monitor configs (backs up existing files)')
    parser.add_argument('--language', choices=['en', 'mixed'], help='Set the shared language; otherwise preserve the current preference')
    args = parser.parse_args()
    if args.link_config and not args.link:
        parser.error('--link-config requires --link')
    home = args.home.expanduser().resolve()
    local = home / '.local'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = local / 'share/i3-ascii-desktop/backups' / stamp
    count = 0

    def install(source, destination, link=False, executable=False):
        nonlocal count
        if link and destination.is_symlink() and destination.resolve() == source.resolve():
            return
        if destination.is_file() or destination.is_symlink():
            saved = backup / destination.relative_to(home)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
            count += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            destination.unlink()
        if link:
            destination.unlink(missing_ok=True)
            destination.symlink_to(source.resolve())
        else:
            shutil.copyfile(source, destination)
            destination.chmod(0o755 if executable else 0o644)

    for source in (ROOT / 'bin').iterdir():
        if source.is_file():
            install(source, local / 'bin' / source.name, args.link, True)
    for name in ('center-desktop', 'i3-show-desktop', 'desktop_common'):
        for source in (ROOT / 'share' / name).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                install(source, local / 'share' / source.relative_to(ROOT / 'share'), args.link)
    for source in (ROOT / 'config').iterdir():
        if source.is_file():
            install(source, local / 'share/i3-ascii-desktop/config' / source.name, args.link)
    if args.link_config:
        for name, destination in [('i3.conf', home / '.config/i3/config'),
                                  ('i3status-rust.toml', home / '.config/i3status-rust/config.toml'),
                                  ('monitors.sh', home / '.screenlayout/monitors.sh')]:
            install(ROOT / 'config' / name, destination, True)
    central = local / 'share/center-desktop'
    central.chmod(0o700)
    if not args.skip_deps:
        environment = ROOT / '.venv' if args.link else central / 'venv'
        if not (environment / 'bin/python').exists():
            subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
        subprocess.run([str(environment / 'bin/python'), '-m', 'pip', 'install', '-r', str(ROOT / 'share/center-desktop/requirements.lock')], check=True)
    if args.language:
        sys.path.insert(0, str(ROOT / 'share'))
        from desktop_common.i18n import set_language
        os.environ['XDG_CONFIG_HOME'] = str(home / '.config')
        set_language(args.language)
    print(f'Installed {"links" if args.link else "files"} in {local / "bin"}')
    if count:
        print(f'Backed up {count} replaced files in {backup}')
    if args.link_config:
        print('Linked full reference configuration. Reload i3 after validating it.')
    else:
        print(f'Config examples: {local / "share/i3-ascii-desktop/config"}')
        print('Merge the i3 snippet manually. Your active i3 config was not changed.')
    if args.skip_deps:
        print('Dependencies skipped; the central Python environment must already exist.')
    print('No service was started.')


if __name__ == '__main__':
    main()
