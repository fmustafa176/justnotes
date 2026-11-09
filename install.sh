#!/bin/sh
# Optional installation for the current user. Never touches saved notes.
set -eu
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    echo 'Install Python 3, PyGObject, and GTK 3 first.' >&2
    echo 'Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-3.0' >&2
    exit 1
fi
source_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
app_dir="$data_home/justnotes/application"
bin_dir=${JUSTNOTES_BIN_DIR:-"$HOME/.local/bin"}
desktop_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/scalable/apps"
mkdir -p -- "$app_dir/assets/fonts" "$bin_dir" "$desktop_dir" "$icon_dir"
if [ "$source_dir" != "$app_dir" ]; then
    for file in app.py storage.py formatting.py appearance.py justnotes; do
        install -m 644 -- "$source_dir/$file" "$app_dir/$file"
    done
    for file in "$source_dir"/assets/*.svg; do
        install -m 644 -- "$file" "$app_dir/assets/"
    done
    for file in "$source_dir"/assets/fonts/*.ttf "$source_dir/assets/fonts/LICENSE.txt"; do
        install -m 644 -- "$file" "$app_dir/assets/fonts/"
    done
fi
chmod +x -- "$app_dir/justnotes"
ln -sfn -- "$app_dir/justnotes" "$bin_dir/justnotes"
install -m 644 -- "$source_dir/assets/justnotes.svg" "$icon_dir/justnotes.svg"
# Use desktop-entry escaping, including percent field-code escaping.
/usr/bin/python3 - "$source_dir/justnotes.desktop" "$desktop_dir/justnotes.desktop" "$app_dir/justnotes" <<'PY'
import pathlib
import sys

source, destination, executable = sys.argv[1:]
escaped = executable.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
# Desktop strings need another escaping layer for literal backslashes.
escaped = escaped.replace('\\', '\\\\')
text = pathlib.Path(source).read_text().replace('Exec=justnotes', 'Exec="' + escaped + '"')
pathlib.Path(destination).write_text(text)
PY
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$desktop_dir" || true
fi
printf 'Installed justnotes to %s\nLaunch from your application menu or %s/justnotes\n' "$app_dir" "$bin_dir"
