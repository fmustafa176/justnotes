"""Native GTK layout and bundled, offline typography."""

import ctypes
from datetime import datetime
from pathlib import Path


def register_fonts():
    """Register only in this process, without installing fonts or using a network."""
    try:
        fontconfig = ctypes.CDLL("libfontconfig.so.1")
        fontconfig.FcConfigGetCurrent.restype = ctypes.c_void_p
        fontconfig.FcConfigAppFontAddFile.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
        fontconfig.FcConfigAppFontAddFile.restype = ctypes.c_int
        config = fontconfig.FcConfigGetCurrent()
        if not config:
            return
        for path in sorted((Path(__file__).parent / "assets/fonts").glob("*.ttf")):
            fontconfig.FcConfigAppFontAddFile(config, str(path).encode())
    except (OSError, AttributeError):
        # GTK can use its normal serif/sans fallback on other font backends.
        pass


def modified_label(timestamp, now=None):
    """Use actual file modification dates rather than prototype sample dates."""
    now = now or datetime.now()
    date = datetime.fromtimestamp(timestamp)
    days = (now.date() - date.date()).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if 1 < days < 7:
        return date.strftime("%a")
    return f"{date.day} {date:%b}" if date.year == now.year else f"{date.day} {date:%b %Y}"


EDITOR_COLORS = {
    "light": dict(background="#FAFAF9", foreground="#1F2024", muted="#6A6D78",
                  accent="#5B4FE0", selection="rgba(91,79,224,0.16)"),
    "dark": dict(background="#131418", foreground="#E9EAEE", muted="#9B9FAD",
                 accent="#A29BFF", selection="rgba(162,155,255,0.22)"),
}


def titlebar_stylesheet(color, translucent=True):
    """Paint a rounded native-colored frame without fading text or controls."""
    red, green, blue = (round(channel * 255) for channel in (color.red, color.green, color.blue))
    alpha = min(color.alpha, 0.84) if translucent else 1.0
    background = f"rgba({red}, {green}, {blue}, {alpha:.4f})"
    return (
        "headerbar.justnotes-translucent-titlebar { background-image: none; "
        f"background-color: {background}; border-radius: 28px 28px 0 0; border-bottom: none; }} "
        f".window-shell {{ background-image: none; background-color: {background}; "
        "border-radius: 0 0 28px 28px; } "
        "window.maximized headerbar.justnotes-translucent-titlebar, "
        "window.fullscreen headerbar.justnotes-translucent-titlebar, "
        "window.maximized .window-shell, window.fullscreen .window-shell { border-radius: 0; }")


def stylesheet(editor_theme="light"):
    """Editor colors are scoped to content; window decorations stay native."""
    colors = EDITOR_COLORS[editor_theme]
    editor = """
    window.justnotes-window { background-color: transparent; background-image: none; }
    window.justnotes-window decoration { border-radius: 28px; }
    window.justnotes-window.maximized decoration,
    window.justnotes-window.fullscreen decoration { border-radius: 0; }
    .window-shell .tab-scroller,
    .window-shell .tab-scroller > viewport,
    .window-shell notebook.editor-tabs,
    .window-shell notebook.editor-tabs > stack,
    .window-shell .note-page { background: transparent; border: none; box-shadow: none; }
    .editor-surface { background-color: %(background)s; color: %(foreground)s; border-radius: 22px; }
    .window-shell .bottom-panel, .window-shell .bottom-controls { background: transparent; }
    .editor-surface textview, .editor-surface textview text {
        background-color: %(background)s; color: %(foreground)s; caret-color: %(accent)s;
    }
    .window-shell entry.note-title,
    .window-shell entry.note-title:focus,
    .window-shell entry.note-title:backdrop {
        background: transparent; color: @theme_fg_color;
        caret-color: @theme_fg_color; border: none; box-shadow: none;
        padding: 0 12px; min-width: 0; font-weight: normal;
    }
    .editor-surface button.chrome-button { color: %(muted)s; }
    .editor-surface button.chrome-button:hover { color: %(foreground)s; }
    .window-shell .tab-track {
        background-color: alpha(@theme_fg_color, 0.06); border-radius: 20px;
    }
    .window-shell .title-tab {
        background: transparent; border: none; border-radius: 20px; min-height: 38px;
    }
    .window-shell .title-tab.active { background-color: alpha(@theme_fg_color, 0.12); }
    .window-shell .title-tab > button {
        color: @theme_fg_color; background: transparent; box-shadow: none;
        border: none; border-radius: 20px; padding: 0 12px; min-width: 0;
    }
    .window-shell .title-tab > button:hover { background-color: alpha(@theme_fg_color, 0.06); }
    .window-shell .title-tab button.close-tab {
        padding: 0; margin: 0 6px 0 0; min-width: 24px; min-height: 24px;
    }
    .editor-surface textview text selection, .editor-surface entry.note-title selection {
        background-color: %(selection)s; color: %(foreground)s;
    }
    """ % colors
    return editor + """
    .justnotes-surface button.delete-note, .justnotes-surface button.delete-note image { color: #e53935; }
    .justnotes-surface button.delete-note:hover { background-color: rgba(229,57,53,0.12); }
    .justnotes-window button.chrome-button {
        min-width: 36px; min-height: 36px; padding: 0;
    }
    .justnotes-window button.info-button { min-width: 30px; min-height: 30px; }
    .justnotes-surface button.menu-row { min-height: 36px; padding: 0 12px; }
    .justnotes-surface button.menu-row.current-note { font-weight: bold; }
    .justnotes-surface button.format-action { min-height: 32px; padding: 0 7px; }
    .justnotes-surface .section-label { margin: 12px 4px 6px; font-weight: bold; }
    .justnotes-surface .keycap { font-family: monospace; padding: 2px 6px; }
    .justnotes-surface separator { margin: 6px 4px; }
    .justnotes-surface .info-row { padding: 6px 4px; }
    .justnotes-surface .about-title { font-weight: bold; }
    .justnotes-surface .info-footer { padding: 4px; }
    """


def tab_width(available, count, minimum=140):
    """Share the title row evenly, then overflow horizontally at the minimum."""
    return max(minimum, available // max(1, count))


def window_geometry(preferences, workareas):
    """Restore normal geometry on an available monitor, or use a roomy first size."""
    primary = workareas[0]
    saved = preferences.get("geometry_version") == 1
    x, y = preferences.get("x"), preferences.get("y")
    area = next((area for area in workareas if x is not None and y is not None
                 and area[0] <= x < area[0] + area[2]
                 and area[1] <= y < area[1] + area[3]), primary)
    left, top, screen_width, screen_height = area
    width = preferences["width"] if saved else round(screen_width * .56)
    height = preferences["height"] if saved else round(screen_height * .56)
    width = min(max(360, width), screen_width)
    height = min(max(280, height), max(280, screen_height - 48))
    x = x if saved and x is not None else left + round(screen_width * .09)
    y = y if saved and y is not None else top + round(screen_height * .10)
    return width, height, max(left, min(x, left + screen_width - width)), max(
        top, min(y, top + screen_height - height - 48))
