"""Exercise queue transitions through the actual Qt event loop."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from kecheenok.core import DownloadOptions, Job, Store
from kecheenok.qt import QApplication
from kecheenok.ui import MainWindow


class QueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.window = MainWindow(self.store, auto_start=False)
        self.window.paused = True

    def tearDown(self):
        if self.window.worker:
            self.window.worker.cancel()
            self.wait_until(lambda: self.window.worker is None)
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_until(self, predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()
        self.assertTrue(predicate(), "Qt queue operation timed out")

    def test_queue_options_are_snapshots_and_duplicates_are_skipped(self):
        self.window.urls.setPlainText("https://youtu.be/one\nhttps://youtu.be/two")
        self.window.enqueue()
        self.assertEqual(len(self.window.jobs), 2)
        self.window.format.setCurrentIndex(self.window.format.findData("mp3"))
        self.assertEqual(self.window.jobs[0].options.format, "mp4")
        self.window.format.setCurrentIndex(0)
        self.window.urls.setPlainText("https://youtu.be/one")
        self.window.enqueue()
        self.assertEqual(len(self.window.jobs), 2)

    def test_cancel_waiting_and_restore_after_restart(self):
        self.window.add_job(Job("https://youtu.be/one", DownloadOptions()))
        job = self.window.jobs[0]
        self.window.cancel_job(job.id)
        self.assertEqual(job.state, "cancelled")
        self.assertIn(job.id, self.window.history_cards)
        self.window.add_job(Job("https://youtu.be/two", DownloadOptions()))
        self.window.save_state()
        other = MainWindow(self.store, auto_start=False)
        try:
            self.assertTrue(all(j.state == "cancelled" for j in other.jobs))
            self.assertEqual(len(other.history_cards), 2)
        finally:
            other.close()

    def test_error_does_not_block_next_job_and_history_is_persisted(self):
        calls = []
        output = Path(self.temp.name) / "output.mp4"
        output.write_bytes(b"fixture")
        def download(service, job):
            calls.append(job.url)
            if job.url.endswith("bad"):
                raise RuntimeError("Test unavailable video")
            service.emit({"title": "Successful video", "files": [str(output)]})
            return [str(output)]
        self.window.add_job(Job("https://youtu.be/bad", DownloadOptions()))
        self.window.add_job(Job("https://youtu.be/good", DownloadOptions()))
        with patch("kecheenok.ui.DownloadService.download", download):
            self.window.paused = False
            self.window.start_next()
            self.wait_until(lambda: self.window.worker is None and all(j.state in {"error", "done"} for j in self.window.jobs))
        self.assertEqual(calls, ["https://youtu.be/bad", "https://youtu.be/good"])
        self.assertEqual([j.state for j in self.window.jobs], ["error", "done"])
        self.assertEqual(self.window.jobs[1].progress, 100)
        self.assertEqual(len(self.store.load("history.json", [])), 2)

    def test_retry_keeps_original_options_and_prevents_duplicate_retry(self):
        job = Job("https://youtu.be/one", DownloadOptions(format="flac"), state="error", error="fixture")
        self.window.add_job(job)
        self.window.retry_job(job.id)
        self.window.retry_job(job.id)
        self.assertEqual(len(self.window.jobs), 2)
        self.assertEqual(self.window.jobs[1].options.format, "flac")
        self.assertEqual(self.window.jobs[1].state, "waiting")
