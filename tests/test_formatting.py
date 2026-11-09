import unittest

from formatting import heading_edit, inline_edit, spans, toggle_inline


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

    def test_inline_styles_toggle_off_for_content_or_whole_raw_selection(self):
        for marker in ("*", "**", "~", "~~"):
            text = marker + "hello" + marker
            for first, last in ((len(marker), len(marker) + 5), (0, len(text))):
                with self.subTest(marker=marker, selection=(first, last)):
                    self.assertEqual(toggle_inline(text, first, last, marker), (0, len(text), "hello", 0, 5))

    def test_removing_one_combined_style_keeps_the_other(self):
        self.assertEqual(toggle_inline("***hello***", 3, 8, "**"), (0, 11, "*hello*", 1, 6))
        self.assertEqual(toggle_inline("***hello***", 3, 8, "*"), (0, 11, "**hello**", 2, 7))

    def test_caret_inside_styled_text_can_toggle_it_off(self):
        self.assertEqual(toggle_inline("**hello**", 4, 4, "**"), (0, 9, "hello", 2, 2))

    def test_toggling_a_subselection_preserves_formatting_of_other_words(self):
        self.assertEqual(toggle_inline("**hello world**", 2, 7, "**"),
                         (0, 15, "hello **world**", 0, 5))
        self.assertEqual(toggle_inline("*one two*", 5, 8, "*"),
                         (0, 9, "*one* two", 6, 9))

    def test_nested_styles_and_both_strikethrough_spellings(self):
        text = "**bold *italic* and ~gone~** then ~~also gone~~"
        ranges = [(tag, text[first:last]) for tag, first, last in spans(text)]
        self.assertIn(("bold", "bold *italic* and ~gone~"), ranges)
        self.assertIn(("italic", "italic"), ranges)
        self.assertIn(("strikethrough", "gone"), ranges)
        self.assertIn(("strikethrough", "also gone"), ranges)

    def test_incomplete_and_escaped_delimiters_stay_literal(self):
        for text in ("****", "**", "*unfinished", "~unfinished", r"\*literal*", r"\~literal~"):
            with self.subTest(text=text):
                self.assertEqual(list(spans(text)), [])

    def test_same_heading_level_toggles_off_and_other_level_replaces_it(self):
        self.assertEqual(heading_edit("## One\n## Two", 0, 13, 2), (0, 13, "One\nTwo"))
        self.assertEqual(heading_edit("## One\n## Two", 0, 13, 1), (0, 13, "# One\n# Two"))
