"""Explicit UI translations; user content and protocol identifiers stay intact."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import time

LANGUAGES = ('en', 'mixed')
_catalog = json.loads((Path(__file__).with_name('locales') / 'mixed.json').read_text())
_language = 'en'
_last_check = float('-inf')
_signature = None


def settings_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'i3-ascii-desktop/settings.json'


def get_language(force=False):
    global _language, _last_check, _signature
    override = os.environ.get('DESKTOP_LANGUAGE')
    if override in LANGUAGES:
        return override
    now = time.monotonic()
    if not force and now - _last_check < .25:
        return _language
    _last_check = now
    path = settings_path()
    try:
        stat = path.stat()
        signature = (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if signature != _signature:
            value = json.loads(path.read_text()).get('language', 'en')
            _language = value if value in LANGUAGES else 'en'
            _signature = signature
    except (OSError, ValueError, AttributeError):
        _language, _signature = 'en', None
    return _language


def set_language(language):
    if language not in LANGUAGES:
        raise ValueError('Language must be en or mixed')
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        values = json.loads(path.read_text())
        if not isinstance(values, dict):
            values = {}
    except (OSError, ValueError):
        values = {}
    values['language'] = language
    fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(values, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    get_language(force=True)


def tr(message):
    return _catalog.get(message, message) if get_language() == 'mixed' else message


def picker():
    """Standalone language picker for the desktop-language command."""
    import tkinter as tk
    root = tk.Tk(className='DesktopLanguage')
    root.title(tr('Language'))
    root.configure(bg='#0b0e15')
    root.resizable(False, False)
    tk.Label(root, text=tr('Interface language for all three widgets'), bg='#0b0e15', fg='#73daca', padx=24, pady=18).pack()
    def choose(language):
        set_language(language)
        root.destroy()
    for language, label in [('en', 'English'), ('mixed', tr('Mixed (current style)'))]:
        tk.Button(root, text=label, command=lambda language=language: choose(language), bg='#182533', fg='#73daca', padx=20, pady=8).pack(fill='x', padx=24, pady=6)
    tk.Label(root, text=tr('Saved automatically; updates within one second.'), bg='#0b0e15', fg='#737e9c', padx=24, pady=16).pack()
    root.mainloop()
