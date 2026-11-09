import unittest
from unittest.mock import Mock
from types import SimpleNamespace

try:
    from system_theme import SystemTheme, TitlebarBackground, follow_system, GLib, Gtk
except ImportError:
    SystemTheme = None


@unittest.skipIf(SystemTheme is None, "System PyGObject is unavailable")
class SystemThemeTests(unittest.TestCase):
    def watcher(self):
        # No session bus required: feed real portal variants into the watcher.
        watcher = SystemTheme.__new__(SystemTheme)
        watcher.changed = Mock()
        watcher.dark = None
        watcher.closed = False
        watcher.generation = 0
        watcher.proxy = None
        watcher.cancel = Mock()
        watcher.handlers = []
        return watcher

    def test_system_styling_always_releases_application_overrides(self):
        settings = Mock()
        follow_system(settings)
        settings.reset_property.assert_called_once_with("gtk-application-prefer-dark-theme")
        settings.set_property.assert_not_called()

    def test_desktop_color_scheme_changes_are_followed(self):
        settings = Mock()
        follow_system(settings, True)
        settings.set_property.assert_called_with("gtk-application-prefer-dark-theme", True)
        follow_system(settings, False)
        settings.set_property.assert_called_with("gtk-application-prefer-dark-theme", False)

    def test_live_changes_and_unspecified_preferences(self):
        watcher = self.watcher()
        for value, expected in ((1, True), (2, False), (0, None), (99, None)):
            parameters = GLib.Variant("(ssv)", ("org.freedesktop.appearance", "color-scheme",
                                               GLib.Variant("u", value)))
            watcher.signal(None, None, "SettingChanged", parameters)
            self.assertIs(watcher.dark, expected)
        self.assertEqual(watcher.changed.call_count, 3)

    def test_late_read_cannot_override_a_more_recent_change(self):
        watcher = self.watcher()
        watcher.generation = 1
        parameters = GLib.Variant("(ssv)", ("org.freedesktop.appearance", "color-scheme",
                                           GLib.Variant("u", 1)))
        watcher.signal(None, None, "SettingChanged", parameters)
        proxy = Mock()
        proxy.call_finish.return_value = GLib.Variant("(v)", (GLib.Variant("u", 2),))
        watcher.read_done(proxy, None, 1)
        self.assertTrue(watcher.dark)

    def test_initial_portal_read(self):
        watcher = self.watcher()
        proxy = Mock()
        proxy.call_finish.return_value = GLib.Variant("(v)", (GLib.Variant("u", 1),))
        watcher.read_done(proxy, None, 0)
        self.assertTrue(watcher.dark)
        watcher.changed.assert_called_once()

    def test_close_cancels_pending_work_and_ignores_later_updates(self):
        watcher = self.watcher()
        watcher.close()
        watcher.cancel.cancel.assert_called_once()
        watcher.set_scheme(1)
        watcher.changed.assert_not_called()


@unittest.skipIf(SystemTheme is None, "System PyGObject is unavailable")
class TitlebarBackgroundTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.titlebar = Mock()
        self.native = Mock()
        self.native.get_background_color.return_value = SimpleNamespace(red=0.2, green=0.4, blue=0.6, alpha=1)
        factory = patch("system_theme.Gtk.StyleContext", return_value=self.native)
        factory.start()
        self.addCleanup(factory.stop)
        self.effect = TitlebarBackground(self.titlebar)
        self.addCleanup(self.effect.close)

    def test_only_background_alpha_changes_not_native_buttons_or_widget_opacity(self):
        self.assertIn("rgba(51, 102, 153, 0.8400)", self.effect.last_css)
        self.assertNotIn("button", self.effect.last_css)
        self.assertNotIn("opacity", self.effect.last_css)
        self.titlebar.set_opacity.assert_not_called()
        self.titlebar.get_style_context().get_background_color.assert_not_called()
        self.assertTrue(self.effect.provider.to_string())

    def test_theme_changes_use_new_native_color_not_previous_tint(self):
        self.native.get_background_color.return_value = SimpleNamespace(red=1, green=1, blue=1, alpha=1)
        self.effect.refresh()
        self.assertIn("rgba(255, 255, 255, 0.8400)", self.effect.last_css)

    def test_themes_with_more_transparency_keep_it(self):
        self.native.get_background_color.return_value = SimpleNamespace(red=0, green=0, blue=0, alpha=0.4)
        self.effect.refresh()
        self.assertIn("rgba(0, 0, 0, 0.4000)", self.effect.last_css)

    def test_background_images_can_fall_back_to_native_theme_color(self):
        self.native.get_background_color.return_value = SimpleNamespace(red=0, green=0, blue=0, alpha=0)
        self.native.lookup_color.return_value = (True, SimpleNamespace(red=1, green=1, blue=1, alpha=1))
        self.effect.refresh()
        self.assertIn("rgba(255, 255, 255, 0.8400)", self.effect.last_css)

    def test_own_style_updates_do_not_reload_css_indefinitely(self):
        from unittest.mock import patch
        with patch.object(self.effect.provider, "load_from_data") as load:
            self.effect.refresh()
            load.assert_not_called()

    def test_close_cancels_pending_refresh_and_disconnects_settings(self):
        from unittest.mock import patch
        with patch("system_theme.GLib.idle_add", return_value=42), patch("system_theme.GLib.source_remove") as remove:
            self.effect.schedule()
            self.effect.close()
            remove.assert_called_once_with(42)
        self.assertEqual(self.titlebar.get_settings().disconnect.call_count, 2)
