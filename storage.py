"""Local, atomic persistence. This module has no GTK dependency."""

import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


class StorageError(Exception):
    """An operation failed without discarding the caller's editor state."""


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Store:
    def __init__(self, root=None, lock=False):
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        self.root = Path(root) if root is not None else base / "justnotes"
        self.notes_dir = self.root / "notes"
        self.recovered = []
        self._lock = None
        try:
            self.notes_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if lock:
                self._lock = (self.root / ".lock").open("a")
                try:
                    fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    self.close()
                    raise StorageError("justnotes is already running for this data folder.") from exc
        except OSError as exc:
            self.close()
            raise StorageError(f"Cannot open the notes folder: {exc}") from exc

    def close(self):
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    @staticmethod
    def _note(value):
        return (isinstance(value, dict) and value.get("version") == 1
                and isinstance(value.get("id"), str)
                and re.fullmatch(r"[0-9a-f]{32}", value["id"]) is not None
                and isinstance(value.get("title"), str)
                and isinstance(value.get("text"), str))

    @staticmethod
    def _preferences(value):
        return (isinstance(value, dict) and value.get("version") == 1
                and value.get("editor_theme", "light") in ("light", "dark")
                and isinstance(value.get("maximized", False), bool)
                and (value.get("geometry_version") is None or value["geometry_version"] == 1)
                and all(value.get(key) is None or type(value[key]) is int
                        and -100000 <= value[key] <= 100000 for key in ("x", "y"))
                and isinstance(value.get("always_show_tabs", False), bool)
                and isinstance(value.get("open_tabs", []), list)
                and all(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{32}", key)
                        for key in value.get("open_tabs", []))
                and len(value.get("open_tabs", [])) == len(set(value.get("open_tabs", [])))
                and type(value.get("transparency", 40)) is int
                and 0 <= value.get("transparency", 40) <= 100
                and isinstance(value.get("hide_markers", True), bool)
                and (value.get("font") is None or
                     isinstance(value["font"], str) and 0 < len(value["font"]) <= 512
                     and not any(ord(char) < 32 for char in value["font"]))
                and isinstance(value.get("width"), int)
                and isinstance(value.get("height"), int)
                and 320 <= value["width"] <= 20000
                and 240 <= value["height"] <= 20000
                and (value.get("last_note") is None or
                     isinstance(value["last_note"], str) and
                     re.fullmatch(r"[0-9a-f]{32}", value["last_note"]) is not None))

    @staticmethod
    def _read(path, validate):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not validate(value):
            raise ValueError("invalid file structure")
        return value

    def _load(self, path, validate):
        try:
            return self._read(path, validate)
        except (OSError, ValueError) as primary:
            backup = path.with_suffix(path.suffix + ".bak")
            try:
                value = self._read(backup, validate)
            except (OSError, ValueError) as exc:
                raise StorageError(f"Cannot read {path.name} or its recovery copy: {primary}; {exc}") from exc
            if path.name not in self.recovered:
                self.recovered.append(path.name)
            return value

    @staticmethod
    def _replace(path, data):
        """Write beside the destination so replacement stays on one filesystem."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".justnotes-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            _sync_directory(path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _save(self, path, value, validate):
        if not validate(value):
            raise StorageError(f"Refusing to save invalid data to {path.name}.")
        try:
            data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            backup = path.with_suffix(path.suffix + ".bak")
            if path.exists():
                # Only rotate a valid primary. Never overwrite a good recovery
                # copy with a corrupt file recovered during startup.
                try:
                    previous = self._read(path, validate)
                except (OSError, ValueError):
                    self._read(backup, validate)  # Require a valid recovery copy.
                else:
                    if previous == value:
                        # Opening the same note or window must not sync unchanged
                        # preferences again. Keep a valid recovery copy as well.
                        try:
                            self._read(backup, validate)
                        except (OSError, ValueError):
                            self._replace(backup, data)
                        return
                    self._replace(backup, (json.dumps(previous, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            elif not backup.exists():
                self._replace(backup, data)
            self._replace(path, data)
        except (OSError, ValueError, UnicodeError) as exc:
            raise StorageError(f"Could not save {path.name}: {exc}") from exc

    def _path(self, note_id):
        if not isinstance(note_id, str) or not re.fullmatch(r"[0-9a-f]{32}", note_id):
            raise StorageError("Invalid note ID.")
        return self.notes_dir / f"{note_id}.json"

    def load_note(self, note_id):
        value = self._load(self._path(note_id), self._note)
        if value["id"] != note_id:
            raise StorageError(f"Note ID does not match its filename: {note_id}.")
        return value

    def list_notes(self):
        try:
            # Include backup-only files (for example after an interrupted first save).
            ids = {p.name.split(".")[0] for p in self.notes_dir.iterdir()
                   if re.fullmatch(r"[0-9a-f]{32}\.json(?:\.bak)?", p.name)}
            return sorted((self.load_note(key) for key in ids),
                          key=lambda note: (note["title"].casefold(), note["id"]))
        except OSError as exc:
            raise StorageError(f"Cannot list notes: {exc}") from exc

    def save_note(self, note):
        self._save(self._path(note.get("id")), note, self._note)

    def delete_note(self, note_id):
        """Archive both versions together; a backup must not resurrect a note."""
        path = self._path(note_id)
        note = self.load_note(note_id)
        # A crash between moving the backup and primary must leave a valid
        # primary. Repair a recovered or backup-only note before moving it.
        try:
            self._read(path, self._note)
        except (OSError, ValueError):
            self.save_note(note)
        trash = self.root / "trash"
        archive = trash / f"{note_id}-{uuid.uuid4().hex}"
        moved = []
        try:
            trash.mkdir(mode=0o700, exist_ok=True)
            archive.mkdir(mode=0o700)
            _sync_directory(self.root)
            _sync_directory(trash)
            for original in (path.with_suffix(".json.bak"), path):
                if original.exists():
                    destination = archive / original.name
                    os.replace(original, destination)
                    moved.append((original, destination))
            _sync_directory(archive)
            _sync_directory(self.notes_dir)
            return archive
        except OSError as exc:
            rollback_failed = False
            for original, destination in reversed(moved):
                try:
                    os.replace(destination, original)
                except OSError:
                    rollback_failed = True
            try:
                _sync_directory(self.notes_dir)
                archive.rmdir()
            except OSError:
                pass
            recovery = f" Copies are kept in {archive}." if rollback_failed else ""
            raise StorageError(f"Could not delete note: {exc}.{recovery}") from exc

    def note_modified(self, note_id):
        path = self._path(note_id)
        try:
            if not path.exists():
                path = path.with_suffix(".json.bak")
            return path.stat().st_mtime
        except OSError as exc:
            raise StorageError(f"Cannot read the date for {path.name}: {exc}") from exc

    def create_note(self, text="", title=None):
        if title is None:
            titles = {note["title"] for note in self.list_notes()}
            number = 1
            while f"note{number}" in titles:
                number += 1
            title = f"note{number}"
        note = dict(version=1, id=uuid.uuid4().hex, title=title, text=text)
        self.save_note(note)
        return note

    def load_preferences(self):
        path = self.root / "preferences.json"
        if not path.exists() and not path.with_suffix(".json.bak").exists():
            return dict(version=1, editor_theme="light", width=960, height=600, last_note=None, font=None,
                        hide_markers=True, transparency=40, open_tabs=[], always_show_tabs=False)
        preferences = self._load(path, self._preferences)
        legacy_theme = preferences.pop("theme", None)
        preferences.setdefault("editor_theme", legacy_theme if legacy_theme in ("light", "dark") else "light")
        preferences.setdefault("font", None)
        preferences.setdefault("hide_markers", True)
        preferences.setdefault("transparency", 40)
        preferences.setdefault("open_tabs", [])
        preferences.setdefault("always_show_tabs", False)
        return preferences

    def save_preferences(self, preferences):
        self._save(self.root / "preferences.json", preferences, self._preferences)
