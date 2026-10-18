#!/usr/bin/env python3
"""Install the optional root fan service for the documented reference hardware."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import pwd
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True, help='Local account permitted to control fans')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run this optional installer as root')
    uid = pwd.getpwnam(args.user).pw_uid
    if uid == 0:
        parser.error('Choose the desktop user, not root')
    backup = Path('/var/backups/i3-ascii-desktop') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    sources = ROOT / 'services/fan-control'
    content = {
        Path('/usr/local/lib/desktop-fan-control.py'): (sources / 'daemon.py').read_text(),
        Path('/etc/systemd/system/desktop-fan-control.service'): (sources / 'desktop-fan-control.service').read_text().replace('@USER_UID@', str(uid)),
        Path('/etc/modules-load.d/desktop-sensors.conf'): 'nct6775\n',
    }
    for destination, text in content.items():
        if destination.exists() or destination.is_symlink():
            saved = backup / destination.relative_to('/')
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            destination.unlink()
        destination.write_text(text)
        destination.chmod(0o644)
        os.chown(destination, 0, 0)
    print('Installed root-owned service files. The service has not been started.')
    print('Next: systemctl daemon-reload')
    print('On the supported hardware: modprobe nct6775')
    print('Then: systemctl enable --now desktop-fan-control.service')


if __name__ == '__main__':
    main()
