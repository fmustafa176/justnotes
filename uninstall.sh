#!/bin/sh
# Remove only this user's app files. Notes and preferences stay in place.
set -eu
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
bin_dir=${JUSTNOTES_BIN_DIR:-"$HOME/.local/bin"}
app_id=io.github.nightfury.justnotes
rm -f -- "$bin_dir/justnotes" \
    "$data_home/applications/$app_id.desktop" \
    "$data_home/applications/justnotes.desktop" \
    "$data_home/icons/hicolor/scalable/apps/$app_id.svg" \
    "$data_home/icons/hicolor/scalable/apps/justnotes.svg" \
    "$data_home/icons/hicolor/256x256/apps/$app_id.png"
rm -rf -- "$data_home/justnotes/application"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data_home/applications" >/dev/null 2>&1 || true
fi
printf 'Removed justnotes. Your notes and preferences are still in %s/justnotes/\n' "$data_home"
