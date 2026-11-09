import unittest

from formatting import heading_edit, inline_edit, spans


class FormattingTests(unittest.TestCase):
    def test_unicode_character_offsets_and_visible_markers(self):
        text = "🌿 **café** and *اردو*"
        found = list(spans(text))
        self.assertIn(("bold", 4, 8), found)
        self.assertIn(("italic", 16, 20), found)
        for tag, start, end in found:
            self.assertGreater(end, start)
            self.assertLessEqual(end, len(text))

    def test_heading_levels(self):
        text = "\n".join("#" * level + " Heading" for level in range(1, 7))
        found = list(spans(text))
        for level in range(1, 7):
            self.assertTrue(any(tag == f"heading{level}" and text[start:end] == "Heading"
                                for tag, start, end in found))
        self.assertEqual(list(spans("####### not a heading")), [])

    def test_basic_inline_and_triple(self):
        self.assertIn(("italic", 1, 2), list(spans("*a*")))
        self.assertIn(("bold", 2, 3), list(spans("**a**")))
        self.assertIn(("bold", 3, 6), list(spans("***abc***")))
        self.assertIn(("italic", 3, 6), list(spans("***abc***")))
        self.assertEqual(list(spans("*unclosed\n**unclosed")), [])

    def test_inline_selection_and_empty_caret(self):
        self.assertEqual(inline_edit("hello world", 6, 11, "**"), ("**world**", 2, 7))
        self.assertEqual(inline_edit("", 0, 0, "*"), ("**", 1, 1))

    def test_heading_replaces_level_for_all_selected_lines(self):
        self.assertEqual(heading_edit("# One\nTwo\nThree", 2, 10, 3),
                         (0, 9, "### One\n### Two"))

    def test_heading_selection_excludes_unselected_final_line(self):
        self.assertEqual(heading_edit("one\ntwo", 0, 4, 2), (0, 3, "## one"))
        self.assertEqual(heading_edit("one\ntwo", 5, 5, 1), (4, 7, "# two"))
        self.assertEqual(heading_edit("", 0, 0, 1), (0, 0, "# "))
