"""Prototype colors and bundled, offline typography for GTK 3."""

import ctypes
from datetime import datetime
from pathlib import Path


THEMES = {
    "light": dict(background="#FAFAF9", foreground="#1F2024", muted="#6A6D78",
                  accent="#5B4FE0", line="rgba(20,20,40,0.10)", hover="rgba(20,20,40,0.06)",
                  popover="#FFFFFF", selection="rgba(91,79,224,0.16)", shadow="rgba(20,20,40,0.16)"),
    "dark": dict(background="#131418", foreground="#E9EAEE", muted="#9B9FAD",
                 accent="#A29BFF", line="rgba(255,255,255,0.10)", hover="rgba(255,255,255,0.07)",
                 popover="#1B1C22", selection="rgba(162,155,255,0.22)", shadow="rgba(0,0,0,0.55)"),
}


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


def stylesheet(theme):
    return """
    .justnotes-window, .justnotes-surface {
        color: %(foreground)s;
    }
    .justnotes-window { background-color: %(background)s; }
    .justnotes-window textview, .justnotes-window textview text {
        background-color: %(background)s; color: %(foreground)s;
        caret-color: %(accent)s;
    }
    .justnotes-window entry.note-title {
        background: transparent; color: %(foreground)s;
        caret-color: %(accent)s; font-size: 15px; font-weight: 600;
        border: none; box-shadow: none; padding: 6px 0; min-height: 28px;
    }
    .justnotes-window button, .justnotes-surface button {
        background-image: none; box-shadow: none; text-shadow: none;
        border: none; color: %(foreground)s; outline-offset: -2px;
    }
    .justnotes-window button:focus, .justnotes-surface button:focus {
        outline: 1px solid %(accent)s;
    }
    .justnotes-window button.chrome-button {
        background: transparent; color: %(muted)s;
        min-width: 40px; min-height: 40px; padding: 0;
        border-radius: 10px; font-size: 15px; font-weight: 600;
    }
    .justnotes-window button.chrome-button:hover,
    .justnotes-window button.chrome-button.popover-open {
        background: %(hover)s; color: %(foreground)s;
    }
    .justnotes-window button.chrome-button image {
        -gtk-icon-style: symbolic; -gtk-icon-shadow: none;
    }
    .justnotes-window button.info-button {
        min-width: 30px; min-height: 30px; border: 1px solid %(line)s;
        border-radius: 50%%;
        font-family: "Newsreader 16pt 16pt", Newsreader, Georgia, serif;
        font-size: 16px; font-style: italic; font-weight: 600;
    }
    .justnotes-window textview text selection,
    .justnotes-window entry selection {
        background-color: %(selection)s; color: %(foreground)s;
    }
    popover.justnotes-surface {
        background: %(popover)s; border: 1px solid %(line)s;
        border-radius: 14px; box-shadow: 0 18px 48px %(shadow)s; padding: 0;
    }
    .justnotes-surface button.menu-row {
        background: transparent; border-radius: 9px;
        min-height: 40px; padding: 0 12px; font-size: 14px; font-weight: 500;
    }
    .justnotes-surface button.menu-row:hover,
    .justnotes-surface button.menu-row.current-note {
        background: %(selection)s;
    }
    .justnotes-surface button.menu-row.current-note { color: %(accent)s; }
    .justnotes-surface button.format-action {
        background: %(hover)s; min-height: 36px; min-width: 0;
        border-radius: 8px; padding: 0 7px; font-size: 13px; font-weight: 500;
    }
    .justnotes-surface button.format-action:hover { background: %(selection)s; }
    .justnotes-surface .section-label {
        font-size: 11px; font-weight: 500; color: %(muted)s;
        margin: 12px 4px 6px;
    }
    .justnotes-surface .muted { color: %(muted)s; font-size: 12px; font-weight: 400; }
    .justnotes-surface .keycap {
        font-family: monospace; font-size: 11px; font-weight: 500;
        color: %(muted)s; padding: 2px 6px;
        border-radius: 5px; border: 1px solid %(line)s;
    }
    .justnotes-surface separator { background: %(line)s; min-height: 1px; margin: 6px 4px; }
    .justnotes-surface .info-row { font-size: 13px; padding: 6px 4px; }
    .justnotes-surface .about-title { font-size: 15px; font-weight: 600; }
    .justnotes-surface .info-footer { padding: 4px; }
    """ % THEMES[theme]
