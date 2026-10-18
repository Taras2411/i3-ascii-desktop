"""Protect existing user data and verify the distributable installation layout."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def install(self, home):
        subprocess.run([sys.executable, str(ROOT / 'install.py'), '--home', str(home), '--skip-deps'], check=True, capture_output=True, text=True)

    def test_staging_has_all_apps_and_does_not_apply_i3_config(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            self.install(home)
            for name in ('center-desktop', 'system-desktop', 'prague-weather', 'i3-show-desktop', 'i3-keyboard-layout', 'desktop-screen-timeout'):
                script = home / '.local/bin' / name
                self.assertTrue(script.is_file())
                self.assertTrue(script.stat().st_mode & 0o100)
            self.assertTrue((home / '.local/share/center-desktop/GOOGLE-SETUP.html').is_file())
            self.assertFalse((home / '.config/i3/config').exists())
            self.assertFalse((home / '.local/share/center-desktop/google-client.json').exists())

    def test_updates_preserve_private_state_and_backup_replaced_source(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            self.install(home)
            source = home / '.local/share/center-desktop/app.py'
            state = source.with_name('state.json')
            state.write_text('{"tasks": ["keep me"]}')
            source.write_text('# previous source\n')
            self.install(home)
            self.assertEqual(state.read_text(), '{"tasks": ["keep me"]}')
            copies = list((home / '.local/share/i3-ascii-desktop/backups').glob('*/.local/share/center-desktop/app.py'))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_text(), '# previous source\n')
            self.assertNotEqual(source.read_text(), '# previous source\n')

    def test_destination_symlink_does_not_overwrite_its_target(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            binary = home / '.local/bin/system-desktop'
            binary.parent.mkdir(parents=True)
            other = home / 'unrelated-file'
            other.write_text('keep this')
            binary.symlink_to(other)
            self.install(home)
            self.assertEqual(other.read_text(), 'keep this')
            self.assertFalse(binary.is_symlink())

    def test_link_mode_uses_checkout_and_preserves_config_and_state_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            config = home / '.config/i3/config'
            config.parent.mkdir(parents=True)
            config.write_text('# previous config\n')
            state = home / '.local/share/center-desktop/state.json'
            state.parent.mkdir(parents=True)
            state.write_text('{"tasks": ["keep me"]}')
            subprocess.run([sys.executable, str(ROOT / 'install.py'), '--home', str(home), '--skip-deps', '--link', '--link-config', '--language', 'mixed'], check=True, capture_output=True)
            self.assertEqual(config.resolve(), ROOT / 'config/i3.conf')
            self.assertEqual((home / '.local/bin/system-desktop').resolve(), ROOT / 'bin/system-desktop')
            self.assertEqual((state.with_name('app.py')).resolve(), ROOT / 'share/center-desktop/app.py')
            self.assertEqual(state.read_text(), '{"tasks": ["keep me"]}')
            backups = list((home / '.local/share/i3-ascii-desktop/backups').glob('*/.config/i3/config'))
            self.assertEqual(backups[0].read_text(), '# previous config\n')
            self.assertEqual((home / '.config/i3-ascii-desktop/settings.json').read_text().strip(), '{\n  "language": "mixed"\n}')


if __name__ == '__main__':
    unittest.main()
