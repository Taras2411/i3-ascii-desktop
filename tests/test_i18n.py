"""Live preference reloads, explicit UI translation and stable machine values."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'share'))
sys.path.insert(0, str(ROOT / 'share/center-desktop'))
from desktop_common import i18n
import app
from arch_motion import ArchMotion

loader = importlib.machinery.SourceFileLoader('weather', str(ROOT / 'bin/prague-weather'))
spec = importlib.util.spec_from_loader(loader.name, loader)
weather = importlib.util.module_from_spec(spec)
loader.exec_module(weather)


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.env.start()
        self.override = os.environ.pop('DESKTOP_LANGUAGE', None)
        i18n.get_language(force=True)

    def tearDown(self):
        if self.override is not None:
            os.environ['DESKTOP_LANGUAGE'] = self.override
        self.env.stop()
        self.temp.cleanup()
        i18n.get_language(force=True)

    def test_default_english_and_unknown_user_content(self):
        self.assertEqual(i18n.get_language(force=True), 'en')
        self.assertEqual(i18n.tr('[ GOOGLE / CALENDAR ]'), '[ GOOGLE / CALENDAR ]')
        self.assertEqual(i18n.tr('A user event / a song title'), 'A user event / a song title')

    def test_atomic_preference_preserves_other_fields_and_reloads_external_change(self):
        path = i18n.settings_path()
        path.parent.mkdir(parents=True)
        path.write_text('{"other": 42}')
        i18n.set_language('mixed')
        self.assertEqual(json.loads(path.read_text()), {'other': 42, 'language': 'mixed'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(i18n.tr('CLOTHING'), 'ОДЕЖДА')
        path.write_text('{"language":"en"}')
        self.assertEqual(i18n.get_language(force=True), 'en')
        self.assertEqual(i18n.tr('CLOTHING'), 'CLOTHING')

    def test_invalid_preferences_and_invalid_selection(self):
        path = i18n.settings_path()
        path.parent.mkdir(parents=True)
        for text in ('{', '[]', '{"language":"invalid"}'):
            path.write_text(text)
            self.assertEqual(i18n.get_language(force=True), 'en')
        with self.assertRaises(ValueError):
            i18n.set_language('invalid')

    def test_environment_override_does_not_overwrite_saved_choice(self):
        i18n.set_language('mixed')
        with patch.dict(os.environ, {'DESKTOP_LANGUAGE': 'en'}):
            self.assertEqual(i18n.get_language(force=True), 'en')
        self.assertEqual(i18n.get_language(force=True), 'mixed')

    def test_arch_machine_modes_are_not_translated_as_dictionary_keys(self):
        panel = app.App.__new__(app.App)
        panel.text = Mock()
        panel.arch = ArchMotion()
        panel.mesh = None
        for language in ('en', 'mixed'):
            i18n.set_language(language)
            with patch.object(app, 'render_arch', return_value='ASCII'):
                for mode in ('auto', 'drag', 'inertia', 'return', 'paused'):
                    panel.arch.mode = mode
                    panel.draw_arch()
                    self.assertEqual(panel.arch.mode, mode)
            state = panel.text.call_args.args[3]
            self.assertIn('пауза' if language == 'mixed' else 'paused', state)

    def test_weather_translates_labels_and_keeps_condition_keys_stable(self):
        for language in ('en', 'mixed'):
            i18n.set_language(language)
            kind, label, _ = weather.condition(0)
            self.assertEqual(kind, 'sun')
            self.assertEqual(label, 'Ясно' if language == 'mixed' else 'Clear')
            self.assertEqual(i18n.tr('step '), 'шаг ' if language == 'mixed' else 'step ')


if __name__ == '__main__':
    unittest.main()
