# justnotes

A small Linux notes app I made for myself cuz everything I tried looked so extra.
Just write—your notes save automatically on your computer. Includes basic Markdown,
light/dark themes, and a font chooser.

## Install

On Ubuntu/Debian:

```sh
sudo apt install git python3-gi gir1.2-gtk-3.0 libqrencode4
git clone https://github.com/fmustafa176/justnotes.git
cd justnotes
./install.sh
```

Open **justnotes** from your applications menu, or run `~/.local/bin/justnotes`.
You can also run `./justnotes` straight from the downloaded folder.
Other Linux distributions need Python 3, PyGObject, GTK 3, and libqrencode.

Use **+** for a new note and the arrow beside it to open a file.

Use **Notes** to create or import notes and **Aa** for formatting, fonts, and lists.
Use **Settings** for transparency and Markdown visibility. The QR button shares the
current note as an offline QR code (short notes only).
Notes live in `~/.local/share/justnotes/notes/` (or `$XDG_DATA_HOME/justnotes/notes/`).
Updating or reinstalling the app keeps them. Bundled font licenses are in
`assets/fonts/LICENSE.txt`.

Installed copies check GitHub for updates in the background and ask before
installing and restarting. You can also check from **Info → Check for updates**.
