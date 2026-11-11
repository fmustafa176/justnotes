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
                     "set_editor_font", "choose_font", "toggle_markers", "toggle_editor_theme", "delete_note",
                     "cache_current_tab", "update_tab_title", "restore_scroll", "close_tab", "switch_tab",
                     "request_quit", "request_delete_note", "tab_switched", "schedule_save", "autosave",
                     "before_insert", "before_delete", "mark_style_dirty", "edit_list_line"):
            locals()[name] = getattr(Editor, name)

    def __init__(self, store):
        self.store = store
        self.note = store.create_note()
        self.preferences = store.load_preferences()
        self.preferences["last_note"] = self.note["id"]
        self.open_tabs = [self.note["id"]]
        self.preferences["open_tabs"] = list(self.open_tabs)
        self.tab_states = {}
        self.tab_titles = {self.note["id"]: self.note["title"]}
        self.tab_labels = {}
        self.scroller = None
        self.scroll_source = 0
        self.save_source = 0
        self.style_dirty = None
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
        self.title_entry.set_text.side_effect = lambda text: setattr(self.title_entry.get_text, "return_value", text)
        self.view = Mock()
        self.error_bar = Mock()
        self.report = Mock()
        self.apply_appearance = Mock()
        self.show_notes = Mock()
        self.refresh_tabs = Mock()
        self.close = Mock()
        self.configured = Mock()
        self.buffer = Gtk.TextBuffer()
        for tag in ("bold", "italic", "strikethrough", "marker", "list", *(f"heading{i}" for i in range(1, 7))):
            self.buffer.create_tag(tag)
        self.marker_tag = self.buffer.get_tag_table().lookup("marker")
        self.buffer.connect("insert-text", self.before_insert)
        self.buffer.connect("delete-range", self.before_delete)
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
        for name in ("style_source", "scroll_source", "save_source"):
            if getattr(self.editor, name):
                GLib.source_remove(getattr(self.editor, name))
                setattr(self.editor, name, 0)

    def type_text(self, text, flush=True):
        self.editor.buffer.begin_user_action()
        self.editor.buffer.insert_at_cursor(text)
        self.editor.buffer.end_user_action()
        if flush:
            self.editor.flush()

    def press(self, key, shift=False):
        event = SimpleNamespace(keyval=Gdk.keyval_from_name(key), state=Gdk.ModifierType.CONTROL_MASK |
                                (Gdk.ModifierType.SHIFT_MASK if shift else 0))
        self.assertTrue(self.editor.key_pressed(None, event))

    def test_flush_persists_current_edit(self):
        self.type_text("Hello 🌙")
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "Hello 🌙")
        self.assertFalse(self.editor.dirty)

    def test_typing_burst_coalesces_disk_writes_and_flush_cancels_timer(self):
        with patch.object(self.store, "save_note", wraps=self.store.save_note) as save:
            for _ in range(100):
                previous = self.editor.save_source
                self.type_text("x", flush=False)
                if previous:
                    self.assertEqual(self.editor.save_source, previous)
            save.assert_not_called()
            self.assertTrue(self.editor.flush())
            save.assert_called_once()
        self.assertEqual(self.editor.save_source, 0)
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "x" * 100)

    def test_autosave_timer_writes_without_navigation(self):
        import time
        self.type_text("autosaved", flush=False)
        deadline = time.monotonic() + 1.5
        while self.editor.dirty and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertFalse(self.editor.dirty)
        self.assertEqual(self.editor.save_source, 0)
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "autosaved")

    def test_editing_back_to_saved_text_avoids_rewriting_file(self):
        self.type_text("x", flush=False)
        self.editor.buffer.delete(*self.editor.buffer.get_bounds())
        with patch.object(self.store, "save_note", wraps=self.store.save_note) as save:
            self.assertTrue(self.editor.flush())
            save.assert_not_called()
        self.assertFalse(self.editor.dirty)

    def test_pending_autosave_is_saved_before_switching_and_quitting(self):
        original = self.editor.note["id"]
        other = self.store.create_note()
        self.type_text("pending first", flush=False)
        self.assertTrue(self.editor.open_note(other["id"]))
        self.assertEqual(self.store.load_note(original)["text"], "pending first")
        self.type_text("pending second", flush=False)
        self.assertTrue(self.editor.request_quit())
        self.assertEqual(self.store.load_note(other["id"])["text"], "pending second")
        self.assertEqual(self.editor.save_source, 0)

    def test_small_edit_only_parses_affected_line(self):
        self.type_text("**bold**\n" * 3000 + "last", flush=False)
        self.clear_idle()
        self.editor.restyle(full=True)
        self.editor.buffer.place_cursor(self.editor.buffer.get_end_iter())
        self.type_text(" word", flush=False)
        from formatting import spans
        with patch("app.spans", wraps=spans) as parse:
            self.clear_idle()
            self.editor.restyle()
        parse.assert_called_once_with("last word")

    def test_incremental_styling_matches_full_styling_across_edits(self):
        import random
        generator = random.Random(74)
        self.type_text("# Title\n**bold** and *italic*\n~~gone~~\nplain 🌙\n• item\n1. number\n☑ done", flush=False)
        self.clear_idle()
        self.editor.restyle(full=True)
        reference = Gtk.TextBuffer()
        names = ("bold", "italic", "strikethrough", "marker", "list", *(f"heading{i}" for i in range(1, 7)))
        for name in names:
            reference.create_tag(name)
        expected = SimpleNamespace(buffer=reference, preferences=self.editor.preferences,
                                   marker_tag=reference.get_tag_table().lookup("marker"), style_dirty=None)
        for _ in range(100):
            length = self.editor.buffer.get_char_count()
            position = generator.randrange(length + 1)
            self.editor.buffer.begin_user_action()
            if generator.randrange(2) and position < length:
                end = min(length, position + generator.randrange(1, 6))
                self.editor.buffer.delete(self.editor.buffer.get_iter_at_offset(position),
                                          self.editor.buffer.get_iter_at_offset(end))
            else:
                self.editor.buffer.insert(self.editor.buffer.get_iter_at_offset(position),
                                          generator.choice(("**", "*", "# ", "~", "\n", "a 🌙")))
            self.editor.buffer.end_user_action()
            self.clear_idle()
            self.editor.restyle()
            reference.set_text(self.editor.text())
            Editor.restyle(expected, full=True)
            for index in range(self.editor.buffer.get_char_count()):
                actual_iter = self.editor.buffer.get_iter_at_offset(index)
                expected_iter = reference.get_iter_at_offset(index)
                for name in names:
                    self.assertEqual(actual_iter.has_tag(self.editor.buffer.get_tag_table().lookup(name)),
                                     expected_iter.has_tag(reference.get_tag_table().lookup(name)),
                                     (self.editor.text(), index, name))

    def test_undo_history_is_bounded_and_retains_recent_undo(self):
        from app import history_bytes, HISTORY_BYTES
        self.type_text("a" * 60000, flush=False)
        for _ in range(80):
            self.type_text("x", flush=False)
        self.assertLessEqual(history_bytes(self.editor.history), HISTORY_BYTES)
        self.assertLess(len(self.editor.history), 80)
        before = self.editor.text()
        self.editor.undo()
        self.assertEqual(self.editor.text(), before[:-1])
        self.editor.undo(redo=True)
        self.assertEqual(self.editor.text(), before)

    def test_switching_transfers_history_ownership_and_evicts_old_cache(self):
        from app import history_bytes
        original = self.editor.note["id"]
        self.type_text("first", flush=False)
        second = self.store.create_note(text="second")
        self.editor.open_note(second["id"])
        self.assertNotIn(second["id"], self.editor.tab_states)
        self.editor.open_note(original)
        self.assertNotIn(original, self.editor.tab_states)
        with patch("app.TAB_CACHE_BYTES", 900):
            for _ in range(6):
                note = self.store.create_note(text="n" * 100)
                self.editor.open_note(note["id"])
            self.assertLessEqual(sum(history_bytes(s["history"]) for s in self.editor.tab_states.values()), 900)
            self.assertNotIn(original, self.editor.tab_states)
        self.editor.open_note(original)
        self.assertEqual(self.editor.text(), "first")

    def test_open_notes_become_unique_persisted_tabs(self):
        first = self.editor.note["id"]
        second = self.store.create_note(title="second", text="other")
        self.assertTrue(self.editor.open_note(second["id"]))
        self.assertTrue(self.editor.open_note(second["id"]))
        self.assertEqual(self.editor.open_tabs, [first, second["id"]])
        self.assertEqual(Store(self.store.root).load_preferences()["open_tabs"], self.editor.open_tabs)
        self.assertEqual(Store(self.store.root).load_preferences()["last_note"], second["id"])

    def test_tabs_keep_independent_undo_redo_and_cursor(self):
        self.type_text("first")
        self.type_text(" second")
        first = self.editor.note["id"]
        self.editor.buffer.place_cursor(self.editor.buffer.get_iter_at_offset(3))
        other = self.store.create_note(title="other", text="other")
        self.editor.open_note(other["id"])
        self.editor.buffer.place_cursor(self.editor.buffer.get_end_iter())
        self.type_text(" changed")
        self.editor.open_note(first)
        self.assertEqual(self.editor.text(), "first second")
        self.assertEqual(self.editor.buffer.get_iter_at_mark(self.editor.buffer.get_insert()).get_offset(), 3)
        self.editor.undo()
        self.assertEqual(self.editor.text(), "first")
        self.assertEqual(self.store.load_note(other["id"])["text"], "other changed")
        self.editor.open_note(other["id"])
        self.editor.undo()
        self.assertEqual(self.editor.text(), "other")
        self.editor.open_note(first)
        self.editor.undo(redo=True)
        self.assertEqual(self.editor.text(), "first second")

    def test_closing_active_or_inactive_tabs_never_deletes_notes(self):
        first = self.editor.note["id"]
        other = self.store.create_note(title="other", text="saved")
        self.editor.open_note(other["id"])
        self.assertTrue(self.editor.close_tab(other["id"]))
        self.assertEqual(self.editor.note["id"], first)
        self.assertEqual(self.editor.open_tabs, [first])
        self.assertEqual(self.store.load_note(other["id"])["text"], "saved")
        self.editor.open_note(other["id"])
        self.editor.open_note(first)
        self.assertTrue(self.editor.close_tab(other["id"]))
        self.assertEqual(self.editor.note["id"], first)
        self.assertEqual(len(self.store.list_notes()), 2)

    def test_deleting_open_note_removes_its_tab(self):
        first = self.editor.note["id"]
        other = self.store.create_note(title="other")
        self.editor.open_note(other["id"])
        self.editor.delete_note(other["id"])
        self.assertEqual(self.editor.open_tabs, [first])
        self.assertEqual(Store(self.store.root).load_preferences()["open_tabs"], [first])

    def test_failed_tab_preference_write_keeps_current_editor_and_tabs(self):
        self.type_text("keep this")
        original = self.editor.note["id"]
        other = self.store.create_note(title="other")
        with patch.object(self.store, "save_preferences", side_effect=StorageError("disk full")):
            self.assertFalse(self.editor.open_note(other["id"]))
        self.assertEqual(self.editor.open_tabs, [original])
        self.assertEqual(self.editor.note["id"], original)
        self.assertEqual(self.editor.text(), "keep this")

    def test_failed_save_blocks_tab_closing(self):
        original = self.editor.note["id"]
        other = self.store.create_note()
        self.editor.open_note(other["id"])
        self.type_text("unsaved edit", flush=False)
        with patch.object(self.store, "save_note", side_effect=StorageError("disk full")):
            self.assertFalse(self.editor.close_tab(other["id"]))
        self.assertEqual(self.editor.open_tabs, [original, other["id"]])
        self.assertEqual(self.editor.note["id"], other["id"])

    def test_failed_notebook_switch_restores_selected_tab(self):
        original = self.editor.note["id"]
        other = self.store.create_note()
        page1, page2 = object(), object()
        self.editor.tab_pages = {original: page1, other["id"]: page2}
        self.editor.syncing_tabs = False
        notebook = Mock()
        notebook.page_num.return_value = 0
        with patch.object(self.editor, "open_note", return_value=False):
            self.editor.tab_switched(notebook, page2, 1)
        notebook.set_current_page.assert_called_once_with(0)
        self.assertFalse(self.editor.syncing_tabs)

    def test_tab_keyboard_shortcuts_and_ctrl_q(self):
        original = self.editor.note["id"]
        other = self.store.create_note()
        self.editor.open_note(other["id"])
        self.press("Tab")
        self.assertEqual(self.editor.note["id"], original)
        self.press("ISO_Left_Tab", shift=True)
        self.assertEqual(self.editor.note["id"], other["id"])
        self.press("w")
        self.assertEqual(self.editor.open_tabs, [original])
        self.assertEqual(len(self.store.list_notes()), 2)
        self.press("q")
        self.editor.close.assert_called_once()

    def test_ctrl_q_does_not_close_if_saving_fails(self):
        with patch.object(self.store, "save_note", side_effect=StorageError("disk full")):
            self.type_text("do not lose this")
            self.press("q")
            self.editor.close.assert_not_called()
        self.press("q")
        self.editor.close.assert_called_once()
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "do not lose this")

    def test_delete_confirmation_cancel_keeps_note(self):
        original = self.editor.note["id"]
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.CANCEL
        with patch("app.Gtk.MessageDialog", return_value=dialog), patch.object(self.editor, "delete_note") as delete:
            self.assertFalse(self.editor.request_delete_note(original))
            delete.assert_not_called()
        dialog.set_default_response.assert_called_once_with(Gtk.ResponseType.CANCEL)
        dialog.destroy.assert_called_once()
        self.assertEqual(len(self.store.list_notes()), 1)

    def test_delete_confirmation_accept_deletes_exact_note(self):
        original = self.editor.note["id"]
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.ACCEPT
        with patch("app.Gtk.MessageDialog", return_value=dialog):
            self.assertTrue(self.editor.request_delete_note(original))
        with self.assertRaises(StorageError):
            self.store.load_note(original)
        self.assertEqual(len(self.store.list_notes()), 1)  # Fresh blank replacement.

    def test_delete_confirmation_is_not_shown_if_saving_fails(self):
        self.type_text("unsaved edit", flush=False)
        with patch.object(self.store, "save_note", side_effect=StorageError("disk full")), \
                patch("app.Gtk.MessageDialog") as dialog:
            self.assertFalse(self.editor.request_delete_note(self.editor.note["id"]))
            dialog.assert_not_called()
        self.assertEqual(len(self.store.list_notes()), 1)

    def test_delete_inactive_note_keeps_current_editor(self):
        self.type_text("keep editing")
        other = self.store.create_note(text="remove this")
        current = self.editor.note["id"]
        self.assertTrue(self.editor.delete_note(other["id"]))
        self.assertEqual(self.editor.note["id"], current)
        self.assertEqual(self.editor.text(), "keep editing")
        self.assertEqual(len(Store(self.store.root).list_notes()), 1)
        self.editor.show_notes.assert_called_once()

    def test_delete_active_note_opens_remaining_note_and_remembers_it(self):
        original = self.editor.note["id"]
        other = self.store.create_note(text="remaining", title="other")
        self.assertTrue(self.editor.delete_note(original))
        self.assertEqual(self.editor.note["id"], other["id"])
        self.assertEqual(self.editor.text(), "remaining")
        self.assertEqual(Store(self.store.root).load_preferences()["last_note"], other["id"])
        self.assertEqual(len(Store(self.store.root).list_notes()), 1)

    def test_delete_last_note_creates_a_persisted_blank_note(self):
        original = self.editor.note["id"]
        self.assertTrue(self.editor.delete_note(original))
        self.assertNotEqual(self.editor.note["id"], original)
        self.assertEqual(self.editor.text(), "")
        self.assertEqual(self.editor.note["title"], "note1")
        self.assertEqual(Store(self.store.root).list_notes(), [self.editor.note])
        self.assertEqual(Store(self.store.root).load_preferences()["last_note"], self.editor.note["id"])

    def test_delete_failure_keeps_editor_and_active_note(self):
        self.type_text("keep this text")
        original = self.editor.note["id"]
        self.store.create_note(text="other")
        with patch.object(self.store, "delete_note", side_effect=StorageError("cannot delete")):
            self.assertFalse(self.editor.delete_note(original))
        self.assertEqual(self.editor.note["id"], original)
        self.assertEqual(self.editor.text(), "keep this text")
        self.assertEqual(len(Store(self.store.root).list_notes()), 2)
        self.assertTrue(self.editor.flush())
        self.assertEqual(Store(self.store.root).load_preferences()["last_note"], original)

    def test_failed_last_note_deletion_cleans_up_its_placeholder(self):
        original = self.editor.note["id"]
        actual_delete = self.store.delete_note

        def fail_original(note_id):
            if note_id == original:
                raise StorageError("cannot delete original")
            return actual_delete(note_id)

        with patch.object(self.store, "delete_note", side_effect=fail_original):
            self.assertFalse(self.editor.delete_note(original))
        self.assertEqual([note["id"] for note in self.store.list_notes()], [original])
        self.assertEqual(self.editor.note["id"], original)

    def test_delete_stops_if_current_changes_cannot_save(self):
        other = self.store.create_note(text="other")
        self.type_text("unsaved edit", flush=False)
        with patch.object(self.store, "save_note", side_effect=StorageError("disk full")), \
                patch.object(self.store, "delete_note") as delete:
            self.assertFalse(self.editor.delete_note(other["id"]))
            delete.assert_not_called()
        self.assertEqual(len(self.store.list_notes()), 2)

    def test_delete_stops_if_replacement_preference_cannot_save(self):
        original = self.editor.note["id"]
        self.store.create_note(text="other")
        with patch.object(self.store, "save_preferences", side_effect=StorageError("disk full")), \
                patch.object(self.store, "delete_note") as delete:
            self.assertFalse(self.editor.delete_note(original))
            delete.assert_not_called()
        self.assertEqual(self.editor.note["id"], original)
        self.assertEqual(len(self.store.list_notes()), 2)

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

    def enter(self, control=False, shift=False):
        event = SimpleNamespace(keyval=Gdk.keyval_from_name("Return"),
                                state=(Gdk.ModifierType.CONTROL_MASK if control else 0) |
                                      (Gdk.ModifierType.SHIFT_MASK if shift else 0))
        return self.editor.key_pressed(None, event)

    def test_list_actions_toggle_selected_lines_as_one_undo_step(self):
        self.type_text("one\ntwo")
        for kind, expected in (("bullet", "• one\n• two"),
                               ("number", "1. one\n2. two"), ("task", "☐ one\n☐ two")):
            self.editor.buffer.select_range(*self.editor.buffer.get_bounds())
            self.editor.apply_format(list_type=kind)
            self.assertEqual(self.editor.text(), expected)
            self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], expected)
            self.editor.undo()
            self.assertEqual(self.editor.text(), "one\ntwo")
            self.editor.undo(redo=True)
            self.editor.buffer.select_range(*self.editor.buffer.get_bounds())
            self.editor.apply_format(list_type=kind)
            self.assertEqual(self.editor.text(), "one\ntwo")

    def test_list_enter_continues_and_leaves_an_empty_item(self):
        self.type_text("9. nine")
        self.assertTrue(self.enter())
        self.assertEqual(self.editor.text(), "9. nine\n10. ")
        self.assertTrue(self.enter())
        self.assertEqual(self.editor.text(), "9. nine\n")
        self.editor.undo()
        self.assertEqual(self.editor.text(), "9. nine\n10. ")
        self.editor.undo()
        self.assertEqual(self.editor.text(), "9. nine")

    def test_list_enter_splits_content_at_caret(self):
        self.type_text("• hello world")
        self.editor.buffer.place_cursor(self.editor.buffer.get_iter_at_offset(8))
        self.assertTrue(self.enter())
        self.assertEqual(self.editor.text(), "• hello \n• world")

    def test_checking_task_preserves_caret_saves_and_undoes(self):
        self.type_text("- [ ] done")
        self.assertTrue(self.enter(control=True))
        self.assertEqual(self.editor.text(), "☑ done")
        self.assertEqual(self.editor.buffer.get_iter_at_mark(self.editor.buffer.get_insert()).get_offset(), 6)
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "☑ done")
        self.clear_idle()
        self.editor.restyle()
        strike = self.editor.buffer.get_tag_table().lookup("strikethrough")
        self.assertTrue(self.editor.buffer.get_iter_at_offset(3).has_tag(strike))
        self.editor.undo()
        self.assertEqual(self.editor.text(), "- [ ] done")

    def test_list_shortcuts_handle_shifted_key_symbols(self):
        self.type_text("item")
        for key, expected in (("asterisk", "• item"), ("ampersand", "1. item"), ("parenleft", "☐ item")):
            self.press(key, shift=True)
            self.assertEqual(self.editor.text(), expected)
            self.editor.undo()
            self.assertEqual(self.editor.text(), "item")

    def test_enter_does_not_intercept_plain_text_selection_or_title(self):
        self.type_text("plain")
        self.assertFalse(self.enter())
        self.editor.apply_format(list_type="bullet")
        self.assertFalse(self.enter(shift=True))
        self.editor.buffer.select_range(*self.editor.buffer.get_bounds())
        self.assertFalse(self.enter())
        self.editor.buffer.place_cursor(self.editor.buffer.get_end_iter())
        self.editor.view.has_focus.return_value = False
        self.assertFalse(self.enter())

    def test_checklist_and_inline_styles_survive_marker_visibility_toggle(self):
        self.type_text("☑ **done**")
        self.clear_idle()
        self.editor.restyle()
        self.assertEqual(self.editor.buffer.get_text(*self.editor.buffer.get_bounds(), False), "☑ done")
        checkbox = Mock()
        checkbox.get_active.return_value = False
        self.editor.toggle_markers(checkbox)
        self.assertEqual(self.editor.text(), "☑ **done**")
        self.assertEqual(self.editor.buffer.get_text(*self.editor.buffer.get_bounds(), False), "☑ **done**")

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

    def test_bold_italic_and_strikethrough_toggle_and_preserve_undo(self):
        self.type_text("hello")
        for marker in ("**", "*", "~"):
            with self.subTest(marker=marker):
                self.editor.buffer.select_range(self.editor.buffer.get_start_iter(), self.editor.buffer.get_end_iter())
                self.editor.apply_format(marker=marker)
                self.assertEqual(self.editor.text(), marker + "hello" + marker)
                self.editor.apply_format(marker=marker)
                self.assertEqual(self.editor.text(), "hello")
                self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], "hello")
                self.editor.undo()
                self.assertEqual(self.editor.text(), marker + "hello" + marker)
                self.editor.undo(redo=True)
                self.assertEqual(self.editor.text(), "hello")

    def test_heading_toggles_selected_lines_without_stacking(self):
        self.type_text("one\ntwo")
        self.editor.buffer.select_range(self.editor.buffer.get_start_iter(), self.editor.buffer.get_end_iter())
        self.editor.apply_format(level=2)
        self.assertEqual(self.editor.text(), "## one\n## two")
        self.editor.apply_format(level=2)
        self.assertEqual(self.editor.text(), "one\ntwo")
        self.editor.apply_format(level=3)
        self.assertEqual(self.editor.text(), "### one\n### two")
        self.editor.apply_format(level=1)
        self.assertEqual(self.editor.text(), "# one\n# two")

    def test_hidden_markers_preserve_source_and_can_be_shown(self):
        raw = "# Title\n**bold** *italic* ~gone~ ~~also gone~~ 🌙"
        self.type_text(raw)
        self.clear_idle()
        self.editor.restyle()
        start, end = self.editor.buffer.get_bounds()
        self.assertEqual(self.editor.buffer.get_text(start, end, False), "Title\nbold italic gone also gone 🌙")
        self.assertEqual(self.editor.text(), raw)
        self.assertEqual(self.store.load_note(self.editor.note["id"])["text"], raw)
        history_size = len(self.editor.history)
        toggle = Mock()
        toggle.get_active.return_value = False
        self.editor.toggle_markers(toggle)
        self.assertEqual(self.editor.buffer.get_text(*self.editor.buffer.get_bounds(), False), raw)
        self.assertFalse(Store(self.store.root).load_preferences()["hide_markers"])
        toggle.get_active.return_value = True
        self.editor.toggle_markers(toggle)
        self.assertEqual(len(self.editor.history), history_size)
        self.assertTrue(Store(self.store.root).load_preferences()["hide_markers"])

    def test_strikethrough_keyboard_shortcut_toggles(self):
        self.type_text("gone")
        self.editor.buffer.select_range(self.editor.buffer.get_start_iter(), self.editor.buffer.get_end_iter())
        event = SimpleNamespace(keyval=Gdk.keyval_from_name("X"),
                                state=Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        self.assertTrue(self.editor.key_pressed(None, event))
        self.assertEqual(self.editor.text(), "~gone~")
        self.assertTrue(self.editor.key_pressed(None, event))
        self.assertEqual(self.editor.text(), "gone")

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
        self.editor.flush()
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

    def test_editor_light_dark_toggle_persists_without_changing_notes(self):
        self.type_text("my note")
        self.editor.toggle_editor_theme()
        self.assertEqual(Store(self.store.root).load_preferences()["editor_theme"], "dark")
        self.editor.toggle_editor_theme()
        self.assertEqual(Store(self.store.root).load_preferences()["editor_theme"], "light")
        self.assertEqual(self.editor.text(), "my note")

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
                self.assertEqual(window.title_entry.get_tooltip_text(), "Title")
                self.assertEqual(window.notes_button.get_tooltip_text(), "Notes")
                self.assertTrue(window.shell.get_style_context().has_class("window-shell"))
                self.assertFalse(window.shell.get_style_context().has_class("editor-surface"))
                self.assertTrue(window.overlay.get_style_context().has_class("editor-surface"))
                self.assertEqual(window.notebook.get_margin_start(), 10)
                self.assertEqual(window.notebook.get_margin_end(), 10)
                self.assertEqual(window.info_button.get_halign(), Gtk.Align.START)
                self.assertEqual(window.info_button.get_parent(), window.footer)
                self.assertEqual(window.bottom_controls.get_halign(), Gtk.Align.END)
                self.assertEqual(window.bottom_controls.get_parent(), window.footer)
                self.assertEqual(window.bottom_controls.get_children(),
                                 [window.notes_button, window.theme_button, window.format_button])
                self.assertEqual(window.get_titlebar().get_custom_title(), None)
                for button in (window.notes_button, window.theme_button, window.format_button):
                    self.assertEqual(button.get_parent(), window.bottom_controls)
                self.assertEqual(window.footer.get_parent(), window.shell)
                self.assertEqual(window.overlay.get_children(), [window.scroller])
                self.assertEqual(window.scroller.get_margin_bottom(), 12)
                self.assertTrue(window.footer.get_style_context().has_class("bottom-panel"))
                extra = store.create_note(title="temporary", text="remove this")
                window.show_notes()
                def descendants(widget):
                    yield widget
                    if isinstance(widget, Gtk.Container):
                        for child in widget.get_children():
                            yield from descendants(child)
                deletes = [widget for widget in descendants(window.popovers[0])
                           if isinstance(widget, Gtk.Button) and widget.get_tooltip_text() == "Delete"]
                self.assertEqual(len(deletes), 2)
                delete = next(button for button in deletes if button.get_accessible().get_name() == "Delete temporary")
                color = delete.get_style_context().get_color(Gtk.StateFlags.NORMAL)
                self.assertGreater(color.red, color.green * 2)
                confirmation = Mock()
                confirmation.run.return_value = Gtk.ResponseType.ACCEPT
                with patch("app.Gtk.MessageDialog", return_value=confirmation):
                    delete.clicked()
                with self.assertRaises(StorageError):
                    store.load_note(extra["id"])
                self.assertEqual(window.text(), "hello **world**")
                original = window.note["id"]
                self.assertFalse(window.notebook.get_show_tabs())
                window.new_note()
                second = window.note["id"]
                self.assertEqual(window.notebook.get_n_pages(), 2)
                self.assertFalse(window.notebook.get_show_tabs())
                self.assertEqual(window.title_entry.get_parent(), window.tab_widgets[second])
                self.assertFalse(window.tab_labels[second].get_visible())
                self.assertTrue(window.tab_closes[second].get_visible())
                self.assertFalse(window.tab_closes[original].get_visible())
                self.assertEqual(window.title_entry.get_alignment(), .5)
                self.assertTrue(window.tab_labels[original].get_visible())
                # First click selects an inactive title and leaves focus in the editor.
                window.tab_labels[original].clicked()
                self.assertEqual(window.note["id"], original)
                self.assertFalse(window.title_entry.has_focus())
                self.assertEqual(window.title_entry.get_parent(), window.tab_widgets[original])
                window.notebook.set_current_page(0)
                self.assertEqual(window.note["id"], original)
                self.assertEqual(window.text(), "hello **world**")
                self.assertEqual(window.overlay.get_parent(), window.tab_pages[original])
                window.close_tab(second)
                self.assertEqual(window.notebook.get_n_pages(), 1)
                self.assertFalse(window.notebook.get_show_tabs())
                self.assertEqual(store.load_note(second)["text"], "")
                window.open_note(second)
                reopened = Editor(application, store)
                try:
                    self.assertEqual(reopened.open_tabs, [original, second])
                    self.assertEqual(reopened.note["id"], second)
                    self.assertEqual(reopened.notebook.get_n_pages(), 2)
                finally:
                    reopened.destroy()
                window.open_note(original)
                window.show_formatting()
                window.show_info()
                self.assertIsInstance(window.get_titlebar(), Gtk.HeaderBar)
                self.assertFalse(window.get_titlebar().get_property("decoration-layout-set"))
                self.assertTrue(hasattr(window, "theme_button"))
                self.assertNotIn("theme", store.load_preferences())
                settings = Gtk.Settings.get_default()
                dark_preference = settings.get_property("gtk-application-prefer-dark-theme")
                if window.titlebar_background:
                    window.titlebar_background.refresh()
                titlebar_color = window.get_titlebar().get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
                if window.titlebar_background.translucent:
                    self.assertLessEqual(titlebar_color.alpha, 0.841)
                    shell_color = window.shell.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
                    self.assertAlmostEqual(shell_color.alpha, titlebar_color.alpha, places=3)
                self.assertEqual(window.get_titlebar().get_opacity(), 1)
                self.assertEqual(window.get_opacity(), 1)
                self.assertEqual(window.view.get_style_context().get_background_color(Gtk.StateFlags.NORMAL).alpha, 1)
                window.theme_button.clicked()
                self.assertEqual(store.load_preferences()["editor_theme"], "dark")
                self.assertEqual(settings.get_property("gtk-application-prefer-dark-theme"), dark_preference)
                self.assertEqual(window.get_titlebar().get_style_context().get_background_color(Gtk.StateFlags.NORMAL), titlebar_color)
                for state in (Gtk.StateFlags.NORMAL, Gtk.StateFlags.FOCUSED, Gtk.StateFlags.BACKDROP):
                    self.assertEqual(window.title_entry.get_style_context().get_background_color(state).alpha, 0)
                note_id = window.note["id"]
                window.new_note()
                window.open_note(note_id)
                self.assertEqual(window.text(), "hello **world**")
                self.assertEqual(window.title_entry.get_text(), "renamed")
                self.assertFalse(window.close_requested())
            finally:
                window.destroy()


@unittest.skipIf(Editor is None, "System GTK is unavailable")
class GeometryControllerTests(unittest.TestCase):
    def window(self, state=0, can_position=True):
        return SimpleNamespace(
            preferences=dict(width=900, height=600, x=50, y=60),
            get_mapped=Mock(return_value=True),
            get_window=Mock(return_value=SimpleNamespace(get_state=lambda: state)),
            get_size=Mock(return_value=(950, 700)),
            get_position=Mock(return_value=(-1200, 75)),
            can_position=can_position, geometry_source=0, save_geometry=Mock())

    def test_capture_uses_restorable_size_instead_of_raw_event_dimensions(self):
        window = self.window()
        with patch("app.GLib.timeout_add", return_value=17):
            Editor.configured(window, window, SimpleNamespace(width=980, height=748))
        self.assertEqual((window.preferences["width"], window.preferences["height"]), (950, 700))
        self.assertEqual((window.preferences["x"], window.preferences["y"]), (-1200, 75))
        self.assertEqual(window.preferences["geometry_version"], 1)
        self.assertTrue(window.preferences_dirty)

    def test_identical_configure_does_not_reschedule_disk_write(self):
        window = self.window()
        with patch("app.GLib.timeout_add", return_value=17) as schedule:
            Editor.configured(window)
            Editor.configured(window)
        schedule.assert_called_once()

    def test_maximized_and_fullscreen_keep_normal_geometry(self):
        for state in (Gdk.WindowState.MAXIMIZED, Gdk.WindowState.FULLSCREEN, Gdk.WindowState.ICONIFIED):
            window = self.window(state)
            with patch("app.GLib.timeout_add", return_value=17):
                Editor.configured(window)
            self.assertEqual((window.preferences["width"], window.preferences["height"]), (900, 600))
            window.get_size.assert_not_called()
        window = self.window(Gdk.WindowState.MAXIMIZED)
        with patch("app.GLib.timeout_add", return_value=17):
            Editor.configured(window)
        self.assertTrue(window.preferences["maximized"])

    def test_unmapped_window_does_not_overwrite_saved_geometry(self):
        window = self.window()
        window.get_mapped.return_value = False
        Editor.configured(window)
        window.get_size.assert_not_called()

    def test_wayland_does_not_save_meaningless_global_position(self):
        window = self.window(can_position=False)
        with patch("app.GLib.timeout_add", return_value=17):
            Editor.configured(window)
        window.get_position.assert_not_called()
        self.assertEqual((window.preferences["x"], window.preferences["y"]), (50, 60))


@unittest.skipIf(Editor is None, "System GTK is unavailable")
class LifecycleTests(unittest.TestCase):
    def test_destroy_releases_timers_signals_popovers_and_cached_history(self):
        window = SimpleNamespace(
            titlebar_background=Mock(), system_theme=Mock(), update_worker=Mock(),
            update_source=0, update_repeat_source=0, geometry_source=10, style_source=11,
            scroll_source=12, save_source=13, recovery_source=14,
            popovers=[Mock()], style_dirty=(object(), object()),
            buffer=Mock(), buffer_handlers=[21, 22, 23],
            tab_states={"note": {"history": ["big text"]}}, history=["text"],
            tab_pages={"note": Mock()}, tab_labels={"note": Mock()}, tab_widgets={"note": Mock()},
            tab_closes={"note": Mock()},
            get_screen=Mock(), css=Mock())
        popover = window.popovers[0]
        with patch("app.GLib.source_remove") as remove, \
                patch("app.Gtk.StyleContext.remove_provider_for_screen") as remove_css:
            Editor.destroyed(window)
        self.assertEqual([call.args[0] for call in remove.call_args_list], [10, 11, 12, 13, 14])
        self.assertEqual(window.buffer.disconnect.call_count, 3)
        self.assertEqual(window.buffer.delete_mark.call_count, 2)
        self.assertIsNone(window.style_dirty)
        popover.destroy.assert_called_once()
        self.assertFalse(window.tab_states)
        self.assertFalse(window.history)
        self.assertFalse(window.buffer_handlers)
        remove_css.assert_called_once()
