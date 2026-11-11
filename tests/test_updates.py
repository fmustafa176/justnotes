"""Exercise update downloads, archive validation, rollback and background delivery."""

import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from updates import (UpdateClient, UpdateWorker, Update, UpdateError, REQUIRED,
                     REVISION_FILE, installed_revision, record_install)

OLD = "a" * 40
NEW = "b" * 40


def archive_bytes(extra=(), missing=()):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name in sorted(REQUIRED - set(missing)):
            data = b"# new version\n" if name.endswith(".py") else b"new version\n"
            member = tarfile.TarInfo("justnotes-" + NEW + "/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        for member, data in extra:
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app = self.root / "application"
        self.app.mkdir()
        (self.app / "app.py").write_text("# old version\n")
        (self.app / REVISION_FILE).write_text(OLD)
        self.opener = Mock()
        self.client = UpdateClient(self.app, opener=self.opener)
        self.cancelled = threading.Event()

    def responses(self, *values):
        self.opener.side_effect = [io.BytesIO(json.dumps(value).encode()) for value in values]

    def test_current_version_does_not_download_or_compare(self):
        self.responses({"sha": OLD})
        self.assertIsNone(self.client.check(self.cancelled))
        self.assertEqual(self.opener.call_count, 1)

    def test_newer_remote_commit_is_pinned_and_compared(self):
        self.responses({"sha": NEW, "commit": {"message": "Fix something\nMore detail"}}, {"status": "ahead"})
        update = self.client.check(self.cancelled)
        self.assertEqual(update, Update(NEW, "Fix something"))
        request = self.opener.call_args_list[1].args[0]
        self.assertIn(f"{OLD}...{NEW}", request.full_url)

    def test_newer_local_and_diverged_builds_are_not_overwritten(self):
        for status in ("behind", "identical", "diverged"):
            self.responses({"sha": NEW}, {"status": status})
            if status == "diverged":
                with self.assertRaises(UpdateError):
                    self.client.check(self.cancelled)
            else:
                self.assertIsNone(self.client.check(self.cancelled))

    def test_missing_revision_and_invalid_response_are_reported(self):
        (self.app / REVISION_FILE).unlink()
        with self.assertRaises(UpdateError):
            self.client.check(self.cancelled)
        self.opener.assert_not_called()
        (self.app / REVISION_FILE).write_text(OLD)
        for value in ({"sha": "../wrong"}, [], None):
            self.responses(value)
            with self.subTest(value=value), self.assertRaises(UpdateError):
                self.client.check(self.cancelled)

    def test_offline_and_invalid_json_keep_current_installation(self):
        self.opener.side_effect = OSError("offline")
        with self.assertRaises(UpdateError):
            self.client.check(self.cancelled)
        self.opener.side_effect = [io.BytesIO(b"not json")]
        with self.assertRaises(UpdateError):
            self.client.check(self.cancelled)
        self.assertEqual(installed_revision(self.app), OLD)

    def test_download_is_bounded_and_cancellable(self):
        self.opener.return_value = io.BytesIO(b"123456")
        with self.assertRaises(UpdateError):
            self.client.download("https://example.test", 5, self.cancelled)
        self.cancelled.set()
        self.opener.return_value = io.BytesIO(b"123456")
        with self.assertRaises(UpdateError):
            self.client.download("https://example.test", 10, self.cancelled)

    def test_successful_update_preserves_notes_and_preferences(self):
        notes = self.root / "notes"
        notes.mkdir()
        note = notes / "example.json"
        note.write_text('saved note')
        preferences = self.root / "preferences.json"
        preferences.write_text('saved preferences')
        self.opener.return_value = io.BytesIO(archive_bytes())
        self.client.install(Update(NEW, "New"), self.cancelled)
        self.assertEqual(installed_revision(self.app), NEW)
        self.assertEqual((self.app / "app.py").read_text(), "# new version\n")
        self.assertTrue((self.app / "justnotes").stat().st_mode & 0o100)
        self.assertEqual(note.read_text(), "saved note")
        self.assertEqual(preferences.read_text(), "saved preferences")
        self.assertFalse(list(self.root.glob(".justnotes-*")))
        self.assertTrue(self.opener.call_args.args[0].full_url.endswith(NEW))

    def test_real_app_archive_updates_and_imports_with_notes_intact(self):
        from storage import Store
        source = Path(__file__).resolve().parent.parent
        store = Store(self.root)
        note = store.create_note(text="keep my real note")
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode="w:gz") as archive:
            for name in REQUIRED:
                archive.add(source / name, arcname="justnotes-" + NEW + "/" + name)
            for path in (source / "assets/fonts").iterdir():
                if path.is_file():
                    archive.add(path, arcname="justnotes-" + NEW + "/assets/fonts/" + path.name)
        self.opener.return_value = io.BytesIO(package.getvalue())
        self.client.install(Update(NEW, "Actual app"), self.cancelled)
        smoke = subprocess.run(["/usr/bin/python3", "-c",
                                "import app; assert hasattr(app.Editor, 'check_updates')"],
                               cwd=self.app, capture_output=True, text=True)
        self.assertEqual(smoke.returncode, 0, smoke.stderr)
        self.assertEqual(store.load_note(note["id"])["text"], "keep my real note")
        self.assertEqual(installed_revision(self.app), NEW)
        self.assertTrue((self.app / "assets/fonts/LICENSE.txt").is_file())

    def test_unsafe_and_incomplete_archives_are_rejected_before_install(self):
        bad = tarfile.TarInfo("justnotes/../../escaped.py")
        link = tarfile.TarInfo("justnotes/assets/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../notes"
        bad_python = tarfile.TarInfo("justnotes-" + NEW + "/broken.py")
        duplicate = tarfile.TarInfo("justnotes-" + NEW + "/app.py")
        samples = (b"not gzip", archive_bytes(missing={"app.py"}),
                   archive_bytes([(bad, b"bad")]), archive_bytes([(link, b"")]),
                   archive_bytes([(bad_python, b"invalid python !!!")]),
                   archive_bytes([(duplicate, b"# duplicate")]))
        for data in samples:
            self.opener.return_value = io.BytesIO(data)
            with self.subTest(data_size=len(data)), self.assertRaises(UpdateError):
                self.client.install(Update(NEW, "New"), self.cancelled)
            self.assertEqual(installed_revision(self.app), OLD)
            self.assertFalse((self.root.parent / "escaped.py").exists())

    def test_unpacked_archive_size_limit(self):
        self.opener.return_value = io.BytesIO(archive_bytes())
        with patch("updates.MAX_UNPACKED", 1), self.assertRaises(UpdateError):
            self.client.install(Update(NEW, "New"), self.cancelled)
        self.assertEqual(installed_revision(self.app), OLD)

    def test_install_failure_restores_previous_directory(self):
        original = Path.rename
        def fail_payload(path, destination):
            if path.name == "application" and path.parent.name.startswith(".justnotes-update-"):
                raise OSError("disk full")
            return original(path, destination)
        self.opener.return_value = io.BytesIO(archive_bytes())
        with patch.object(Path, "rename", fail_payload), self.assertRaises(UpdateError):
            self.client.install(Update(NEW, "New"), self.cancelled)
        self.assertEqual(installed_revision(self.app), OLD)
        self.assertEqual((self.app / "app.py").read_text(), "# old version\n")

    def test_failed_rollback_keeps_previous_files_for_recovery(self):
        original = Path.rename
        def fail_both(path, destination):
            if path.name == "application" and path.parent.name.startswith((".justnotes-update-", ".justnotes-previous-")):
                raise OSError("permission lost")
            return original(path, destination)
        self.opener.return_value = io.BytesIO(archive_bytes())
        with patch.object(Path, "rename", fail_both), self.assertRaisesRegex(UpdateError, "previous app is kept"):
            self.client.install(Update(NEW, "New"), self.cancelled)
        backups = list(self.root.glob(".justnotes-previous-*/application"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(installed_revision(backups[0]), OLD)

    def test_cancel_before_replacement_keeps_previous_version(self):
        self.opener.return_value = io.BytesIO(archive_bytes())
        original = self.client.unpack
        def cancel_after_unpack(*args):
            original(*args)
            self.cancelled.set()
        with patch.object(self.client, "unpack", side_effect=cancel_after_unpack), self.assertRaises(UpdateError):
            self.client.install(Update(NEW, "New"), self.cancelled)
        self.assertEqual(installed_revision(self.app), OLD)

    def test_installer_records_git_revision_and_archive_fallback(self):
        source = self.root / "clone"
        source.mkdir()
        result = subprocess.CompletedProcess([], 0, stdout=NEW + "\n")
        with patch("updates.subprocess.run", return_value=result):
            record_install(source, self.app)
        self.assertEqual(installed_revision(self.app), NEW)
        (source / REVISION_FILE).write_text(OLD)
        with patch("updates.subprocess.run", side_effect=FileNotFoundError):
            record_install(source, self.app)
        self.assertEqual(installed_revision(self.app), OLD)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.ready = threading.Event()
        self.queued = None
        def dispatch(callback, *args):
            self.queued = (callback, args)
            self.ready.set()
            return 42
        self.remove = Mock()
        self.worker = UpdateWorker(dispatch, self.remove)
        self.addCleanup(self.worker.close)

    def deliver(self):
        self.assertTrue(self.ready.wait(2))
        callback, args = self.queued
        callback(*args)

    def test_check_runs_off_thread_and_delivers_on_caller_thread(self):
        owner = threading.get_ident()
        callback = Mock()
        self.worker.start(lambda _: threading.get_ident(), callback)
        self.deliver()
        self.assertNotEqual(callback.call_args.args[0], owner)
        self.assertIsNone(callback.call_args.args[1])
        self.assertFalse(self.worker.busy)

    def test_duplicate_work_is_rejected_and_errors_are_delivered(self):
        callback = Mock()
        def fail(_):
            raise UpdateError("offline")
        self.worker.start(fail, callback)
        self.assertFalse(self.worker.start(fail, callback))
        self.deliver()
        self.assertIsInstance(callback.call_args.args[1], UpdateError)

    def test_close_removes_queued_callback(self):
        callback = Mock()
        self.worker.start(lambda _: "done", callback)
        self.assertTrue(self.ready.wait(2))
        self.worker.close()
        self.remove.assert_called_once_with(42)
        self.queued[0](*self.queued[1])
        callback.assert_not_called()
        self.assertFalse(self.worker.start(lambda _: None, callback))

    def test_close_while_downloading_prevents_delivery(self):
        started = threading.Event()
        finished = threading.Event()
        callback = Mock()
        def wait(cancelled):
            started.set()
            cancelled.wait(2)
            finished.set()
        self.worker.start(wait, callback)
        self.assertTrue(started.wait(2))
        self.worker.close()
        self.assertTrue(finished.wait(2))
        callback.assert_not_called()


try:
    from app import Editor, Justnotes, Gtk
except (ImportError, ValueError):
    Editor = None


@unittest.skipIf(Editor is None, "System GTK unavailable")
class UpdateControllerTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.application = Mock()
        self.view = SimpleNamespace(update_worker=Mock(), update_client=Mock(directory=Path("/tmp/app")),
                                    flush=Mock(return_value=True), update_message=Mock(), configured=Mock(),
                                    get_application=Mock(return_value=self.application))

    def test_background_offline_and_current_version_are_quiet(self):
        Editor.update_checked(self.view, None, UpdateError("offline"))
        Editor.update_checked(self.view, None, None)
        self.view.update_message.assert_not_called()
        Editor.update_checked(self.view, None, UpdateError("offline"), manual=True)
        self.view.update_message.assert_called_once()

    def test_later_never_installs(self):
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.CANCEL
        with patch("app.Gtk.MessageDialog", return_value=dialog):
            Editor.update_checked(self.view, Update(NEW, "New"), None)
        self.view.update_worker.start.assert_not_called()
        self.view.flush.assert_not_called()

    def test_accept_flushes_notes_before_installation(self):
        self.view.update_installed = Mock()
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.ACCEPT
        with patch("app.Gtk.MessageDialog", return_value=dialog):
            Editor.update_checked(self.view, Update(NEW, "New"), None)
        self.view.flush.assert_called_once()
        operation, callback = self.view.update_worker.start.call_args.args
        cancelled = threading.Event()
        operation(cancelled)
        self.view.update_client.install.assert_called_once_with(Update(NEW, "New"), cancelled)
        self.assertIs(callback, self.view.update_installed)

    def test_failed_save_blocks_update_and_restart(self):
        self.view.flush.return_value = False
        dialog = Mock()
        dialog.run.return_value = Gtk.ResponseType.ACCEPT
        with patch("app.Gtk.MessageDialog", return_value=dialog):
            Editor.update_checked(self.view, Update(NEW, "New"), None)
        self.view.update_worker.start.assert_not_called()
        Editor.update_installed(self.view, None, None)
        self.application.quit.assert_not_called()

    def test_successful_update_restarts_only_after_saving(self):
        Editor.update_installed(self.view, None, None)
        self.view.flush.assert_called_once()
        self.assertEqual(self.application.restart_command, ["/tmp/app/justnotes"])
        self.application.quit.assert_called_once()

    def test_failed_install_keeps_app_running(self):
        Editor.update_installed(self.view, None, UpdateError("disk full"))
        self.application.quit.assert_not_called()
        self.view.update_message.assert_called_once()

    def test_shutdown_releases_notes_lock_before_starting_restart_helper(self):
        from types import SimpleNamespace
        events = []
        store = Mock()
        store.close.side_effect = lambda: events.append("close")
        application = SimpleNamespace(store=store, restart_command=["/tmp/app/justnotes"])
        with patch("app.Gtk.Application.do_shutdown", side_effect=lambda _: events.append("shutdown")), \
                patch("app.subprocess.Popen", side_effect=lambda *args, **kwargs: events.append("restart")) as spawn:
            Justnotes.do_shutdown(application)
        self.assertEqual(events, ["close", "shutdown", "restart"])
        command = spawn.call_args.args[0]
        self.assertEqual(command[-1], "/tmp/app/justnotes")
        compile(command[2], "restart helper", "exec")
