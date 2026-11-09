"""The justnotes GTK 3 interface."""

import sys
from pathlib import Path

from appearance import EDITOR_COLORS, modified_label, register_fonts, stylesheet, tab_width, window_geometry

register_fonts()

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from formatting import heading_edit, spans, toggle_inline
from storage import StorageError, Store
from system_theme import SystemTheme, TitlebarBackground, follow_system

APP_ID = "io.github.nightfury.justnotes"


def glyph(name):
    """Use the desktop's symbolic icons and its light/dark recoloring."""
    icon = {"notes": "text-x-generic-symbolic", "moon": "weather-clear-night-symbolic",
            "close": "window-close-symbolic"}[name]
    image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    image.set_pixel_size(20)
    return image


def editor_font_css(font):
    if font is None:
        return ""  # Inherit the live GTK system font, including its size.
    description = Pango.FontDescription.from_string(font)
    family = (description.get_family() or "Sans").replace("\\", "\\\\").replace('"', '\\"')
    family = family.replace("\n", "\\a ").replace("\r", "\\d ")
    size = description.get_size() / Pango.SCALE or 10
    unit = "px" if description.get_size_is_absolute() else "pt"
    style = {Pango.Style.ITALIC: "italic", Pango.Style.OBLIQUE: "oblique"}.get(description.get_style(), "normal")
    return ('.justnotes-window textview { font-family: "' + family + '"; '
            f'font-size: {size:g}{unit}; font-weight: {int(description.get_weight())}; font-style: {style}; }}')


HISTORY_BYTES = 8 * 1024 * 1024
TAB_CACHE_BYTES = 32 * 1024 * 1024


def history_bytes(history):
    # Python Unicode may use four bytes per character; include tuple overhead.
    return sum(len(state[0]) * 4 + 128 for state in history)


def trim_history(history):
    total = 0
    first = len(history)
    for state in reversed(history[-500:]):
        size = len(state[0]) * 4 + 128
        if total + size > HISTORY_BYTES and len(history) - first >= 2:
            break
        total += size
        first -= 1
    return history[first:]


class Editor(Gtk.ApplicationWindow):
    def __init__(self, application, store):
        super().__init__(application=application, title="justnotes")
        self.store = store
        self.preferences = store.load_preferences()
        notes = store.list_notes()
        existing = {note["id"]: note for note in notes}
        self.open_tabs = [key for key in self.preferences.get("open_tabs", []) if key in existing]
        self.note = next((n for n in notes if n["id"] == self.preferences["last_note"]), None)
        if self.note is None:
            self.note = existing[self.open_tabs[0]] if self.open_tabs else notes[0] if notes else store.create_note()
        if self.note["id"] not in self.open_tabs:
            self.open_tabs.append(self.note["id"])
        self.tab_titles = {note["id"]: note["title"] for note in notes}
        self.tab_titles[self.note["id"]] = self.note["title"]
        self.tab_states = {}
        self.tab_pages = {}
        self.tab_labels = {}
        self.tab_widgets = {}
        self.reveal_tab = False
        self.syncing_tabs = False
        self.preferences["last_note"] = self.note["id"]
        self.preferences["open_tabs"] = list(self.open_tabs)
        store.save_preferences(self.preferences)
        self.loading = False
        self.dirty = False
        self.preferences_dirty = False
        self.history = []
        self.history_index = -1
        self.user_action_depth = 0
        self.restoring = False
        self.style_source = 0
        self.geometry_source = 0
        self.scroll_source = 0
        self.save_source = 0
        self.recovery_source = 0
        self.style_dirty = None
        self.popovers = []
        self.system_theme = None
        display = self.get_display()
        monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
        primary = display.get_primary_monitor()
        if primary in monitors:
            monitors.remove(primary)
            monitors.insert(0, primary)
        areas = [(r.x, r.y, r.width, r.height) for r in (m.get_workarea() for m in monitors)]
        width, height, x, y = window_geometry(self.preferences, areas or [(0, 0, 1366, 768)])
        self.can_position = display.__gtype__.name == "GdkX11Display"
        self.set_default_size(width, height)
        if self.can_position:
            self.move(x, y)
        if self.preferences.get("maximized", False):
            self.maximize()
        self.set_size_request(360, 280)
        self.get_style_context().add_class("justnotes-window")
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        titlebar = Gtk.HeaderBar(title="justnotes", show_close_button=True)
        titlebar.set_has_subtitle(False)
        # Leave decoration-layout unset so GTK uses the desktop's button layout.
        self.set_titlebar(titlebar)
        self.titlebar_background = None
        if visual is not None and self.is_composited():
            self.titlebar_background = TitlebarBackground(titlebar)
        icon = Path(__file__).parent / "assets/justnotes.png"
        if not icon.exists():
            icon = icon.with_suffix(".svg")
        self.set_icon_from_file(str(icon))

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.get_style_context().add_class("editor-surface")
        self.add(layout)
        controls = Gtk.Box(spacing=4)
        controls.set_margin_top(20)
        controls.set_margin_start(28)
        controls.set_margin_end(28)
        layout.pack_start(controls, False, False, 0)
        self.title_entry = Gtk.Entry()
        self.title_entry.set_has_frame(False)
        self.title_entry.get_style_context().add_class("note-title")
        self.title_entry.set_placeholder_text("Untitled")
        self.title_entry.set_tooltip_text("Title")
        self.title_entry.get_accessible().set_name("Title")
        self.title_entry.set_width_chars(1)
        self.title_entry.set_hexpand(True)
        self.tab_strip = Gtk.Box(homogeneous=True)
        self.tab_scroll = Gtk.ScrolledWindow()
        self.tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.tab_scroll.set_overlay_scrolling(True)
        self.tab_scroll.set_min_content_width(1)
        self.tab_scroll.add(self.tab_strip)
        controls.pack_start(self.tab_scroll, True, True, 0)
        self.tab_scroll.connect("size-allocate", self.size_tabs)
        self.tab_strip.connect("size-allocate", self.reveal_active_tab)
        self.notes_button = self.button("notes", "Notes", self.show_notes)
        controls.pack_start(self.notes_button, False, False, 0)
        self.theme_button = self.button("moon", "Editor theme", self.toggle_editor_theme)
        controls.pack_start(self.theme_button, False, False, 0)
        self.format_button = self.button("Aa", "Formatting", self.show_formatting)
        controls.pack_start(self.format_button, False, False, 0)

        self.error_bar = Gtk.InfoBar()
        self.error_bar.set_message_type(Gtk.MessageType.ERROR)
        self.error_bar.set_no_show_all(True)
        self.error_label = Gtk.Label(xalign=0)
        self.error_label.set_line_wrap(True)
        self.error_label.set_max_width_chars(70)
        self.error_label.set_selectable(True)
        self.error_bar.get_content_area().add(self.error_label)
        self.error_bar.add_button("Retry saving", Gtk.ResponseType.APPLY)
        self.error_bar.connect("response", lambda *_: self.flush())
        layout.pack_start(self.error_bar, False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_show_tabs(False)
        self.notebook.set_show_border(False)
        self.notebook.get_style_context().add_class("editor-tabs")
        layout.pack_start(self.notebook, True, True, 0)
        overlay = Gtk.Overlay()
        self.overlay = overlay
        scroller = Gtk.ScrolledWindow()
        self.scroller = scroller
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        overlay.add(scroller)
        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(28)
        self.view.set_right_margin(28)
        self.view.set_top_margin(40)
        self.view.set_bottom_margin(74)
        self.view.set_pixels_above_lines(5)
        self.view.set_pixels_below_lines(5)
        self.view.set_pixels_inside_wrap(10)
        self.view.get_accessible().set_name("Note text")
        scroller.add(self.view)
        self.info_button = self.button("i", "Info", self.show_info)
        self.info_button.get_style_context().add_class("info-button")
        self.info_button.set_halign(Gtk.Align.END)
        self.info_button.set_valign(Gtk.Align.END)
        self.info_button.set_margin_end(22)
        self.info_button.set_margin_bottom(20)
        overlay.add_overlay(self.info_button)

        self.buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        self.buffer.create_tag("italic", style=Pango.Style.ITALIC)
        self.buffer.create_tag("strikethrough", strikethrough=True)
        for level, size in enumerate((38, 28, 22, 21, 20, 19), 1):
            self.buffer.create_tag(f"heading{level}", weight=Pango.Weight.SEMIBOLD, scale=size / 19)
        self.marker_tag = self.buffer.create_tag("marker", weight=Pango.Weight.NORMAL,
                                                 style=Pango.Style.NORMAL,
                                                 invisible=self.preferences.get("hide_markers", True))
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_appearance()
        follow_system(Gtk.Settings.get_default())
        self.system_theme = SystemTheme(self.system_theme_changed)
        self.title_entry.connect("changed", self.title_changed)
        self.buffer_handlers = [self.buffer.connect(signal, callback) for signal, callback in (
            ("insert-text", self.before_insert), ("delete-range", self.before_delete),
            ("changed", self.text_changed), ("begin-user-action", self.begin_action),
            ("end-user-action", self.end_action))]
        self.connect("key-press-event", self.key_pressed)
        self.connect("delete-event", self.close_requested)
        self.connect("configure-event", self.configured)
        self.connect("destroy", self.destroyed)
        self.load_editor(self.note)
        self.refresh_tabs()
        self.notebook.connect_after("switch-page", self.tab_switched)
        self.show_all()
        self.view.grab_focus()
        if store.recovered:
            self.recovery_source = GLib.idle_add(self.recovery_notice)

    @staticmethod
    def button(icon, tooltip, callback):
        button = Gtk.Button()
        button.add(Gtk.Label(label=icon) if icon in ("Aa", "i") else glyph(icon))
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("chrome-button")
        button.set_tooltip_text(tooltip)
        button.get_accessible().set_name(tooltip)
        button.connect("clicked", callback)
        return button

    def text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)

    def report(self, error):
        self.error_label.set_text(f"{error}\nYour editor contents are still here. Retry saving before switching notes or closing.")
        self.error_bar.set_no_show_all(False)
        self.error_bar.show_all()
        self.error_bar.set_no_show_all(True)

    def save_current(self):
        if not self.dirty:
            return True
        candidate = dict(self.note, title=self.title_entry.get_text(), text=self.text())
        if candidate == self.note:
            self.dirty = False
            return True
        try:
            self.store.save_note(candidate)
        except StorageError as exc:
            self.report(exc)
            return False
        self.note = candidate
        self.dirty = False
        return True

    def save_preferences(self):
        if not self.preferences_dirty:
            return True
        try:
            self.store.save_preferences(self.preferences)
        except StorageError as exc:
            self.report(exc)
            return False
        self.preferences_dirty = False
        return True

    def flush(self):
        if self.save_source:
            GLib.source_remove(self.save_source)
            self.save_source = 0
        if self.save_current() and self.save_preferences():
            self.error_bar.hide()
            return True
        return False

    def title_changed(self, *_):
        if not self.loading:
            self.dirty = True
            self.schedule_save()
            self.update_tab_title()

    def schedule_save(self):
        # One timer per burst, never postponed indefinitely by continuous typing.
        if not self.save_source:
            self.save_source = GLib.timeout_add(250, self.autosave)

    def autosave(self):
        self.save_source = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def before_insert(self, buffer, location, _text, _length):
        self.mark_style_dirty(location, location)

    def before_delete(self, buffer, first, last):
        self.mark_style_dirty(first, last)

    def mark_style_dirty(self, first, last):
        if self.loading:
            return
        first, last = first.copy(), last.copy()
        first.set_line_offset(0)
        if not last.ends_line():
            last.forward_to_line_end()
        if self.style_dirty is None:
            self.style_dirty = (self.buffer.create_mark(None, first, True),
                                self.buffer.create_mark(None, last, False))
        else:
            start_mark, end_mark = self.style_dirty
            if first.compare(self.buffer.get_iter_at_mark(start_mark)) < 0:
                self.buffer.move_mark(start_mark, first)
            if last.compare(self.buffer.get_iter_at_mark(end_mark)) > 0:
                self.buffer.move_mark(end_mark, last)

    def text_changed(self, *_):
        if self.loading:
            return
        self.dirty = True
        if not self.user_action_depth:
            self.schedule_save()
        if not self.restoring and not self.user_action_depth:
            self.record_history()
        if not self.style_source:
            self.style_source = GLib.idle_add(self.restyle)

    def restyle(self, full=False):
        self.style_source = 0
        start, end = self.buffer.get_bounds()
        if self.style_dirty is not None:
            first, last = self.style_dirty
            if not full:
                start, end = self.buffer.get_iter_at_mark(first), self.buffer.get_iter_at_mark(last)
                start.set_line_offset(0)
                if not end.ends_line():
                    end.forward_to_line_end()
            self.buffer.delete_mark(first)
            self.buffer.delete_mark(last)
            self.style_dirty = None
        self.buffer.remove_all_tags(start, end)
        self.marker_tag.set_property("invisible", self.preferences.get("hide_markers", True))
        # All supported syntax is line-local. GTK shifts unaffected tags itself.
        offset = start.get_offset()
        text = self.buffer.get_text(start, end, True)
        for tag, first, last in spans(text):
            if tag.startswith("heading"):
                first = text.rfind("\n", 0, first) + 1
            self.buffer.apply_tag_by_name(tag, self.buffer.get_iter_at_offset(offset + first),
                                         self.buffer.get_iter_at_offset(offset + last))
        return GLib.SOURCE_REMOVE

    def snapshot(self):
        return (self.text(), self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset(),
                self.buffer.get_iter_at_mark(self.buffer.get_selection_bound()).get_offset())

    def record_history(self):
        state = self.snapshot()
        if self.history_index >= 0 and self.history[self.history_index][0] == state[0]:
            return
        self.history = self.history[:self.history_index + 1] + [state]
        self.history = trim_history(self.history)
        self.history_index = len(self.history) - 1

    def begin_action(self, *_):
        if not self.restoring and not self.user_action_depth and self.history_index >= 0:
            self.history[self.history_index] = self.snapshot()
        self.user_action_depth += 1

    def end_action(self, *_):
        self.user_action_depth = max(0, self.user_action_depth - 1)
        if not self.restoring and not self.user_action_depth:
            self.record_history()
        if not self.user_action_depth and not self.loading:
            if self.restoring:
                self.flush()
            else:
                self.schedule_save()

    def undo(self, redo=False):
        target = self.history_index + (1 if redo else -1)
        if 0 <= target < len(self.history):
            self.restoring = True
            self.history_index = target
            text, cursor, bound = self.history[target]
            self.buffer.begin_user_action()
            try:
                self.buffer.set_text(text)
                self.buffer.select_range(self.buffer.get_iter_at_offset(cursor), self.buffer.get_iter_at_offset(bound))
            finally:
                self.buffer.end_user_action()
                self.restoring = False

    def load_editor(self, note):
        state = self.tab_states.pop(note["id"], None)
        self.loading = True
        self.note = note
        self.title_entry.set_text(note["title"])
        self.buffer.set_text(note["text"])
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.loading = False
        self.dirty = False
        self.history = []
        self.history_index = -1
        self.record_history()
        if self.style_source:
            GLib.source_remove(self.style_source)
            self.style_source = 0
        self.restyle(full=True)
        if self.preferences.get("hide_markers", True):
            first = self.buffer.get_start_iter()
            if first.has_tag(self.marker_tag):
                first.forward_to_tag_toggle(self.marker_tag)
                self.buffer.place_cursor(first)
        if state and state["text"] == note["text"]:
            self.history = list(state["history"])
            self.history_index = state["history_index"]
            cursor, bound = state["selection"]
            self.buffer.select_range(self.buffer.get_iter_at_offset(cursor), self.buffer.get_iter_at_offset(bound))
            if self.scroller is not None:
                if self.scroll_source:
                    GLib.source_remove(self.scroll_source)
                self.scroll_source = GLib.idle_add(self.restore_scroll, note["id"], state["scroll"])

    def cache_current_tab(self):
        text, cursor, bound = self.snapshot()
        self.tab_states.pop(self.note["id"], None)
        self.tab_states[self.note["id"]] = dict(
            text=text, history=list(self.history), history_index=self.history_index,
            selection=(cursor, bound),
            scroll=self.scroller.get_vadjustment().get_value() if self.scroller is not None else 0)
        retained = sum(history_bytes(state["history"]) for state in self.tab_states.values())
        while retained > TAB_CACHE_BYTES and self.tab_states:
            oldest = next(iter(self.tab_states))
            retained -= history_bytes(self.tab_states.pop(oldest)["history"])

    def restore_scroll(self, note_id, value):
        self.scroll_source = 0
        if self.note["id"] == note_id:
            self.scroller.get_vadjustment().set_value(value)
        return GLib.SOURCE_REMOVE

    def update_tab_title(self):
        key = self.note["id"]
        self.tab_titles[key] = self.title_entry.get_text()
        if key in self.tab_labels:
            self.tab_labels[key].set_label(self.tab_titles[key] or "Untitled")

    def size_tabs(self, _widget=None, allocation=None):
        available = self.tab_scroll.get_allocated_width()
        width = tab_width(available, len(self.open_tabs))
        for tab in self.tab_widgets.values():
            if tab.get_size_request().width != width:
                tab.set_size_request(width, -1)

    def reveal_active_tab(self, *_):
        if self.reveal_tab and self.note["id"] in self.tab_widgets:
            active = self.tab_widgets[self.note["id"]]
            start = active.get_allocation().x
            adjustment = self.tab_scroll.get_hadjustment()
            adjustment.clamp_page(start, start + active.get_allocated_width())
            self.reveal_tab = False

    def refresh_tabs(self):
        self.syncing_tabs = True
        try:
            # Detach the shared title editor before replacing the active tab.
            parent = self.title_entry.get_parent()
            if parent is not None:
                parent.remove(self.title_entry)
            for key in self.open_tabs:
                if key in self.tab_pages:
                    continue
                page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                tab = Gtk.Box(spacing=4)
                tab.get_style_context().add_class("title-tab")
                label = Gtk.Button(label=self.tab_titles.get(key) or "Untitled")
                label.set_relief(Gtk.ReliefStyle.NONE)
                label.set_tooltip_text("Title")
                label.get_child().set_ellipsize(Pango.EllipsizeMode.END)
                label.get_child().set_xalign(0)
                label.connect("clicked", lambda _, note_id=key: self.open_note(note_id))
                tab.pack_start(label, True, True, 0)
                close = self.button("close", "Close tab", lambda _, note_id=key: self.close_tab(note_id))
                close.get_style_context().remove_class("chrome-button")
                close.get_style_context().add_class("close-tab")
                tab.pack_end(close, False, False, 0)
                self.tab_strip.pack_start(tab, True, True, 0)
                self.tab_widgets[key] = tab
                self.tab_pages[key] = page
                self.tab_labels[key] = label
                self.notebook.append_page(page, None)
                page.show()
            active = self.tab_pages[self.note["id"]]
            if self.overlay.get_parent() is None:
                active.pack_start(self.overlay, True, True, 0)
            elif self.overlay.get_parent() != active:
                self.overlay.reparent(active)
            active.set_child_packing(self.overlay, True, True, 0, Gtk.PackType.START)
            for key in list(self.tab_pages):
                if key not in self.open_tabs:
                    self.notebook.remove_page(self.notebook.page_num(self.tab_pages.pop(key)))
                    self.tab_labels.pop(key, None)
                    self.tab_widgets.pop(key).destroy()
            self.notebook.set_current_page(self.notebook.page_num(active))
            for key, label in self.tab_labels.items():
                label.set_label(self.tab_titles.get(key) or "Untitled")
                tab = self.tab_widgets[key]
                context = tab.get_style_context()
                context.remove_class("active")
                label.show()
                if key == self.note["id"]:
                    context.add_class("active")
                    label.set_no_show_all(True)
                    label.hide()
                    tab.pack_start(self.title_entry, True, True, 0)
                    tab.reorder_child(self.title_entry, 0)
                else:
                    label.set_no_show_all(False)
            self.tab_strip.show_all()
            self.reveal_tab = True
            self.size_tabs()
            self.tab_strip.queue_resize()
        finally:
            self.syncing_tabs = False

    def tab_switched(self, notebook, page, _number):
        if self.syncing_tabs:
            return
        key = next((key for key, widget in self.tab_pages.items() if widget == page), None)
        if key is None or key == self.note["id"]:
            return
        if not self.open_note(key):
            self.syncing_tabs = True
            try:
                notebook.set_current_page(notebook.page_num(self.tab_pages[self.note["id"]]))
            finally:
                self.syncing_tabs = False

    def activate_note(self, note, tabs=None):
        if not self.flush():
            return False
        opened = list(self.open_tabs if tabs is None else tabs)
        if note["id"] not in opened:
            opened.append(note["id"])
        candidate = dict(self.preferences, last_note=note["id"], open_tabs=opened)
        try:
            self.store.save_preferences(candidate)
        except StorageError as exc:
            self.preferences_dirty = True
            self.report(exc)
            return False
        self.cache_current_tab()
        self.open_tabs = opened
        self.preferences = candidate
        self.preferences_dirty = False
        self.tab_titles[note["id"]] = note["title"]
        self.load_editor(note)
        self.refresh_tabs()
        for popover in self.popovers:
            popover.popdown()
        self.view.grab_focus()
        return True

    def open_note(self, note_id):
        if not self.flush():
            return False
        if note_id == self.note["id"]:
            for popover in self.popovers:
                popover.popdown()
            return True
        try:
            note = self.store.load_note(note_id)
        except StorageError as exc:
            self.report(exc)
            return False
        return self.activate_note(note)

    def close_tab(self, note_id):
        if note_id not in self.open_tabs or not self.flush():
            return False
        if len(self.open_tabs) == 1:
            return self.request_quit()
        remaining = [key for key in self.open_tabs if key != note_id]
        if note_id == self.note["id"]:
            index = self.open_tabs.index(note_id)
            try:
                note = self.store.load_note(remaining[min(index, len(remaining) - 1)])
            except StorageError as exc:
                self.report(exc)
                return False
            if not self.activate_note(note, remaining):
                return False
        else:
            candidate = dict(self.preferences, open_tabs=remaining)
            try:
                self.store.save_preferences(candidate)
            except StorageError as exc:
                self.preferences_dirty = True
                self.report(exc)
                return False
            self.preferences = candidate
            self.open_tabs = remaining
            self.refresh_tabs()
        self.tab_states.pop(note_id, None)
        self.tab_titles.pop(note_id, None)
        return True

    def switch_tab(self, direction):
        index = self.open_tabs.index(self.note["id"])
        return self.open_note(self.open_tabs[(index + direction) % len(self.open_tabs)])

    def request_quit(self):
        if not self.flush():
            return False
        self.close()
        return True

    def new_note(self, *_):
        if not self.flush():
            return
        try:
            note = self.store.create_note()
        except StorageError as exc:
            self.report(exc)
            return
        self.activate_note(note)

    def request_delete_note(self, note_id):
        if not self.flush():
            return False
        try:
            note = self.store.load_note(note_id)
        except StorageError as exc:
            self.report(exc)
            return False
        for popover in self.popovers:
            popover.popdown()
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=f'Delete “{note["title"] or "Untitled"}”?')
        dialog.format_secondary_text("The note will be moved to trash.")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        delete = dialog.add_button("Delete", Gtk.ResponseType.ACCEPT)
        delete.get_style_context().add_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        try:
            confirmed = dialog.run() == Gtk.ResponseType.ACCEPT
        finally:
            dialog.destroy()
        return self.delete_note(note_id) if confirmed else False

    def delete_note(self, note_id):
        if not self.flush():
            return False
        active = note_id == self.note["id"]
        replacement = None
        created = False
        preferences_written = False
        try:
            tabs = [key for key in self.open_tabs if key != note_id]
            if active:
                remaining = [note for note in self.store.list_notes() if note["id"] != note_id]
                if tabs:
                    replacement = self.store.load_note(tabs[-1])
                elif remaining:
                    replacement = max(remaining, key=lambda note: self.store.note_modified(note["id"]))
                else:
                    replacement = self.store.create_note(title="note1")
                    created = True
                if replacement["id"] not in tabs:
                    tabs.append(replacement["id"])
            next_preferences = dict(self.preferences, open_tabs=tabs,
                                    last_note=replacement["id"] if active else self.note["id"])
            if active or tabs != self.open_tabs:
                self.store.save_preferences(next_preferences)
                preferences_written = True
            self.store.delete_note(note_id)
        except StorageError as exc:
            if preferences_written:
                try:
                    self.store.save_preferences(self.preferences)
                except StorageError:
                    self.preferences_dirty = True
            if created:
                try:
                    self.store.delete_note(replacement["id"])
                except StorageError:
                    pass
            if active:
                self.dirty = True  # Retry can restore the editor after an I/O failure.
                self.preferences_dirty = True
            self.report(exc)
            return False
        self.preferences = next_preferences
        self.preferences_dirty = False
        self.open_tabs = tabs
        self.tab_states.pop(note_id, None)
        self.tab_titles.pop(note_id, None)
        if active:
            self.tab_titles[replacement["id"]] = replacement["title"]
            self.load_editor(replacement)
        self.refresh_tabs()
        self.show_notes()
        return True

    def popover(self, anchor, width, above=False):
        # A single temporary surface at a time; release old widget references.
        for previous in self.popovers:
            previous.destroy()
        self.popovers.clear()
        popover = Gtk.Popover.new(anchor)
        popover.get_style_context().add_class("justnotes-surface")
        popover.set_position(Gtk.PositionType.TOP if above else Gtk.PositionType.BOTTOM)
        anchor.get_style_context().add_class("popover-open")
        def clear_active(*_):
            anchor.get_style_context().remove_class("popover-open")
        popover.connect("closed", clear_active)
        popover.connect("destroy", clear_active)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_border_width(10)
        box.set_size_request(width - 20, -1)
        if above:
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_propagate_natural_height(True)
            scroller.set_max_content_height(max(180, self.get_allocated_height() - 140))
            scroller.add(box)
            popover.add(scroller)
        else:
            popover.add(box)
        self.popovers.append(popover)
        return popover, box

    @staticmethod
    def action_button(box, label, callback, detail=None, keycap=False):
        button = Gtk.Button()
        row = Gtk.Box(spacing=12)
        title = Gtk.Label(label=label, xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(24)
        row.pack_start(title, True, True, 0)
        if detail:
            metadata = Gtk.Label(label=detail)
            metadata.get_style_context().add_class("keycap" if keycap else "muted")
            row.pack_end(metadata, False, False, 0)
        button.add(row)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("menu-row")
        button.connect("clicked", callback)
        box.pack_start(button, False, False, 0)
        return button

    def show_notes(self, *_):
        if not self.flush():
            return
        try:
            notes = self.store.list_notes()
            modified = {note["id"]: self.store.note_modified(note["id"]) for note in notes}
            notes.sort(key=lambda note: -modified[note["id"]])
        except StorageError as exc:
            self.report(exc)
            return
        popover, box = self.popover(self.notes_button, 300)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(320)
        scroller.set_propagate_natural_height(True)
        scroller.set_min_content_width(280)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroller.add(rows)
        box.pack_start(scroller, True, True, 0)
        for note in notes:
            row = Gtk.Box(spacing=4)
            rows.pack_start(row, False, False, 0)
            button = self.action_button(row, note["title"] or "Untitled",
                                        lambda _, key=note["id"]: self.open_note(key),
                                        modified_label(modified[note["id"]]))
            button.set_hexpand(True)
            row.set_child_packing(button, True, True, 0, Gtk.PackType.START)
            if note["id"] == self.note["id"]:
                button.get_style_context().add_class("current-note")
            button.set_tooltip_text(note["title"] or "Untitled")
            delete = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
            delete.set_relief(Gtk.ReliefStyle.NONE)
            delete.get_style_context().add_class("delete-note")
            delete.set_tooltip_text("Delete")
            delete.get_accessible().set_name("Delete " + (note["title"] or "Untitled"))
            delete.connect("clicked", lambda _, key=note["id"]: self.request_delete_note(key))
            row.pack_end(delete, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 0)
        self.action_button(box, "New note", self.new_note, "Ctrl+N", keycap=True)
        self.action_button(box, "Open file…", self.import_file, "Ctrl+O", keycap=True)
        popover.show_all()
        popover.popup()

    def import_file(self, *_):
        if not self.flush():
            return
        for popover in self.popovers:
            popover.popdown()
        dialog = Gtk.FileChooserDialog(title="Import a text or Markdown file", transient_for=self,
                                       action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Import", Gtk.ResponseType.OK)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Text and Markdown (*.txt, *.md)")
        file_filter.add_pattern("*.[tT][xX][tT]")
        file_filter.add_pattern("*.[mM][dD]")
        dialog.add_filter(file_filter)
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                path = Path(dialog.get_filename())
                if path.suffix.lower() not in (".txt", ".md"):
                    raise StorageError("Choose a .txt or .md file.")
                text = path.read_text(encoding="utf-8-sig")
                note = self.store.create_note(text=text, title=path.stem)
                self.activate_note(note)
        except (OSError, UnicodeError, StorageError) as exc:
            self.report(f"Import failed: {exc}")
        finally:
            dialog.destroy()

    def apply_format(self, marker=None, level=None):
        selection = self.buffer.get_selection_bounds()
        if selection:
            first, last = (iterator.get_offset() for iterator in selection)
        else:
            first = last = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        if level is not None:
            first, last, replacement = heading_edit(self.text(), first, last, level)
            selected_start = 0 if selection else len(replacement)
            selected_end = len(replacement)
        else:
            first, last, replacement, selected_start, selected_end = toggle_inline(
                self.text(), first, last, marker)
        self.buffer.begin_user_action()
        try:
            self.buffer.delete(self.buffer.get_iter_at_offset(first), self.buffer.get_iter_at_offset(last))
            self.buffer.insert(self.buffer.get_iter_at_offset(first), replacement)
            self.buffer.select_range(self.buffer.get_iter_at_offset(first + selected_end),
                                     self.buffer.get_iter_at_offset(first + selected_start))
        finally:
            self.buffer.end_user_action()
        self.flush()
        for popover in self.popovers:
            popover.popdown()
        self.view.grab_focus()

    def show_formatting(self, *_):
        popover, box = self.popover(self.format_button, 272)
        inline = Gtk.Box(spacing=6, homogeneous=True)
        box.pack_start(inline, False, False, 0)
        self.format_action(inline, "<b>B</b>  Bold", lambda *_: self.apply_format(marker="**"), "Bold")
        self.format_action(inline, "<i>I</i>  Italic", lambda *_: self.apply_format(marker="*"), "Italic")
        strike = Gtk.Box()
        box.pack_start(strike, False, False, 4)
        self.format_action(strike, "<s>S</s>  Strikethrough", lambda *_: self.apply_format(marker="~"),
                           "Strikethrough")
        self.section_label(box, "HEADING")
        headings = Gtk.Box(spacing=4, homogeneous=True)
        box.pack_start(headings, False, False, 0)
        for level in range(1, 7):
            self.format_action(headings, f"H{level}", lambda _, value=level: self.apply_format(level=value),
                               f"Heading {level}")
        self.section_label(box, "EDITOR FONT")
        fonts = Gtk.Box(spacing=6)
        box.pack_start(fonts, False, False, 0)
        font_button = Gtk.Button(label=self.preferences.get("font") or "System default")
        font_button.get_child().set_ellipsize(Pango.EllipsizeMode.END)
        font_button.get_child().set_max_width_chars(24)
        font_button.get_style_context().add_class("format-action")
        font_button.set_tooltip_text("Font")
        font_button.get_accessible().set_name("Font")
        font_button.connect("clicked", self.choose_font)
        fonts.pack_start(font_button, True, True, 0)
        reset = Gtk.Button(label="Reset")
        reset.get_style_context().add_class("format-action")
        reset.set_tooltip_text("System font")
        reset.set_sensitive(self.preferences.get("font") is not None)
        reset.connect("clicked", lambda *_: self.set_editor_font(None))
        fonts.pack_start(reset, False, False, 0)
        hide_markers = Gtk.CheckButton(label="Hide Markdown markers")
        hide_markers.set_active(self.preferences.get("hide_markers", True))
        hide_markers.set_tooltip_text("Hide markers")
        hide_markers.set_margin_top(8)
        hide_markers.connect("toggled", self.toggle_markers)
        box.pack_start(hide_markers, False, False, 0)
        popover.show_all()
        popover.popup()

    def toggle_markers(self, button):
        self.preferences["hide_markers"] = button.get_active()
        self.preferences_dirty = True
        if self.style_source:
            GLib.source_remove(self.style_source)
            self.style_source = 0
        self.restyle()
        self.flush()

    def choose_font(self, *_):
        for popover in self.popovers:
            popover.popdown()
        dialog = Gtk.FontChooserDialog(title="Editor font", transient_for=self)
        dialog.set_modal(True)
        system_font = Gtk.Settings.get_default().get_property("gtk-font-name")
        dialog.set_font(self.preferences.get("font") or system_font)
        dialog.set_preview_text("The quick brown fox jumps over the lazy dog.")
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                font = dialog.get_font()
                if font:
                    self.set_editor_font(font)
        finally:
            dialog.destroy()
        self.view.grab_focus()

    def set_editor_font(self, font):
        self.preferences["font"] = font
        self.preferences_dirty = True
        self.apply_appearance()
        self.flush()
        for popover in self.popovers:
            popover.popdown()
        self.view.grab_focus()

    @staticmethod
    def format_action(box, markup, callback, tooltip):
        button = Gtk.Button()
        label = Gtk.Label()
        label.set_markup(markup)
        button.add(label)
        button.set_tooltip_text(tooltip)
        button.get_accessible().set_name(tooltip)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("format-action")
        button.connect("clicked", callback)
        box.pack_start(button, True, True, 0)
        return button

    @staticmethod
    def section_label(box, text):
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("section-label")
        box.pack_start(label, False, False, 0)

    @staticmethod
    def info_row(box, left, right, syntax=False):
        row = Gtk.Box(spacing=12)
        row.get_style_context().add_class("info-row")
        name = Gtk.Label(label=left, xalign=0)
        value = Gtk.Label(label=right, xalign=1)
        if syntax and right in ("Italic", "Bold"):
            tag = "i" if right == "Italic" else "b"
            value.set_markup(f"<{tag}>{right}</{tag}>")
        (name if syntax else value).get_style_context().add_class("keycap")
        row.pack_start(name, False, False, 0)
        row.pack_end(value, False, False, 0)
        box.pack_start(row, False, False, 0)

    def show_info(self, *_):
        popover, box = self.popover(self.info_button, 320, above=True)
        for text, css_class in (("justnotes", "about-title"), ("by nightfury", "muted")):
            label = Gtk.Label(label=text, xalign=0)
            label.get_style_context().add_class(css_class)
            label.set_margin_start(4)
            box.pack_start(label, False, False, 0)
        self.section_label(box, "SYNTAX")
        for marker, description in (("*text*", "Italic"), ("**text**", "Bold"),
                                    ("~text~ / ~~text~~", "Strikethrough"), ("# … ######", "Heading 1–6")):
            self.info_row(box, marker, description, syntax=True)
        self.section_label(box, "SHORTCUTS")
        for action, shortcut in (("New note", "Ctrl+N"), ("Open file", "Ctrl+O"),
                                 ("Bold", "Ctrl+B"), ("Italic", "Ctrl+I"),
                                 ("Strikethrough", "Ctrl+Shift+X"),
                                 ("Switch / close tab", "Ctrl+Tab / Ctrl+W"),
                                 ("Quit", "Ctrl+Q"),
                                 ("Undo / Redo", "Ctrl+Z / Ctrl+Shift+Z")):
            self.info_row(box, action, shortcut)
        box.pack_start(Gtk.Separator(), False, False, 0)
        footer = Gtk.Label(label="Notes stay on this computer.", xalign=0)
        footer.get_style_context().add_class("muted")
        footer.get_style_context().add_class("info-footer")
        box.pack_start(footer, False, False, 0)
        popover.show_all()
        popover.popup()

    def apply_appearance(self):
        mode = self.preferences.get("editor_theme", "light")
        self.css.load_from_data((stylesheet(mode) + editor_font_css(self.preferences.get("font"))).encode())
        self.marker_tag.set_property("foreground", EDITOR_COLORS[mode]["accent"])
        self.theme_button.get_child().set_from_icon_name(
            "weather-clear-symbolic" if mode == "dark" else "weather-clear-night-symbolic", Gtk.IconSize.BUTTON)
        self.theme_button.get_child().set_pixel_size(20)
        self.theme_button.set_tooltip_text("Editor theme")

    def toggle_editor_theme(self, *_):
        self.preferences["editor_theme"] = "dark" if self.preferences.get("editor_theme", "light") == "light" else "light"
        self.preferences_dirty = True
        self.apply_appearance()
        self.flush()

    def system_theme_changed(self):
        follow_system(Gtk.Settings.get_default(), self.system_theme.dark)

    def key_pressed(self, _widget, event):
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if not modifiers & Gdk.ModifierType.CONTROL_MASK:
            return False
        key = Gdk.keyval_name(event.keyval).lower()
        shift = bool(modifiers & Gdk.ModifierType.SHIFT_MASK)
        if key == "q":
            self.request_quit()
        elif key == "w":
            self.close_tab(self.note["id"])
        elif key in ("Tab", "tab", "iso_left_tab"):
            self.switch_tab(-1 if shift else 1)
        elif key in ("n", "t"):
            self.new_note()
        elif key == "o":
            self.import_file()
        elif key in ("b", "i") and self.view.has_focus():
            self.apply_format(marker="**" if key == "b" else "*")
        elif key == "x" and shift and self.view.has_focus():
            self.apply_format(marker="~")
        elif key in ("z", "y") and self.view.has_focus():
            self.undo(redo=shift or key == "y")
        else:
            return False
        return True

    def configured(self, *_):
        if not self.get_mapped():
            return False
        state = self.get_window().get_state()
        if state & (Gdk.WindowState.ICONIFIED | Gdk.WindowState.FULLSCREEN):
            return False
        geometry = dict(maximized=bool(state & Gdk.WindowState.MAXIMIZED))
        if not geometry["maximized"]:
            # get_size excludes client decorations and is paired with set_default_size.
            width, height = self.get_size()
            geometry.update(width=max(360, width), height=max(280, height), geometry_version=1)
            if self.can_position:
                geometry["x"], geometry["y"] = self.get_position()
        if all(self.preferences.get(key) == value for key, value in geometry.items()):
            return False
        self.preferences.update(geometry)
        self.preferences_dirty = True
        if self.geometry_source:
            GLib.source_remove(self.geometry_source)
        self.geometry_source = GLib.timeout_add(350, self.save_geometry)
        return False

    def save_geometry(self):
        self.geometry_source = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def recovery_notice(self):
        self.recovery_source = 0
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.OK, text="Recovered saved data")
        dialog.format_secondary_text("A saved file was unreadable. justnotes loaded its last valid recovery copy:\n" +
                                     "\n".join(self.store.recovered))
        dialog.run()
        dialog.destroy()
        return GLib.SOURCE_REMOVE

    def close_requested(self, *_):
        # Capture the final move/resize even if the debounce has not fired.
        self.configured()
        return not self.flush()

    def destroyed(self, *_):
        if self.titlebar_background:
            self.titlebar_background.close()
        if self.system_theme:
            self.system_theme.close()
        for name in ("geometry_source", "style_source", "scroll_source", "save_source", "recovery_source"):
            source = getattr(self, name)
            if source:
                GLib.source_remove(source)
                setattr(self, name, 0)
        for popover in self.popovers:
            popover.destroy()
        self.popovers.clear()
        if self.style_dirty is not None:
            for mark in self.style_dirty:
                self.buffer.delete_mark(mark)
            self.style_dirty = None
        for handler in self.buffer_handlers:
            self.buffer.disconnect(handler)
        self.buffer_handlers.clear()
        self.tab_states.clear()
        self.history.clear()
        self.tab_pages.clear()
        self.tab_labels.clear()
        self.tab_widgets.clear()
        Gtk.StyleContext.remove_provider_for_screen(self.get_screen(), self.css)


class Justnotes(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.store = None

    def do_activate(self):
        window = self.get_active_window()
        if window is not None:
            window.present()
            return
        try:
            self.store = Store(lock=True)
            window = Editor(self, self.store)
        except (StorageError, GLib.Error) as exc:
            dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE,
                                       text="justnotes could not start")
            dialog.format_secondary_text(str(exc))
            dialog.run()
            dialog.destroy()
            self.quit()
            return
        window.present()

    def do_shutdown(self):
        if self.store is not None:
            self.store.close()
        Gtk.Application.do_shutdown(self)


def main():
    GLib.set_prgname("justnotes")
    GLib.set_application_name("justnotes")
    if not Gtk.init_check()[0]:
        print("justnotes needs a graphical desktop (GTK 3 could not open a display).", file=sys.stderr)
        return 1
    Gtk.Window.set_default_icon_name(APP_ID)
    return Justnotes().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
