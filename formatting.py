"""Markdown ranges use character offsets, matching Gtk.TextBuffer offsets."""

import re


def spans(text):
    """Yield (tag, start, end); markers stay visible and editable."""
    for match in re.finditer(r"^(#{1,6})[ \t]+([^\n]+)", text, re.MULTILINE):
        yield "marker", match.start(), match.start(2)
        yield f"heading{len(match[1])}", match.start(2), match.end(2)
    # Match triple markers first so nested bold + italic has useful behavior.
    pattern = r"(\*\*\*|\*\*|\*)(?=\S)([^\n]*?\S|\S)\1(?!\*)"
    for match in re.finditer(pattern, text):
        marker, content = match[1], match[2]
        start = match.start(2)
        end = start + len(content)
        yield "marker", match.start(), start
        yield "marker", end, match.end()
        if len(marker) >= 2:
            yield "bold", start, end
        if len(marker) in (1, 3):
            yield "italic", start, end


def inline_edit(text, start, end, marker):
    """Return a replacement plus selection offsets relative to the replacement."""
    selection = text[start:end]
    return marker + selection + marker, len(marker), len(marker) + len(selection)


def heading_edit(text, start, end, level):
    """Apply a heading to selected lines; exclude an unselected final line."""
    first = text.rfind("\n", 0, start) + 1
    last_selected = end - 1 if end > start and text[end - 1:end] == "\n" else end
    last = text.find("\n", last_selected)
    if last == -1:
        last = len(text)
    replacement = "\n".join("#" * level + " " + re.sub(r"^#{1,6}[ \t]+", "", line)
                            for line in text[first:last].split("\n"))
    return first, last, replacement
