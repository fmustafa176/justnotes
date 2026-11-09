# justnotes

A small Linux notes app by nightfury. Just write—your notes save automatically
on your computer. Includes basic Markdown, light/dark themes, and a font chooser.

## Install

On Ubuntu/Debian:

```sh
sudo apt install git python3-gi gir1.2-gtk-3.0
git clone https://github.com/fmustafa176/justnotes.git
cd justnotes
./install.sh
```

Open **justnotes** from your applications menu, or run `~/.local/bin/justnotes`.
You can also run `./justnotes` straight from the downloaded folder.
Other Linux distributions need Python 3, PyGObject, and GTK 3.

Use **Notes** to create or import notes and **Aa** for formatting and fonts.
Notes live in `~/.local/share/justnotes/notes/` (or `$XDG_DATA_HOME/justnotes/notes/`).
Updating or reinstalling the app keeps them. Bundled font licenses are in
`assets/fonts/LICENSE.txt`.

Tests: `/usr/bin/python3 -m unittest discover -s tests`.
