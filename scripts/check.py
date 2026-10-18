#!/usr/bin/env python3
"""Run offline behavior checks without touching a running desktop or hardware."""
from pathlib import Path
import py_compile
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    for directory, pattern in [('share/center-desktop', 'test_center.py'),
                               ('share/i3-show-desktop', 'test_desktop.py'),
                               ('services/fan-control', 'test_controller.py'),
                               ('tests', 'test_install.py')]:
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / directory), '-p', pattern, '-v'], check=True)
    for path in ROOT.rglob('*.py'):
        if not any(part in {'.venv', 'venv', '__pycache__', '.git'} for part in path.relative_to(ROOT).parts):
            py_compile.compile(str(path), doraise=True)
    for name in ('system-desktop', 'prague-weather', 'desktop-screen-timeout', 'i3-keyboard-layout'):
        py_compile.compile(str(ROOT / 'bin' / name), doraise=True)
    for name in ('center-desktop', 'i3-show-desktop'):
        subprocess.run(['sh', '-n', str(ROOT / 'bin' / name)], check=True)
    print('All offline checks passed.')


if __name__ == '__main__':
    main()
