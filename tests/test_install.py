"""Verify menu/icon identity and data-safe uninstall in an isolated directory."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from storage import Store


class InstallationTests(unittest.TestCase):
    def test_icon_registration_migration_and_reinstall_preserve_notes(self):
        available = subprocess.run(
            ["/usr/bin/python3", "-c", "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"],
            capture_output=True)
        if available.returncode:
            self.skipTest("System GTK bindings are unavailable")
        source = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="justnotes install ") as directory:
            root = Path(directory)
            data, binary = root / "share with spaces", root / "bin"
            environment = dict(os.environ, XDG_DATA_HOME=str(data), JUSTNOTES_BIN_DIR=str(binary))
            old = data / "applications/justnotes.desktop"
            old.parent.mkdir(parents=True)
            old.write_text("old entry")
            subprocess.run([str(source / "install.sh")], env=environment, check=True, capture_output=True)
            app_id = "io.github.nightfury.justnotes"
            desktop = data / f"applications/{app_id}.desktop"
            self.assertTrue(desktop.exists())
            self.assertFalse(old.exists())
            self.assertIn(f"Icon={app_id}\n", desktop.read_text())
            self.assertTrue((data / f"icons/hicolor/scalable/apps/{app_id}.svg").exists())
            self.assertEqual((data / f"icons/hicolor/scalable/apps/{app_id}.svg").read_bytes(),
                             (source / "assets/justnotes.svg").read_bytes())
            self.assertTrue(os.access(binary / "justnotes", os.X_OK))
            self.assertTrue((data / "justnotes/application/system_theme.py").is_file())
            installed = data / "justnotes/application"
            self.assertTrue((installed / ".update-revision").is_file())
            for filename in ("app.py", "appearance.py", "system_theme.py", "storage.py", "formatting.py", "updates.py"):
                self.assertEqual((installed / filename).read_bytes(), (source / filename).read_bytes())
            # Exercise the installed controller, not modules imported from the
            # source tree: this catches testing a different copy than we ship.
            smoke = '''import app
from types import SimpleNamespace
from unittest.mock import Mock, patch
assert not hasattr(app.Editor, "toggle_theme")
assert not hasattr(app.Editor, "show_themes")
assert hasattr(app.Editor, "toggle_editor_theme")
view = SimpleNamespace(preferences={"font": None, "editor_theme": "light"},
                       css=app.Gtk.CssProvider(), marker_tag=app.Gtk.TextTag(), theme_button=Mock())
with patch("app.Gtk.Settings.get_default") as settings:
    app.Editor.apply_appearance(view)
settings.assert_not_called()
'''
            subprocess.run(["/usr/bin/python3", "-c", smoke], cwd=installed,
                           env=environment, check=True, capture_output=True)
            store = Store(data / "justnotes")
            note = store.create_note(text="**Keep my notes**")
            preferences = store.load_preferences()
            preferences["hide_markers"] = False
            store.save_preferences(preferences)
            unrelated = data / "applications/another-app.desktop"
            unrelated.write_text("preserve me")
            subprocess.run([str(source / "uninstall.sh")], env=environment, check=True, capture_output=True)
            self.assertFalse(desktop.exists())
            self.assertFalse((data / "justnotes/application").exists())
            self.assertFalse((binary / "justnotes").exists())
            self.assertEqual(unrelated.read_text(), "preserve me")
            self.assertEqual(Store(data / "justnotes").load_note(note["id"]), note)
            subprocess.run([str(source / "install.sh")], env=environment, check=True, capture_output=True)
            self.assertEqual(Store(data / "justnotes").load_note(note["id"]), note)
            self.assertEqual(Store(data / "justnotes").load_preferences(), preferences)
