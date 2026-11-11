import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storage import StorageError, Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root)

    def test_immediate_persistence_restart_and_empty_note(self):
        note = self.store.create_note()
        self.assertEqual(note["title"], "note1")
        self.assertEqual(Store(self.root).load_note(note["id"])["text"], "")
        for text in ("a", "ab", "", "こんにちは 🌙\nاردو café"):
            note["text"] = text
            self.store.save_note(note)
            self.assertEqual(Store(self.root).load_note(note["id"]), note)

    def test_duplicate_titles_do_not_collide(self):
        first = self.store.create_note(text="first", title="same")
        second = self.store.create_note(text="second", title="same")
        first["title"] = "renamed / ../ 🌿"
        self.store.save_note(first)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.load_note(first["id"]), first)
        self.assertEqual(self.store.load_note(second["id"]), second)
        self.assertEqual(len(self.store.list_notes()), 2)

    def test_delete_removes_primary_and_backup_without_resurrecting_on_restart(self):
        note = self.store.create_note(text="latest", title="same title")
        other = self.store.create_note(text="keep", title="same title")
        archive = self.store.delete_note(note["id"])
        self.assertEqual(Store(self.root).list_notes(), [other])
        self.assertFalse((self.root / "notes" / f'{note["id"]}.json').exists())
        self.assertFalse((self.root / "notes" / f'{note["id"]}.json.bak').exists())
        self.assertEqual(json.loads((archive / f'{note["id"]}.json').read_text())["text"], "latest")
        self.assertTrue((archive / f'{note["id"]}.json.bak').exists())
        with self.assertRaises(StorageError):
            self.store.load_note(note["id"])

    def test_delete_backup_only_or_corrupt_primary_notes(self):
        for corrupt in (False, True):
            note = self.store.create_note(text="recoverable")
            path = self.root / "notes" / f'{note["id"]}.json'
            if corrupt:
                path.write_text("broken")
            else:
                path.unlink()
            archive = self.store.delete_note(note["id"])
            self.assertEqual(Store(self.root).list_notes(), [])
            self.assertEqual(json.loads((archive / path.name).read_text())["text"], "recoverable")

    def test_failed_delete_restores_note_and_recovery_copy(self):
        note = self.store.create_note(text="keep this")
        path = self.root / "notes" / f'{note["id"]}.json'
        original_replace = os.replace

        def fail_primary_move(source, destination):
            if Path(source) == path:
                raise PermissionError("cannot move note")
            return original_replace(source, destination)

        with patch("storage.os.replace", side_effect=fail_primary_move):
            with self.assertRaisesRegex(StorageError, "cannot move note"):
                self.store.delete_note(note["id"])
        self.assertEqual(Store(self.root).load_note(note["id"]), note)
        self.assertTrue(path.with_suffix(".json.bak").exists())

    def test_failed_delete_sync_keeps_note(self):
        note = self.store.create_note(text="keep this")
        with patch("storage.os.fsync", side_effect=OSError("I/O error")):
            with self.assertRaises(StorageError):
                self.store.delete_note(note["id"])
        self.assertEqual(Store(self.root).load_note(note["id"]), note)

    def test_late_delete_failure_rolls_back_both_moved_files(self):
        note = self.store.create_note(text="keep this")
        from storage import _sync_directory

        def fail_archive_sync(path):
            if Path(path).parent == self.root / "trash":
                raise OSError("archive sync failed")
            return _sync_directory(path)

        with patch("storage._sync_directory", side_effect=fail_archive_sync):
            with self.assertRaisesRegex(StorageError, "archive sync failed"):
                self.store.delete_note(note["id"])
        self.assertEqual(Store(self.root).load_note(note["id"]), note)
        self.assertTrue((self.root / "notes" / f'{note["id"]}.json.bak').exists())

    def test_next_available_default_number(self):
        self.store.create_note(title="note1")
        self.store.create_note(title="note3")
        self.assertEqual(self.store.create_note()["title"], "note2")
        self.assertEqual(self.store.create_note()["title"], "note4")

    def test_note_date_uses_real_file_timestamp_and_backup_when_missing(self):
        note = self.store.create_note()
        path = self.root / "notes" / f'{note["id"]}.json'
        os.utime(path, (1000, 1000))
        self.assertEqual(self.store.note_modified(note["id"]), 1000)
        os.utime(path.with_suffix(".json.bak"), (500, 500))
        path.unlink()
        self.assertEqual(self.store.note_modified(note["id"]), 500)

    def test_previous_valid_version_is_retained(self):
        note = self.store.create_note(text="before")
        note["text"] = "after"
        self.store.save_note(note)
        path = self.root / "notes" / f'{note["id"]}.json.bak'
        self.assertEqual(json.loads(path.read_text())["text"], "before")

    def test_corrupt_primary_recovers_and_does_not_poison_backup(self):
        note = self.store.create_note(text="recover me")
        path = self.root / "notes" / f'{note["id"]}.json'
        path.write_bytes(b"\xffbroken")
        restarted = Store(self.root)
        recovered = restarted.load_note(note["id"])
        self.assertEqual(recovered, note)
        self.assertEqual(restarted.recovered, [path.name])
        recovered["text"] = "new version"
        restarted.save_note(recovered)
        self.assertEqual(json.loads(path.with_suffix(".json.bak").read_text())["text"], "recover me")
        self.assertEqual(Store(self.root).load_note(note["id"])["text"], "new version")

    def test_missing_primary_discovered_from_initial_backup(self):
        note = self.store.create_note(text="initial copy")
        (self.root / "notes" / f'{note["id"]}.json').unlink()
        self.assertEqual(Store(self.root).list_notes(), [note])

    def test_no_valid_recovery_reports_error_without_overwriting(self):
        note = self.store.create_note()
        path = self.root / "notes" / f'{note["id"]}.json'
        path.write_text("broken")
        path.with_suffix(".json.bak").write_text("also broken")
        with self.assertRaises(StorageError):
            self.store.list_notes()
        with self.assertRaises(StorageError):
            self.store.save_note(note)
        self.assertEqual(path.read_text(), "broken")

    def test_failed_atomic_replace_keeps_original_and_cleans_temporary(self):
        note = self.store.create_note(text="saved")
        note["text"] = "still in editor"
        original_replace = os.replace

        def fail_primary(source, destination):
            if str(destination).endswith(".json"):
                raise OSError("disk full")
            return original_replace(source, destination)

        with patch("storage.os.replace", side_effect=fail_primary):
            with self.assertRaisesRegex(StorageError, "disk full"):
                self.store.save_note(note)
        self.assertEqual(note["text"], "still in editor")
        self.assertEqual(self.store.load_note(note["id"])["text"], "saved")
        self.assertEqual(list((self.root / "notes").glob(".justnotes-*")), [])
        self.store.save_note(note)
        self.assertEqual(Store(self.root).load_note(note["id"]), note)

    def test_fsync_failure_is_reported_and_primary_stays_valid(self):
        note = self.store.create_note(text="saved")
        note["text"] = "unsaved"
        with patch("storage.os.fsync", side_effect=OSError("I/O error")):
            with self.assertRaises(StorageError):
                self.store.save_note(note)
        self.assertEqual(self.store.load_note(note["id"])["text"], "saved")
        self.assertEqual(list((self.root / "notes").glob(".justnotes-*")), [])

    def test_backup_failure_does_not_replace_primary(self):
        note = self.store.create_note(text="saved")
        note["text"] = "unsaved"
        with patch.object(self.store, "_replace", side_effect=PermissionError("read only")):
            with self.assertRaisesRegex(StorageError, "read only"):
                self.store.save_note(note)
        self.assertEqual(self.store.load_note(note["id"])["text"], "saved")

    def test_unchanged_preferences_skip_disk_writes_and_keep_recovery(self):
        preferences = self.store.load_preferences()
        self.store.save_preferences(preferences)
        before = (self.root / "preferences.json.bak").read_bytes()
        with patch("storage.os.fsync") as sync, patch.object(self.store, "_replace") as replace:
            self.store.save_preferences(preferences)
            replace.assert_not_called()
            sync.assert_not_called()
        self.assertEqual((self.root / "preferences.json.bak").read_bytes(), before)

    def test_unchanged_save_repairs_missing_or_corrupt_backup(self):
        preferences = self.store.load_preferences()
        self.store.save_preferences(preferences)
        backup = self.root / "preferences.json.bak"
        for corrupt in (False, True):
            if corrupt:
                backup.write_text("broken")
            else:
                backup.unlink()
            self.store.save_preferences(preferences)
            self.assertEqual(json.loads(backup.read_text()), preferences)

    def test_transparency_survives_restart_and_validates_range(self):
        preferences = self.store.load_preferences()
        self.assertEqual(preferences["transparency"], 40)
        for value in (0, 67, 100):
            preferences["transparency"] = value
            self.store.save_preferences(preferences)
            self.assertEqual(Store(self.root).load_preferences()["transparency"], value)
        for invalid in (-1, 101, True, 12.5, "40", None):
            with self.subTest(invalid=invalid), self.assertRaises(StorageError):
                self.store.save_preferences(dict(preferences, transparency=invalid))
        del preferences["transparency"]
        self.store.save_preferences(preferences)
        self.assertEqual(Store(self.root).load_preferences()["transparency"], 40)

    def test_preferences_restart_and_recovery(self):
        preferences = self.store.load_preferences()
        self.assertNotIn("theme", preferences)
        preferences.update(width=1120, height=780,
                           last_note=self.store.create_note()["id"])
        self.store.save_preferences(preferences)
        self.assertEqual(Store(self.root).load_preferences(), preferences)
        (self.root / "preferences.json").write_text("broken")
        self.assertEqual(Store(self.root).load_preferences(), preferences)

    def test_window_placement_survives_restart_and_validates_coordinates(self):
        preferences = self.store.load_preferences()
        preferences.update(width=950, height=700, geometry_version=1,
                           x=-1200, y=75, maximized=True)
        self.store.save_preferences(preferences)
        self.assertEqual(Store(self.root).load_preferences(), preferences)
        for invalid in (dict(x="wrong"), dict(y=True), dict(maximized=1), dict(geometry_version=2)):
            with self.subTest(invalid=invalid), self.assertRaises(StorageError):
                self.store.save_preferences(dict(preferences, **invalid))

    def test_open_tabs_and_tab_visibility_survive_restart(self):
        first, second = self.store.create_note(), self.store.create_note()
        preferences = self.store.load_preferences()
        preferences.update(open_tabs=[first["id"], second["id"]], last_note=second["id"], always_show_tabs=True)
        self.store.save_preferences(preferences)
        self.assertEqual(Store(self.root).load_preferences(), preferences)

    def test_invalid_or_duplicate_tab_ids_are_rejected(self):
        preferences = self.store.load_preferences()
        for tabs in (["../elsewhere"], ["a" * 32, "a" * 32], [None], "not a list"):
            with self.subTest(tabs=tabs), self.assertRaises(StorageError):
                self.store.save_preferences(dict(preferences, open_tabs=tabs))
    def test_font_preference_defaults_persists_and_resets(self):
        preferences = self.store.load_preferences()
        self.assertIsNone(preferences["font"])
        preferences["font"] = "DejaVu Sans Mono 14"
        self.store.save_preferences(preferences)
        self.assertEqual(Store(self.root).load_preferences()["font"], "DejaVu Sans Mono 14")
        preferences["font"] = None
        self.store.save_preferences(preferences)
        self.assertIsNone(Store(self.root).load_preferences()["font"])

    def test_marker_visibility_persists_and_older_preferences_default_to_hidden(self):
        preferences = self.store.load_preferences()
        self.assertTrue(preferences["hide_markers"])
        preferences["hide_markers"] = False
        self.store.save_preferences(preferences)
        self.assertFalse(Store(self.root).load_preferences()["hide_markers"])
        del preferences["hide_markers"]
        self.store.save_preferences(preferences)
        self.assertTrue(Store(self.root).load_preferences()["hide_markers"])

    def test_existing_preferences_without_font_use_system_default(self):
        preferences = self.store.load_preferences()
        del preferences["font"]
        self.store.save_preferences(preferences)
        loaded = Store(self.root).load_preferences()
        self.assertIsNone(loaded["font"])
        self.assertEqual(loaded["width"], preferences["width"])

    def test_legacy_theme_override_is_removed_without_losing_other_preferences(self):
        preferences = self.store.load_preferences()
        del preferences["editor_theme"]
        preferences.update(theme="dark", font="Sans 14", hide_markers=False)
        self.store.save_preferences(preferences)
        loaded = Store(self.root).load_preferences()
        self.assertNotIn("theme", loaded)
        self.assertEqual(loaded["editor_theme"], "dark")
        self.assertEqual(loaded["font"], "Sans 14")
        self.assertFalse(loaded["hide_markers"])

    def test_xdg_data_home(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root)}):
            self.assertEqual(Store().notes_dir, self.root / "justnotes/notes")

    def test_single_writer_lock(self):
        first = Store(self.root, lock=True)
        try:
            with self.assertRaisesRegex(StorageError, "already running"):
                Store(self.root, lock=True)
        finally:
            first.close()
        second = Store(self.root, lock=True)
        second.close()

    def test_invalid_id_cannot_escape_data_directory(self):
        for value in ("../elsewhere", "", None, "a" * 33):
            with self.assertRaises(StorageError):
                self.store.load_note(value)

    def test_mismatched_id_is_rejected(self):
        note = self.store.create_note()
        path = self.root / "notes" / f'{note["id"]}.json'
        note["id"] = "f" * 32
        path.write_text(json.dumps(note))
        with self.assertRaisesRegex(StorageError, "does not match"):
            self.store.list_notes()


if __name__ == "__main__":
    unittest.main()
