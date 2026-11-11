"""Follow the desktop's optional, local XDG appearance portal."""

import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from appearance import titlebar_stylesheet


class SystemTheme:
    def __init__(self, changed):
        self.changed = changed
        self.dark = None
        self.closed = False
        self.proxy = None
        self.handlers = []
        self.generation = 0
        self.cancel = Gio.Cancellable()
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings", self.cancel, self.ready)

    def ready(self, _source, result):
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return  # No portal/session bus: normal GTK settings still apply.
        if self.closed:
            return
        self.proxy = proxy
        self.handlers = [proxy.connect("g-signal", self.signal),
                         proxy.connect("notify::g-name-owner", self.read)]
        self.read()

    def read(self, *_):
        if self.closed:
            return
        self.generation += 1
        if not self.proxy.get_name_owner():
            self.set_scheme(0)
            return
        self.proxy.call("Read", GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                        Gio.DBusCallFlags.NO_AUTO_START, 1500, self.cancel,
                        self.read_done, self.generation)

    def read_done(self, proxy, result, generation):
        try:
            reply = proxy.call_finish(result)
        except GLib.Error:
            if not self.closed and generation == self.generation:
                self.set_scheme(0)
            return
        if not self.closed and generation == self.generation:
            self.set_scheme(reply.unpack()[0])

    def signal(self, _proxy, _sender, name, parameters):
        if self.closed or name != "SettingChanged":
            return
        namespace, key, value = parameters.unpack()
        if (namespace, key) == ("org.freedesktop.appearance", "color-scheme"):
            self.generation += 1  # An older initial Read must not undo this change.
            self.set_scheme(value)

    def set_scheme(self, value):
        while isinstance(value, GLib.Variant):
            value = value.unpack()
        dark = True if value == 1 else False if value == 2 else None
        if dark != self.dark and not self.closed:
            self.dark = dark
            self.changed()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel.cancel()
        if self.proxy:
            for handler in self.handlers:
                self.proxy.disconnect(handler)
        self.handlers.clear()
        self.proxy = None
        self.changed = None


def initial_color_scheme():
    """Read GNOME's local preference before creating widgets; portal stays authoritative."""
    if "gnome" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":"):
        return None
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.interface", True) if source else None
    if schema is None or not schema.has_key("color-scheme"):
        return None
    settings = Gio.Settings.new_full(schema, None, None)
    return {"prefer-dark": True, "prefer-light": False}.get(settings.get_string("color-scheme"))


def follow_system(settings, system_dark=None):
    # Changing a GTK setting restyles every widget. Apply a portal value directly
    # instead of briefly resetting to the opposite scheme and then changing back.
    key = "gtk-application-prefer-dark-theme"
    if system_dark is None:
        settings.reset_property(key)
    elif settings.get_property(key) != system_dark:
        settings.set_property(key, system_dark)


class TitlebarBackground:
    """Tint the translucent frame black while keeping native window controls."""

    def __init__(self, titlebar, shell=None, translucent=True, transparency=40):
        self.translucent = translucent
        self.transparency = transparency
        self.shell_context = shell.get_style_context() if shell is not None else None
        self.titlebar = titlebar
        self.context = titlebar.get_style_context()
        self.context.add_class("justnotes-translucent-titlebar")
        self.provider = Gtk.CssProvider()
        # A widget-local provider cannot recolor the editor or window buttons.
        self.context.add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if self.shell_context is not None:
            self.shell_context.add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.last_css = None
        self.pending = 0
        self.closed = False
        self.settings = titlebar.get_settings()
        self.settings_handlers = [
            self.settings.connect("notify::gtk-theme-name", self.schedule),
            self.settings.connect("notify::gtk-application-prefer-dark-theme", self.schedule),
        ]
        self.titlebar_handlers = [
            titlebar.connect("style-updated", self.schedule),
            titlebar.connect("state-flags-changed", self.schedule),
            titlebar.connect("destroy", self.close),
        ]
        self.refresh()

    def schedule(self, *_):
        if not self.closed and not self.pending:
            self.pending = GLib.idle_add(self.refresh)

    def native_color(self):
        # Query a separate context without our local background override. This
        # avoids sampling our previous tint when the desktop theme changes.
        native = Gtk.StyleContext()
        native.set_screen(self.titlebar.get_screen())
        native.set_path(self.titlebar.get_path())
        native.set_state(self.titlebar.get_state_flags())
        parent = self.context.get_parent()
        if parent is not None:
            native.set_parent(parent)
        color = native.get_background_color(self.titlebar.get_state_flags())
        if color.alpha == 0:
            found, fallback = native.lookup_color("theme_bg_color")
            if found:
                color = fallback
        return color

    def refresh(self):
        self.pending = 0
        if self.closed:
            return GLib.SOURCE_REMOVE
        css = titlebar_stylesheet(self.native_color(), self.translucent, self.transparency)
        # Loading a provider may emit style-updated; don't create a redraw loop.
        if css != self.last_css:
            self.last_css = css
            self.provider.load_from_data(css.encode())
        return GLib.SOURCE_REMOVE

    def close(self, *_):
        if self.closed:
            return
        self.closed = True
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        for handler in self.settings_handlers:
            self.settings.disconnect(handler)
        self.settings_handlers.clear()
        for handler in self.titlebar_handlers:
            self.titlebar.disconnect(handler)
        self.titlebar_handlers.clear()
        self.context.remove_provider(self.provider)
        if self.shell_context is not None:
            self.shell_context.remove_provider(self.provider)
