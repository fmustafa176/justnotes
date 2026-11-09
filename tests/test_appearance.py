from datetime import datetime
import unittest

from appearance import modified_label, stylesheet

try:
    import app
    import gi
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
except (ImportError, ValueError):
    app = None


class DateTests(unittest.TestCase):
    def test_real_note_dates(self):
        now = datetime(2026, 11, 9, 12)
        for date, expected in ((datetime(2026, 11, 9), "Today"),
                               (datetime(2026, 11, 8), "Yesterday"),
                               (datetime(2026, 11, 6), "Fri"),
                               (datetime(2026, 11, 2), "2 Nov"),
                               (datetime(2025, 11, 2), "2 Nov 2025")):
            with self.subTest(date=date):
                self.assertEqual(modified_label(date.timestamp(), now), expected)


@unittest.skipIf(app is None, "System GTK / Pango is unavailable")
class NativeAppearanceTests(unittest.TestCase):
    def test_native_stylesheet_is_accepted_by_gtk(self):
        provider = app.Gtk.CssProvider()
        provider.load_from_data(stylesheet().encode())
        self.assertTrue(provider.to_string())

    def test_selected_fonts_generate_valid_css(self):
        for mode in ("light", "dark"):
            for font in (None, "DejaVu Sans Mono 14", "Inter Bold 16", "Newsreader 16pt 16pt Italic 19", "Sans 18px"):
                with self.subTest(mode=mode, font=font):
                    provider = app.Gtk.CssProvider()
                    provider.load_from_data((stylesheet(mode) + app.editor_font_css(font)).encode())
        self.assertEqual(app.editor_font_css(None), "")

    def test_editor_palette_changes_do_not_touch_system_preferences(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        view = SimpleNamespace(preferences={"editor_theme": "light", "font": None},
                               css=app.Gtk.CssProvider(), marker_tag=app.Gtk.TextTag(), theme_button=Mock())
        with patch("app.Gtk.Settings.get_default") as settings:
            app.Editor.apply_appearance(view)
            light_css = view.css.to_string()
            view.preferences["editor_theme"] = "dark"
            app.Editor.apply_appearance(view)
            self.assertNotEqual(light_css, view.css.to_string())
            settings.assert_not_called()
        for mode in ("light", "dark"):
            self.assertNotIn("headerbar", stylesheet(mode))
            self.assertNotIn("titlebutton", stylesheet(mode))
            self.assertNotIn("opacity:", stylesheet(mode))

    def test_bundled_fonts_are_used_by_the_native_renderer(self):
        context = PangoCairo.FontMap.get_default().create_context()
        for requested, expected in (("Inter 15", "Inter"),
                                    ("Newsreader 16pt 16pt 19", "Newsreader 16pt 16pt"),
                                    ("Newsreader 16pt 16pt Italic 19", "Newsreader 16pt 16pt")):
            with self.subTest(font=requested):
                description = Pango.FontDescription.from_string(requested)
                font = context.load_font(description)
                self.assertEqual(font.describe().get_family(), expected)
                self.assertEqual(font.describe().get_style(), description.get_style())


class LayoutTests(unittest.TestCase):
    def test_tabs_share_width_until_minimum_then_scroll(self):
        from appearance import tab_width
        self.assertEqual([tab_width(600, n) for n in (1, 2, 3, 4, 5)], [600, 300, 200, 150, 140])
        self.assertGreater(tab_width(600, 5) * 5, 600)
        self.assertEqual(tab_width(80, 1), 140)

    def test_first_window_uses_just_over_quarter_of_screen_and_offset(self):
        from appearance import window_geometry
        geometry = window_geometry(dict(width=360, height=280), [(0, 24, 1920, 1056)])
        width, height, x, y = geometry
        self.assertGreater(width * height / (1920 * 1056), .25)
        self.assertLess(width * height / (1920 * 1056), .35)
        self.assertEqual((x, y), (173, 130))

    def test_saved_geometry_is_stable_including_negative_monitor_positions(self):
        from appearance import window_geometry
        prefs = dict(geometry_version=1, width=850, height=650, x=-1600, y=100)
        areas = [(0, 0, 1920, 1080), (-1920, 0, 1920, 1080)]
        for _ in range(20):
            result = window_geometry(prefs, areas)
            self.assertEqual(result, (850, 650, -1600, 100))
            prefs.update(zip(("width", "height", "x", "y"), result))

    def test_removed_monitor_and_oversized_window_stay_on_screen(self):
        from appearance import window_geometry
        result = window_geometry(dict(geometry_version=1, width=3000, height=2000, x=3000, y=1500),
                                 [(0, 24, 1280, 696)])
        self.assertEqual(result, (1280, 648, 0, 24))
