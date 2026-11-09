"""Exercise real GTK text editing without requiring a window or a display."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    from app import Editor, Gdk, GLib, Gtk
except (ImportError, ValueError):
    Editor = None

from storage import StorageError, Store


class BufferEditor:
    """Use the editor controller with a real GTK buffer and stub window chrome."""

    if Editor is not None:
        for name in ("text", "save_current", "save_preferences", "flush", "title_changed",
                     "text_changed", "restyle", "snapshot", "record_history", "begin_action",
                     "end_action", "undo", "load_editor", "activate_note", "new_note",
                     "apply_format", "close_requested", "open_note", "import_file", "key_pressed",
                     "set_editor_font", "choose_font"):
            locals()[name] = getattr(Editor, name)

    def __init__(self, store):
        self.store = store
        self.note = store.create_note()
        self.preferences = store.load_preferences()
        self.preferences["last_note"] = self.note["id"]
        self.loading = False
        self.dirty = False
        self.preferences_dirty = False
        self.history = []
        self.history_index = -1
        self.user_action_depth = 0
        self.restoring = False
        self.style_source = 0
        self.popovers = []
        self.title_entry = Mock()
        self.title_entry.get_text.return_value = self.note["title"]
        self.view = Mock()
        self.error_bar = Mock()
        self.report = Mock()
        self.apply_theme = Mock()
        self.buffer = Gtk.TextBuffer()
        for tag in ("bold", "italic", "marker", *(f"heading{i}" for i in range(1, 7))):
            self.buffer.create_tag(tag)
        self.buffer.connect("changed", self.text_changed)
        self.buffer.connect("begin-user-action", self.begin_action)
        self.buffer.connect("end-user-action", self.end_action)
        self.record_history()


@unittest.skipIf(Editor is None, "System PyGObject / GTK 3 is unavailable")
class EditingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name))
        self.editor = BufferEditor(self.store)
        self.addCleanup(self.clear_idle)

    def clear_idle(self):
        if self.editor.style_source:
            GLib.source_remove(self.editor.style_source)
            self.editor.style_source = 0

    def type_text(self, text):
        self.editor.buffer.begin_user_action()
        self.editor.buffer.insert_at_cursor(text)
        self.editor.buffer.end_user_action()

    def test_typing_persists_at_end_of_each_edit(self):
        self.type_text("Hello 🌙")
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "Hello 🌙")
        self.assertFalse(self.editor.dirty)

    def test_undo_redo_and_branch_persist(self):
        self.type_text("first")
        self.type_text(" second")
        self.editor.undo()
        self.assertEqual(self.editor.text(), "first")
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "first")
        self.editor.undo(redo=True)
        self.assertEqual(self.editor.text(), "first second")
        self.editor.undo()
        self.type_text(" new")
        self.editor.undo(redo=True)
        self.assertEqual(self.editor.text(), "first new")

    def test_formatting_is_one_undo_action(self):
        self.type_text("hello")
        self.editor.buffer.select_range(self.editor.buffer.get_start_iter(), self.editor.buffer.get_end_iter())
        self.editor.apply_format(marker="**")
        self.assertEqual(self.editor.text(), "**hello**")
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "**hello**")
        self.editor.undo()
        self.assertEqual(self.editor.text(), "hello")
        self.editor.undo(redo=True)
        self.assertEqual(self.editor.text(), "**hello**")

    def test_formatting_empty_selection_places_caret_inside_markers(self):
        self.editor.apply_format(marker="**")
        self.assertEqual(self.editor.text(), "****")
        self.assertEqual(self.editor.buffer.get_iter_at_mark(self.editor.buffer.get_insert()).get_offset(), 2)
        self.type_text("word")
        self.assertEqual(self.editor.text(), "**word**")

    def test_styling_does_not_change_saved_text_or_undo_history(self):
        self.type_text("# Title\n**bold** and *italic*")
        before = self.editor.text()
        count = len(self.editor.history)
        self.clear_idle()
        self.editor.restyle()
        bold = self.editor.buffer.get_tag_table().lookup("bold")
        self.assertTrue(self.editor.buffer.get_iter_at_offset(11).has_tag(bold))
        self.assertEqual(self.editor.text(), before)
        self.assertEqual(len(self.editor.history), count)

    def test_failed_save_preserves_text_and_blocks_close_and_navigation(self):
        original_id = self.editor.note["id"]
        other = self.store.create_note(text="other")
        with patch.object(self.store, "save_note", side_effect=StorageError("disk full")):
            self.type_text("keep this")
            self.assertEqual(self.editor.text(), "keep this")
            self.assertTrue(self.editor.dirty)
            self.assertTrue(self.editor.close_requested())
            self.editor.open_note(other["id"])
            self.editor.new_note()
            self.assertEqual(self.editor.note["id"], original_id)
            self.assertEqual(len(self.store.list_notes()), 2)
            self.editor.report.assert_called()
        self.assertTrue(self.editor.flush())
        self.assertEqual(self.store.load_note(original_id)["text"], "keep this")
        self.assertFalse(self.editor.close_requested())

    def test_title_changes_persist(self):
        self.editor.title_entry.get_text.return_value = "new title 🌿"
        self.editor.title_changed()
        self.assertEqual(self.store.load_note(self.editor.note["id"])["title"], "new title 🌿")

    def test_font_change_preserves_text_and_undo_history_and_can_reset(self):
        self.type_text("first")
        self.type_text(" second")
        self.editor.set_editor_font("DejaVu Sans Mono 14")
        self.assertEqual(self.store.load_preferences()["font"], "DejaVu Sans Mono 14")
        self.assertEqual(self.editor.text(), "first second")
        self.editor.undo()
        self.assertEqual(self.editor.text(), "first")
        self.editor.set_editor_font(None)
        self.assertIsNone(self.store.load_preferences()["font"])
        self.editor.undo(redo=True)
        self.assertEqual(self.editor.text(), "first second")

    def test_font_save_failure_is_reported_and_can_be_retried(self):
        with patch.object(self.store, "save_preferences", side_effect=StorageError("disk full")):
            self.editor.set_editor_font("Sans 14")
            self.assertTrue(self.editor.preferences_dirty)
            self.editor.report.assert_called()
        self.assertTrue(self.editor.flush())
        self.assertEqual(self.store.load_preferences()["font"], "Sans 14")

    def test_font_chooser_applies_selection(self):
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.OK
        dialog.get_font.return_value = "Sans Bold 15"
        settings = Mock()
        settings.get_property.return_value = "Sans 11"
        with patch("app.Gtk.FontChooserDialog", return_value=dialog), \
                patch("app.Gtk.Settings.get_default", return_value=settings):
            self.editor.choose_font()
        dialog.set_font.assert_called_once_with("Sans 11")
        dialog.destroy.assert_called_once()
        self.assertEqual(self.store.load_preferences()["font"], "Sans Bold 15")

    def test_font_chooser_cancel_keeps_existing_selection(self):
        self.editor.set_editor_font("Sans 14")
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.CANCEL
        with patch("app.Gtk.FontChooserDialog", return_value=dialog), \
                patch("app.Gtk.Settings.get_default", return_value=Mock()):
            self.editor.choose_font()
        dialog.set_font.assert_called_once_with("Sans 14")
        self.assertEqual(self.store.load_preferences()["font"], "Sans 14")

    def test_import_creates_independent_unicode_note(self):
        source = Path(self.directory.name) / "Imported.md"
        source.write_text("\ufeff# Hello 🌿\n**bold**", encoding="utf-8")
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.OK
        dialog.get_filename.return_value = str(source)
        with patch("app.Gtk.FileChooserDialog", return_value=dialog), patch("app.Gtk.FileFilter"):
            self.editor.import_file()
        self.assertEqual(self.editor.note["title"], "Imported")
        self.assertEqual(self.editor.text(), "# Hello 🌿\n**bold**")
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], self.editor.text())
        self.assertTrue(source.read_text().startswith("\ufeff"))

    def test_invalid_utf8_import_keeps_active_note(self):
        source = Path(self.directory.name) / "invalid.txt"
        source.write_bytes(b"\xff")
        original_id = self.editor.note["id"]
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.OK
        dialog.get_filename.return_value = str(source)
        with patch("app.Gtk.FileChooserDialog", return_value=dialog), patch("app.Gtk.FileFilter"):
            self.editor.import_file()
        self.assertEqual(self.editor.note["id"], original_id)
        self.assertEqual(len(self.store.list_notes()), 1)
        self.editor.report.assert_called()

    def test_formatting_and_undo_keyboard_shortcuts(self):
        self.type_text("hello")
        self.editor.buffer.select_range(self.editor.buffer.get_start_iter(), self.editor.buffer.get_end_iter())
        def press(key, shift=False):
            event = SimpleNamespace(keyval=Gdk.keyval_from_name(key),
                                    state=Gdk.ModifierType.CONTROL_MASK |
                                    (Gdk.ModifierType.SHIFT_MASK if shift else 0))
            self.assertTrue(self.editor.key_pressed(None, event))
        press("b")
        self.assertEqual(self.editor.text(), "**hello**")
        press("z")
        self.assertEqual(self.editor.text(), "hello")
        press("Z", shift=True)
        self.assertEqual(self.editor.text(), "**hello**")
        press("z")
        press("i")
        self.assertEqual(self.editor.text(), "*hello*")


@unittest.skipIf(Editor is None, "System PyGObject / GTK 3 is unavailable")
class WindowTests(unittest.TestCase):
    def test_window_theme_popovers_and_reopen(self):
        if not Gtk.init_check()[0]:
            self.skipTest("No graphical display available")
        from app import Justnotes
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            application = Justnotes()
            application.set_application_id("io.github.nightfury.justnotes.Tests")
            application.register(None)
            window = Editor(application, store)
            try:
                window.buffer.begin_user_action()
                window.buffer.insert_at_cursor("hello **world**")
                window.buffer.end_user_action()
                window.title_entry.set_text("renamed")
                window.show_notes()
                window.show_formatting()
                window.show_info()
                window.toggle_theme()
                self.assertEqual(store.load_preferences()["theme"], "dark")
                note_id = window.note["id"]
                window.new_note()
                window.open_note(note_id)
                self.assertEqual(window.text(), "hello **world**")
                self.assertEqual(window.title_entry.get_text(), "renamed")
                self.assertFalse(window.close_requested())
            finally:
                window.destroy()
