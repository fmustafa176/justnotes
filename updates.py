"""Background GitHub updates for the per-user installation; never touches notes."""

from dataclasses import dataclass
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.request import Request, urlopen

REPOSITORY = "fmustafa176/justnotes"
API = f"https://api.github.com/repos/{REPOSITORY}"
REVISION_FILE = ".update-revision"
MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024
REQUIRED = {"app.py", "updates.py", "storage.py", "formatting.py", "appearance.py",
            "system_theme.py", "qr.py", "assets/qr-code-symbolic.svg", "justnotes", "uninstall.sh", "assets/justnotes.svg"}


class UpdateError(Exception):
    pass


def valid_revision(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def installed_revision(directory):
    try:
        value = (Path(directory) / REVISION_FILE).read_text().strip()
    except OSError:
        return None
    return value if valid_revision(value) else None


def record_install(source, destination):
    """Record the checked-out commit, also supporting downloaded update archives."""
    revision = None
    try:
        result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=3, check=False)
        if result.returncode == 0 and valid_revision(result.stdout.strip()):
            revision = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    revision = revision or installed_revision(source)
    (Path(destination) / REVISION_FILE).write_text((revision or "") + "\n")


@dataclass(frozen=True)
class Update:
    revision: str
    summary: str


class UpdateClient:
    def __init__(self, directory, opener=urlopen):
        self.directory = Path(directory)
        self.opener = opener

    def download(self, url, limit, cancelled):
        request = Request(url, headers={"User-Agent": "justnotes-updater",
                                       "Accept": "application/vnd.github+json"})
        chunks = []
        size = 0
        deadline = time.monotonic() + 60
        try:
            with self.opener(request, timeout=8) as response:
                while True:
                    if cancelled.is_set():
                        raise UpdateError("Update cancelled.")
                    if time.monotonic() > deadline:
                        raise UpdateError("The download took too long. Try again later.")
                    chunk = response.read(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise UpdateError("The update download is too large.")
                    chunks.append(chunk)
        except (OSError, ValueError) as exc:
            raise UpdateError("Could not reach GitHub. Check your connection and try again.") from exc
        return b"".join(chunks)

    def json(self, path, cancelled):
        try:
            return json.loads(self.download(API + path, 1024 * 1024, cancelled))
        except (ValueError, UnicodeError) as exc:
            raise UpdateError("GitHub returned an unreadable update response.") from exc

    def check(self, cancelled):
        current = installed_revision(self.directory)
        if current is None:
            raise UpdateError("Reinstall using ./install.sh from a Git clone to enable updates.")
        latest = self.json("/commits/main", cancelled)
        revision = latest.get("sha") if isinstance(latest, dict) else None
        if not valid_revision(revision):
            raise UpdateError("GitHub returned an invalid update version.")
        if revision == current:
            return None
        comparison = self.json(f"/compare/{current}...{revision}?per_page=1", cancelled)
        if not isinstance(comparison, dict):
            raise UpdateError("Could not compare the installed version with GitHub.")
        if comparison.get("status") in ("behind", "identical"):
            return None  # Never downgrade a newer local build.
        if comparison.get("status") != "ahead":
            raise UpdateError("This installation has a different history. Update it using Git.")
        commit = latest.get("commit", {})
        message = commit.get("message", "New version available") if isinstance(commit, dict) else "New version available"
        lines = str(message or "").splitlines()
        summary = lines[0][:160] if lines else "New version available"
        return Update(revision, summary)

    def unpack(self, data, destination):
        seen = set()
        total = 0
        root = None
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                for index, member in enumerate(archive):
                    if index >= 3000:
                        raise UpdateError("The update contains too many files.")
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or not path.parts:
                        raise UpdateError("The update contains an unsafe path.")
                    root = root or path.parts[0]
                    if path.parts[0] != root or not (member.isfile() or member.isdir()):
                        raise UpdateError("The update contains an unsupported archive entry.")
                    relative = PurePosixPath(*path.parts[1:])
                    name = str(relative)
                    total += member.size
                    if total > MAX_UNPACKED or member.size < 0:
                        raise UpdateError("The unpacked update is too large.")
                    if member.isdir():
                        if member.size:
                            raise UpdateError("The update contains an invalid directory.")
                        continue
                    allowed = (len(relative.parts) == 1 and
                               (relative.suffix == ".py" or name in {"justnotes", "uninstall.sh", "install.sh",
                                                                    "justnotes.desktop", "README.md"})
                               or len(relative.parts) > 1 and relative.parts[0] == "assets")
                    if not allowed:
                        continue
                    if name in seen:
                        raise UpdateError("The update contains duplicate files.")
                    seen.add(name)
                    target = destination.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as incoming, target.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    target.chmod(0o755 if name in {"justnotes", "uninstall.sh", "install.sh"} else 0o644)
                    if relative.suffix == ".py":
                        compile(target.read_bytes(), name, "exec")
        except (tarfile.TarError, OSError, SyntaxError, ValueError) as exc:
            raise UpdateError("The downloaded update is incomplete or invalid.") from exc
        if not REQUIRED.issubset(seen):
            raise UpdateError("The downloaded update is missing required app files.")

    def install(self, update, cancelled):
        if not valid_revision(update.revision):
            raise UpdateError("Invalid update version.")
        data = self.download(f"https://codeload.github.com/{REPOSITORY}/tar.gz/{update.revision}",
                             MAX_DOWNLOAD, cancelled)
        directory = self.directory
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise UpdateError("The installed application folder is unavailable.")
            with tempfile.TemporaryDirectory(prefix=".justnotes-update-", dir=directory.parent) as temporary:
                temporary = Path(temporary)
                payload = temporary / "application"
                payload.mkdir(mode=0o700)
                self.unpack(data, payload)
                (payload / REVISION_FILE).write_text(update.revision + "\n")
                if cancelled.is_set():
                    raise UpdateError("Update cancelled.")
                backup = Path(tempfile.mkdtemp(prefix=".justnotes-previous-", dir=directory.parent))
                previous = backup / "application"
                try:
                    directory.rename(previous)
                except OSError:
                    backup.rmdir()
                    raise
                try:
                    payload.rename(directory)
                except OSError as install_error:
                    try:
                        previous.rename(directory)
                    except OSError as restore_error:
                        raise UpdateError(f"Could not finish the update. Your previous app is kept in {previous}.") from restore_error
                    backup.rmdir()
                    raise install_error
                shutil.rmtree(backup, ignore_errors=True)
            # The launcher/menu already point at this stable directory. Refresh
            # the menu icon as well; icon-cache errors must not undo an update.
            self.refresh_icon()
        except OSError as exc:
            raise UpdateError("Could not install the update. The previous version was restored.") from exc

    def refresh_icon(self):
        if self.directory.name != "application" or self.directory.parent.name != "justnotes":
            return
        data_home = self.directory.parent.parent
        icon = data_home / "icons/hicolor/scalable/apps/io.github.nightfury.justnotes.svg"
        temporary = None
        try:
            if icon.parent.is_dir():
                with tempfile.NamedTemporaryFile(dir=icon.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write((self.directory / "assets/justnotes.svg").read_bytes())
                temporary.chmod(0o644)
                os.replace(temporary, icon)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


class UpdateWorker:
    """One bounded background operation; callbacks run only on the GTK loop."""
    def __init__(self, dispatch, remove):
        self.dispatch = dispatch
        self.remove = remove
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.busy = False
        self.pending = 0

    def start(self, operation, callback):
        with self.lock:
            if self.busy or self.cancelled.is_set():
                return False
            self.busy = True
        def run():
            try:
                value, error = operation(self.cancelled), None
            except Exception as exc:
                value, error = None, exc
            with self.lock:
                if self.cancelled.is_set():
                    self.busy = False
                    return
                self.pending = self.dispatch(self.deliver, callback, value, error)
        threading.Thread(target=run, name="justnotes-update", daemon=True).start()
        return True

    def deliver(self, callback, value, error):
        with self.lock:
            self.pending = 0
            self.busy = False
            closed = self.cancelled.is_set()
        if not closed:
            callback(value, error)
        return False

    def close(self):
        with self.lock:
            self.cancelled.set()
            pending, self.pending = self.pending, 0
        if pending:
            self.remove(pending)


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--record-install":
    record_install(sys.argv[2], sys.argv[3])
