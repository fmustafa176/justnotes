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


class ParserRegressionTests(unittest.TestCase):
    def test_many_unclosed_delimiters_stay_literal(self):
        self.assertEqual(list(spans("*word " * 10000)), [])
        self.assertEqual(list(spans("~word " * 10000)), [])

    def test_long_runs_and_nested_closing_suffixes(self):
        self.assertIn(("italic", 1, 5), list(spans("*a****")))
        found = list(spans("**bold *italic***"))
        self.assertIn(("bold", 2, 15), found)
        self.assertIn(("italic", 8, 14), found)

    def test_inline_spans_do_not_cross_lines(self):
        found = list(spans("*unfinished\nword*\n**yes**\n~no\nstrike~"))
        self.assertEqual([tag for tag, _, _ in found if tag != "marker"], ["bold"])


class ListFormattingTests(unittest.TestCase):
    def test_bullets_toggle_selected_lines_and_exclude_next_line(self):
        from formatting import list_edit
        self.assertEqual(list_edit("one\ntwo\nthree", 0, 8, "bullet"),
                         (0, 7, "• one\n• two", 0, 11))
        self.assertEqual(list_edit("• one\n• two", 0, 11, "bullet")[2], "one\ntwo")

    def test_numbered_list_and_conversion_preserve_content(self):
        from formatting import list_edit
        self.assertEqual(list_edit("one\ntwo\nthree", 0, 13, "number")[2], "1. one\n2. two\n3. three")
        self.assertEqual(list_edit("• one\n☐ two", 0, 11, "number")[2], "1. one\n2. two")
        self.assertEqual(list_edit("1. one\n2. two", 0, 13, "number")[2], "one\ntwo")

    def test_new_numbered_lines_follow_the_preceding_number(self):
        from formatting import list_edit
        self.assertEqual(list_edit("12. previous\nnext", 13, 17, "number")[2], "13. next")

    def test_single_caret_stays_with_its_content(self):
        from formatting import list_edit
        self.assertEqual(list_edit("hello", 2, 2, "bullet"), (0, 5, "• hello", 4, 4))
        self.assertEqual(list_edit("• hello", 4, 4, "bullet"), (0, 7, "hello", 2, 2))
        self.assertEqual(list_edit("", 0, 0, "task"), (0, 0, "☐ ", 2, 2))

    def test_enter_continues_bullets_numbers_and_tasks(self):
        from formatting import list_enter
        for line, expected in (("• one", "\n• "), ("- one", "\n- "), ("* one", "\n* "),
                               ("9. nine", "\n10. "), ("4) four", "\n5) "),
                               ("☑ done", "\n☐ "), ("- [x] done", "\n☐ "),
                               ("  • nested", "\n  • ")):
            with self.subTest(line=line):
                self.assertEqual(list_enter(line, len(line)), (len(line), len(line), expected))
        self.assertIsNone(list_enter("plain text", 10))
        self.assertIsNone(list_enter("1. item", 0))

    def test_enter_leaves_empty_items(self):
        from formatting import list_enter
        for line in ("• ", "10. ", "☐ ", "  - [ ]  "):
            self.assertEqual(list_enter(line, len(line)), (0, len(line), ""))

    def test_tasks_toggle_markdown_and_unicode_forms(self):
        from formatting import toggle_task
        self.assertEqual(toggle_task("☐ task"), (0, 2, "☑ "))
        self.assertEqual(toggle_task("☑ task"), (0, 2, "☐ "))
        self.assertEqual(toggle_task("- [x] task"), (0, 6, "☐ "))
        self.assertIsNone(toggle_task("• item"))

    def test_list_spans_keep_markers_visible_and_format_content(self):
        text = "☑ **done**\n1. next\n- [x] finished"
        found = list(spans(text))
        self.assertIn(("list", 0, 10), found)
        self.assertIn(("strikethrough", 2, 10), found)
        self.assertIn(("bold", 4, 8), found)
        self.assertNotIn(("marker", 0, 2), found)
        self.assertEqual(len([tag for tag, _, _ in found if tag == "list"]), 3)
