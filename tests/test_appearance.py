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
    def test_both_stylesheets_are_accepted_by_gtk(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                provider = app.Gtk.CssProvider()
                provider.load_from_data(stylesheet(theme).encode())
                self.assertTrue(provider.to_string())

    def test_selected_fonts_generate_valid_css_for_both_themes(self):
        for theme in ("light", "dark"):
            for font in (None, "DejaVu Sans Mono 14", "Inter Bold 16", "Newsreader 16pt 16pt Italic 19",
                         "Sans 18px"):
                with self.subTest(theme=theme, font=font):
                    provider = app.Gtk.CssProvider()
                    provider.load_from_data((stylesheet(theme) + app.editor_font_css(font)).encode())
        self.assertEqual(app.editor_font_css(None), "")

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
