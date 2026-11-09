"""Basic Markdown styles and reversible editor operations, using character offsets."""

from dataclasses import dataclass
import re
from bisect import bisect_right


@dataclass(frozen=True)
class Inline:
    start: int
    end: int
    content_start: int
    content_end: int
    marker: str

    @property
    def styles(self):
        if self.marker.startswith("~"):
            return {"strikethrough"}
        return {1: {"italic"}, 2: {"bold"}, 3: {"bold", "italic"}}[len(self.marker)]


_DELIMITERS = re.compile(r"\*+|~+")


def inline_runs(text, offset=0):
    # Index possible closing delimiters once per range. Searching the suffix
    # afresh for every unmatched opener makes ordinary '*word ' lists quadratic.
    pending = [(text, offset)]
    while pending:
        fragment, base = pending.pop()
        if isinstance(fragment, Inline):
            yield fragment
            continue
        tokens = list(_DELIMITERS.finditer(fragment))
        closers = {}
        for token in tokens:
            marker = token[0]
            for size in range(1, min(len(marker), 3 if marker[0] == "*" else 2) + 1):
                position = token.end() - size
                if position and not fragment[position - 1].isspace() and fragment[position - 1] != "\\":
                    closers.setdefault(marker[0] * size, []).append(position)
        cursor = 0
        children = []
        for token in tokens:
            first, content_start = token.span()
            marker = token[0]
            if (first < cursor or len(marker) > (3 if marker[0] == "*" else 2)
                    or first and fragment[first - 1] == "\\"
                    or content_start == len(fragment) or fragment[content_start].isspace()):
                continue
            positions = closers.get(marker, [])
            index = bisect_right(positions, content_start)
            if index == len(positions):
                continue
            content_end = positions[index]
            # Inline formatting cannot cross a line boundary.
            newline = fragment.find("\n", content_start, content_end)
            if newline != -1:
                continue
            end = content_end + len(marker)
            cursor = end
            content = fragment[content_start:content_end]
            if not content.strip("*~ \t"):
                continue
            run = Inline(base + first, base + end, base + content_start, base + content_end, marker)
            children.append((run, content))
        # Preserve the original depth-first order without Python recursion.
        if children:
            # Jobs distinguish parsed runs from text ranges to keep nesting order.
            for run, content in reversed(children):
                pending.append((content, run.content_start))
                pending.append((run, None))


def spans(text):
    """Yield style and delimiter ranges; the view decides marker visibility."""
    for match in re.finditer(r"^(#{1,6})[ \t]+([^\n]*)", text, re.MULTILINE):
        yield "marker", match.start(), match.start(2)
        yield f"heading{len(match[1])}", match.start(2), match.end(2)
    for run in inline_runs(text):
        yield "marker", run.start, run.content_start
        yield "marker", run.content_end, run.end
        for style in sorted(run.styles):
            yield style, run.content_start, run.content_end


def inline_edit(text, start, end, marker):
    selection = text[start:end]
    return marker + selection + marker, len(marker), len(marker) + len(selection)


def toggle_inline(text, start, end, marker):
    """Toggle the selected inline style, keeping selection on its text.

    A caret inside a styled phrase toggles that enclosing phrase. Mixed styles
    such as bold + italic retain the other style when one is removed.
    """
    style = "strikethrough" if marker.startswith("~") else "bold" if marker == "**" else "italic"
    enclosing = [run for run in inline_runs(text)
                 if style in run.styles and run.start <= start <= end <= run.end
                 and start <= run.content_end and end >= run.content_start]
    if enclosing:
        run = min(enclosing, key=lambda item: item.end - item.start)
        remaining = ""
        if run.marker == "***":
            remaining = "*" if style == "bold" else "**"
        content = text[run.content_start:run.content_end]
        first = len(remaining) + max(0, min(len(content), start - run.content_start))
        last = len(remaining) + max(0, min(len(content), end - run.content_start))
        if (start != end and run.content_start <= start < end <= run.content_end
                and (start > run.content_start or end < run.content_end)):
            before = text[run.content_start:start]
            selected = text[start:end]
            after = text[end:run.content_end]
            left = wrap_text(before, run.marker)
            middle = wrap_text(selected, remaining) if remaining else selected
            right = wrap_text(after, run.marker)
            selected_first = len(left)
            selected_last = len(left) + len(middle)
            if remaining and selected.strip():
                selected_first += len(selected) - len(selected.lstrip()) + len(remaining)
                selected_last = len(left) + len(selected.rstrip()) + len(remaining)
            return run.start, run.end, left + middle + right, selected_first, selected_last
        return run.start, run.end, remaining + content + remaining, first, last
    replacement, first, last = inline_edit(text, start, end, marker)
    return start, end, replacement, first, last


def wrap_text(text, marker):
    """Keep boundary spaces outside delimiters so split phrases still parse."""
    content = text.strip()
    if not content:
        return text
    leading = len(text) - len(text.lstrip())
    trailing = len(text.rstrip())
    return text[:leading] + marker + content + marker + text[trailing:]


def heading_edit(text, start, end, level):
    """Toggle one heading level across the selected lines."""
    first = text.rfind("\n", 0, start) + 1
    last_selected = end - 1 if end > start and text[end - 1:end] == "\n" else end
    last = text.find("\n", last_selected)
    if last == -1:
        last = len(text)
    lines = text[first:last].split("\n")
    matches = [re.match(r"^(#{1,6})[ \t]+", line) for line in lines]
    remove = all(match and len(match[1]) == level for match in matches)
    prefix = "" if remove else "#" * level + " "
    replacement = "\n".join(prefix + re.sub(r"^#{1,6}[ \t]+", "", line) for line in lines)
    return first, last, replacement
