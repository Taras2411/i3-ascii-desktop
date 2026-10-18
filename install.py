#!/usr/bin/env python3
"""Install user applications; leave i3 configuration and running services unchanged."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home(), help='Target home directory (also useful for staging)')
    parser.add_argument('--skip-deps', action='store_true', help='Copy files without creating or updating the central Python environment')
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    local = home / '.local'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = local / 'share/i3-ascii-desktop/backups' / stamp
    count = 0

    def copy(source, destination, executable=False):
        nonlocal count
        if destination.is_file() or destination.is_symlink():
            saved = backup / destination.relative_to(local)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
            count += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Do not follow an existing destination symlink when installing executables.
        if destination.is_symlink():
            destination.unlink()
        shutil.copyfile(source, destination)
        destination.chmod(0o755 if executable else 0o644)

    for source in (ROOT / 'bin').iterdir():
        copy(source, local / 'bin' / source.name, True)
    for name in ('center-desktop', 'i3-show-desktop'):
        target = local / 'share' / name
        for source in (ROOT / 'share' / name).iterdir():
            if source.is_file() and source.suffix != '.pyc':
                copy(source, target / source.name)
    for source in (ROOT / 'config').iterdir():
        copy(source, local / 'share/i3-ascii-desktop/config' / source.name)
    central = local / 'share/center-desktop'
    central.chmod(0o700)
    if not args.skip_deps:
        environment = central / 'venv'
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
        subprocess.run([str(environment / 'bin/python'), '-m', 'pip', 'install', '-r', str(central / 'requirements.lock')], check=True)
    print(f'Installed scripts in {local / "bin"}')
    print(f'Config examples: {local / "share/i3-ascii-desktop/config"}')
    if count:
        print(f'Backed up {count} replaced files in {backup}')
    if args.skip_deps:
        print('Dependencies skipped: center-desktop needs its venv before launch.')
    print('Merge the i3 snippet manually, then reload i3. No service was started.')


if __name__ == '__main__':
    main()
