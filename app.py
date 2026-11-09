"""The justnotes GTK 3 interface."""

import sys
from pathlib import Path

from appearance import THEMES, modified_label, register_fonts, stylesheet

register_fonts()

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from formatting import heading_edit, inline_edit, spans
from storage import StorageError, Store


def glyph(name):
    """Use the desktop's symbolic icons and its light/dark recoloring."""
    icon = {"notes": "text-x-generic-symbolic", "moon": "weather-clear-night-symbolic",
            "sun": "weather-clear-symbolic"}[name]
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


class Editor(Gtk.ApplicationWindow):
    def __init__(self, application, store):
        super().__init__(application=application, title="justnotes")
        self.store = store
        self.preferences = store.load_preferences()
        notes = store.list_notes()
        self.note = next((n for n in notes if n["id"] == self.preferences["last_note"]), None)
        if self.note is None:
            self.note = notes[0] if notes else store.create_note()
        self.preferences["last_note"] = self.note["id"]
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
        self.popovers = []
        self.set_default_size(self.preferences["width"], self.preferences["height"])
        self.set_size_request(360, 280)
        self.get_style_context().add_class("justnotes-window")
        icon = Path(__file__).parent / "assets/justnotes.svg"
        self.set_icon_from_file(str(icon))

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
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
        self.title_entry.set_tooltip_text("Note title — saved automatically")
        self.title_entry.get_accessible().set_name("Note title")
        self.title_entry.set_width_chars(12)
        controls.pack_start(self.title_entry, True, True, 0)
        self.notes_button = self.button("notes", "Notes / Open (Ctrl+O)", self.show_notes)
        controls.pack_start(self.notes_button, False, False, 0)
        self.theme_button = self.button("moon", "Switch light / dark theme", self.toggle_theme)
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

        overlay = Gtk.Overlay()
        layout.pack_start(overlay, True, True, 0)
        scroller = Gtk.ScrolledWindow()
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
        self.info_button = self.button("i", "Syntax, shortcuts, and author", self.show_info)
        self.info_button.get_style_context().add_class("info-button")
        self.info_button.set_halign(Gtk.Align.END)
        self.info_button.set_valign(Gtk.Align.END)
        self.info_button.set_margin_end(22)
        self.info_button.set_margin_bottom(20)
        overlay.add_overlay(self.info_button)

        self.buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        self.buffer.create_tag("italic", style=Pango.Style.ITALIC)
        for level, size in enumerate((38, 28, 22, 21, 20, 19), 1):
            self.buffer.create_tag(f"heading{level}", weight=Pango.Weight.SEMIBOLD, scale=size / 19)
        self.marker_tag = self.buffer.create_tag("marker", weight=Pango.Weight.NORMAL,
                                                 style=Pango.Style.NORMAL)
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_theme()
        self.title_entry.connect("changed", self.title_changed)
        self.buffer.connect("changed", self.text_changed)
        self.buffer.connect("begin-user-action", self.begin_action)
        self.buffer.connect("end-user-action", self.end_action)
        self.connect("key-press-event", self.key_pressed)
        self.connect("delete-event", self.close_requested)
        self.connect("configure-event", self.configured)
        self.connect("destroy", self.destroyed)
        self.load_editor(self.note)
        self.show_all()
        self.view.grab_focus()
        if store.recovered:
            GLib.idle_add(self.recovery_notice)

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
        if self.save_current() and self.save_preferences():
            self.error_bar.hide()
            return True
        return False

    def title_changed(self, *_):
        if not self.loading:
            self.dirty = True
            self.flush()

    def text_changed(self, *_):
        if self.loading:
            return
        self.dirty = True
        if not self.user_action_depth:
            self.flush()
        if not self.restoring and not self.user_action_depth:
            self.record_history()
        if not self.style_source:
            self.style_source = GLib.idle_add(self.restyle)

    def restyle(self):
        self.style_source = 0
        start, end = self.buffer.get_bounds()
        self.buffer.remove_all_tags(start, end)
        text = self.text()
        for tag, first, last in spans(text):
            if tag.startswith("heading"):
                first = text.rfind("\n", 0, first) + 1
            self.buffer.apply_tag_by_name(tag, self.buffer.get_iter_at_offset(first), self.buffer.get_iter_at_offset(last))
        return GLib.SOURCE_REMOVE

    def snapshot(self):
        return (self.text(), self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset(),
                self.buffer.get_iter_at_mark(self.buffer.get_selection_bound()).get_offset())

    def record_history(self):
        state = self.snapshot()
        if self.history_index >= 0 and self.history[self.history_index][0] == state[0]:
            return
        self.history = self.history[:self.history_index + 1] + [state]
        self.history = self.history[-500:]
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
            self.flush()

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
        self.restyle()

    def activate_note(self, note):
        if not self.flush():
            return
        previous = self.preferences["last_note"]
        self.preferences["last_note"] = note["id"]
        self.preferences_dirty = True
        if not self.save_preferences():
            self.preferences["last_note"] = previous
            return
        self.load_editor(note)
        for popover in self.popovers:
            popover.popdown()
        self.view.grab_focus()

    def open_note(self, note_id):
        if not self.flush():
            return
        try:
            note = self.store.load_note(note_id)
        except StorageError as exc:
            self.report(exc)
            return
        self.activate_note(note)

    def new_note(self, *_):
        if not self.flush():
            return
        try:
            note = self.store.create_note()
        except StorageError as exc:
            self.report(exc)
            return
        self.activate_note(note)

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
            button = self.action_button(rows, note["title"] or "Untitled",
                                        lambda _, key=note["id"]: self.open_note(key),
                                        modified_label(modified[note["id"]]))
            if note["id"] == self.note["id"]:
                button.get_style_context().add_class("current-note")
            button.set_tooltip_text(f'{note["title"] or "Untitled"}\nID: {note["id"]}')
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
            selected_start = selected_end = len(replacement)
        else:
            replacement, selected_start, selected_end = inline_edit(self.text(), first, last, marker)
        self.buffer.begin_user_action()
        try:
            self.buffer.delete(self.buffer.get_iter_at_offset(first), self.buffer.get_iter_at_offset(last))
            self.buffer.insert(self.buffer.get_iter_at_offset(first), replacement)
            self.buffer.select_range(self.buffer.get_iter_at_offset(first + selected_end),
                                     self.buffer.get_iter_at_offset(first + selected_start))
        finally:
            self.buffer.end_user_action()
        for popover in self.popovers:
            popover.popdown()
        self.view.grab_focus()

    def show_formatting(self, *_):
        popover, box = self.popover(self.format_button, 272)
        inline = Gtk.Box(spacing=6, homogeneous=True)
        box.pack_start(inline, False, False, 0)
        self.format_action(inline, "<b>B</b>  Bold", lambda *_: self.apply_format(marker="**"), "Bold (Ctrl+B)")
        self.format_action(inline, "<i>I</i>  Italic", lambda *_: self.apply_format(marker="*"), "Italic (Ctrl+I)")
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
        font_button.set_tooltip_text("Choose editor font and size")
        font_button.get_accessible().set_name("Choose editor font and size")
        font_button.connect("clicked", self.choose_font)
        fonts.pack_start(font_button, True, True, 0)
        reset = Gtk.Button(label="Reset")
        reset.get_style_context().add_class("format-action")
        reset.set_tooltip_text("Use system default font")
        reset.set_sensitive(self.preferences.get("font") is not None)
        reset.connect("clicked", lambda *_: self.set_editor_font(None))
        fonts.pack_start(reset, False, False, 0)
        popover.show_all()
        popover.popup()

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
        self.apply_theme()
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
        for marker, description in (("*text*", "Italic"), ("**text**", "Bold"), ("# … ######", "Heading 1–6")):
            self.info_row(box, marker, description, syntax=True)
        self.section_label(box, "SHORTCUTS")
        for action, shortcut in (("New note", "Ctrl+N"), ("Open file", "Ctrl+O"),
                                 ("Bold", "Ctrl+B"), ("Italic", "Ctrl+I"),
                                 ("Undo / Redo", "Ctrl+Z / Ctrl+Shift+Z")):
            self.info_row(box, action, shortcut)
        box.pack_start(Gtk.Separator(), False, False, 0)
        footer = Gtk.Label(label="Notes stay on this computer.", xalign=0)
        footer.get_style_context().add_class("muted")
        footer.get_style_context().add_class("info-footer")
        footer.set_tooltip_text(f"Saved automatically. No network or syncing.\nData: {self.store.root}\n"
                                "Ctrl+Y also redoes. Standard copy, cut, paste, and select-all shortcuts work.")
        box.pack_start(footer, False, False, 0)
        popover.show_all()
        popover.popup()

    def apply_theme(self):
        dark = self.preferences["theme"] == "dark"
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", dark)
        self.marker_tag.set_property("foreground", THEMES[self.preferences["theme"]]["accent"])
        self.css.load_from_data((stylesheet(self.preferences["theme"]) +
                                 editor_font_css(self.preferences.get("font"))).encode())
        self.theme_button.get_child().destroy()
        self.theme_button.add(glyph("sun" if dark else "moon"))
        self.theme_button.show_all()

    def toggle_theme(self, *_):
        self.preferences["theme"] = "light" if self.preferences["theme"] == "dark" else "dark"
        self.preferences_dirty = True
        self.apply_theme()
        self.flush()

    def key_pressed(self, _widget, event):
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if not modifiers & Gdk.ModifierType.CONTROL_MASK:
            return False
        key = Gdk.keyval_name(event.keyval).lower()
        shift = bool(modifiers & Gdk.ModifierType.SHIFT_MASK)
        if key == "n":
            self.new_note()
        elif key == "o":
            self.import_file()
        elif key in ("b", "i") and self.view.has_focus():
            self.apply_format(marker="**" if key == "b" else "*")
        elif key in ("z", "y") and self.view.has_focus():
            self.undo(redo=shift or key == "y")
        else:
            return False
        return True

    def configured(self, _window, event):
        if not self.is_maximized():
            self.preferences["width"] = max(360, event.width)
            self.preferences["height"] = max(280, event.height)
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
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.OK, text="Recovered saved data")
        dialog.format_secondary_text("A saved file was unreadable. justnotes loaded its last valid recovery copy:\n" +
                                     "\n".join(self.store.recovered))
        dialog.run()
        dialog.destroy()
        return GLib.SOURCE_REMOVE

    def close_requested(self, *_):
        # Keep the window and its unsaved contents alive on any save failure.
        return not self.flush()

    def destroyed(self, *_):
        for source in (self.geometry_source, self.style_source):
            if source:
                GLib.source_remove(source)
        Gtk.StyleContext.remove_provider_for_screen(self.get_screen(), self.css)


class Justnotes(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.nightfury.justnotes", flags=Gio.ApplicationFlags.FLAGS_NONE)
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
    return Justnotes().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
