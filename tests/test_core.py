from __future__ import annotations

import dataclasses
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from kecheenok.core import (Cancelled, DownloadOptions, DownloadService, EditOptions, Job,
                           ProcessRunner, Store, Tools, download_command, extract_urls, parse_time)
from kecheenok.tooling import extract_named, release_asset, verified_download


class FakeTools:
    def find(self, name):
        return f"C:/Program Files/Kecheenok/tools/{name}.exe"

    def downloader(self):
        return [self.find("yt-dlp")]


class CoreTests(unittest.TestCase):
    def test_times_validate_minutes_and_finite_numbers(self):
        self.assertEqual(parse_time("1:02:03.5"), 3723.5)
        self.assertEqual(parse_time("90"), 90)
        self.assertIsNone(parse_time(""))
        for value in ("-1", "1:60", "NaN", "1::2", "1:2:3:4", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time(value)

    def test_urls_are_deduplicated_without_shell_interpretation(self):
        url = "https://www.youtube.com/watch?v=abc&list=xyz"
        self.assertEqual(extract_urls(f"{url}\n{url}"), [url])
        with self.assertRaises(ValueError):
            extract_urls("file:///tmp/private")
        with self.assertRaises(ValueError):
            extract_urls("https://user:password@example.com/a")

    def test_quality_cap_applies_to_all_fallbacks(self):
        args = download_command("https://youtu.be/abc", DownloadOptions(height=720), FakeTools())
        selector = args[args.index("-f") + 1]
        self.assertTrue(all("[height<=720]" in branch for branch in selector.split("/")))
        self.assertEqual(args[-2:], ["--", "https://youtu.be/abc"])
        self.assertIn("--ignore-config", args)
        self.assertIn("--no-playlist", args)
        self.assertEqual(args[args.index("--js-runtimes") + 1], "deno:C:/Program Files/Kecheenok/tools/deno.exe")

    def test_audio_playlist_and_subtitles_flags(self):
        args = download_command("https://youtu.be/abc", DownloadOptions(format="mp3", playlist=True, subtitles=True), FakeTools())
        self.assertIn("--yes-playlist", args)
        self.assertIn("--write-subs", args)
        self.assertIn("-x", args)
        self.assertEqual(args[args.index("--audio-format") + 1], "mp3")

    def test_settings_round_trip_and_interrupted_history(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            job = Job("https://youtu.be/abc", DownloadOptions(), state="running")
            store.save("history.json", [dataclasses.asdict(job)])
            recovered = Job.from_dict(store.load("history.json", [])[0])
            self.assertEqual(recovered.state, "cancelled")
            self.assertIsInstance(recovered.options.edit, EditOptions)
            (Path(root) / "history.json").write_text("{broken")
            self.assertEqual(store.load("history.json", []), [])
            self.assertTrue(store.last_error)

    def test_download_requires_existing_final_output(self):
        class Runner:
            def run(self, args, callback):
                callback('__K_FILE__"/missing/file.mp4"')
                return 0
        with tempfile.TemporaryDirectory() as root:
            service = DownloadService(FakeTools(), Runner(), lambda change: None)
            with self.assertRaises(RuntimeError):
                service.download(Job("https://youtu.be/abc", DownloadOptions(folder=root)))

    def test_partial_success_does_not_hide_playlist_failure(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "first.mp4"
            output.write_bytes(b"test")
            changes = []
            class Runner:
                def run(self, args, callback):
                    callback("__K_FILE__" + json.dumps(str(output)))
                    callback("ERROR: Second video unavailable")
                    return 1
            service = DownloadService(FakeTools(), Runner(), changes.append)
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                service.download(Job("https://youtu.be/abc", DownloadOptions(folder=root)))
            self.assertIn({"files": [str(output)]}, changes)

    def test_subtitle_rate_limit_does_not_discard_successful_video(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "video.mp4"
            output.write_bytes(b"fixture")
            changes, commands = [], []
            class Runner:
                def run(self, args, callback):
                    commands.append(args)
                    if "--skip-download" in args:
                        callback("ERROR: Unable to download video subtitles: HTTP Error 429: Too Many Requests")
                        return 1
                    callback("__K_FILE__" + json.dumps(str(output)))
                    return 0
            service = DownloadService(FakeTools(), Runner(), changes.append)
            files = service.download(Job("https://youtu.be/abc", DownloadOptions(folder=root, subtitles=True)))
            self.assertEqual(files, [str(output)])
            self.assertNotIn("--write-subs", commands[0])
            self.assertIn("--write-subs", commands[1])
            self.assertNotIn("--ignore-errors", commands[0])
            self.assertTrue(any("429" in change.get("warning", "") for change in changes))

    def test_video_failure_does_not_start_subtitle_pass(self):
        commands = []
        class Runner:
            def run(self, args, callback):
                commands.append(args)
                callback("ERROR: unable to download video data: HTTP Error 403: Forbidden")
                return 1
        with tempfile.TemporaryDirectory() as root:
            service = DownloadService(FakeTools(), Runner(), lambda change: None)
            with self.assertRaisesRegex(RuntimeError, "403"):
                service.download(Job("https://youtu.be/abc", DownloadOptions(folder=root, subtitles=True)))
        self.assertEqual(len(commands), 1)

    def test_cancel_before_start_does_not_spawn(self):
        runner = ProcessRunner()
        runner.cancel()
        with self.assertRaises(Cancelled):
            runner.run([sys.executable, "-c", "raise RuntimeError('must not run')"], lambda line: None)

    def test_cancel_terminates_real_process_tree_and_returns(self):
        runner = ProcessRunner()
        ready = threading.Event()
        result = []
        def target():
            try:
                runner.run([sys.executable, "-u", "-c",
                    "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('ready',flush=True); time.sleep(60)"], lambda line: ready.set())
            except Cancelled:
                result.append("cancelled")
        thread = threading.Thread(target=target)
        thread.start()
        self.assertTrue(ready.wait(10))
        runner.cancel()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["cancelled"])

    def test_untrusted_archive_paths_are_not_extracted(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "tools.zip"
            destination = Path(root) / "output"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../../../deno.exe", b"MZsafe-path")
                handle.writestr("../../unrequested.txt", b"no")
            extract_named(archive, destination, {"deno.exe"})
            self.assertEqual((destination / "deno.exe").read_bytes(), b"MZsafe-path")
            self.assertEqual(list(destination.iterdir()), [destination / "deno.exe"])

    def test_wrong_download_checksum_is_rejected(self):
        import io
        with tempfile.TemporaryDirectory() as root, patch("kecheenok.tooling.request") as mocked:
            response = io.BytesIO(b"tampered")
            response.headers = {}
            mocked.return_value = response
            output = Path(root) / "download.exe"
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                verified_download("https://example.com", output, "0" * 64, lambda message: None)
            self.assertFalse(output.exists())

    def test_release_checksum_fallback_uses_official_asset(self):
        import io
        release = {"tag_name": "test", "assets": [
            {"name": "yt-dlp.exe", "browser_download_url": "https://github.com/yt-dlp/yt-dlp/releases/download/test/yt-dlp.exe"},
            {"name": "SHA2-256SUMS", "browser_download_url": "https://github.com/yt-dlp/yt-dlp/releases/download/test/SHA2-256SUMS"}]}
        with patch("kecheenok.tooling.request", side_effect=[io.BytesIO(json.dumps(release).encode()),
                   io.BytesIO(("a" * 64 + "  yt-dlp.exe\n").encode())]):
            url, checksum, version = release_asset("yt-dlp/yt-dlp", "latest", "yt-dlp.exe")
            self.assertEqual(checksum, "a" * 64)
            self.assertEqual(version, "test")


@unittest.skipUnless(Tools().find("ffmpeg") and Tools().find("ffprobe"), "FFmpeg not installed")
class MediaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kecheenok-test-")
        self.root = Path(self.temp.name)
        self.tools = Tools()
        self.source = self.root / "Český zvuk se spaces.wav"
        subprocess.run([self.tools.find("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=48000:duration=4", str(self.source)],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True)

    def tearDown(self):
        self.temp.cleanup()

    def duration(self, path):
        result = subprocess.run([self.tools.find("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                                 "-of", "json", str(path)], stdin=subprocess.DEVNULL, capture_output=True, check=True)
        return float(json.loads(result.stdout)["format"]["duration"])

    def test_real_audio_trim_tempo_and_pitch_preserve_source(self):
        source_bytes = self.source.read_bytes()
        service = DownloadService(self.tools, ProcessRunner(), lambda change: None)
        output = service.edit(self.source, self.root, EditOptions(start=1, end=3, speed=2, pitch=5, normalize=True))
        self.assertAlmostEqual(self.duration(output), 1.0, delta=.15)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertFalse(list(self.root.glob(".kecheenok-*")))

    def test_real_video_and_audio_keep_same_duration_after_speed_change(self):
        source = self.root / "video.mp4"
        subprocess.run([self.tools.find("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=25:d=4",
                        "-i", str(self.source), "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True)
        service = DownloadService(self.tools, ProcessRunner(), lambda change: None)
        output = service.edit(source, self.root, EditOptions(start=1, end=3, speed=2))
        self.assertAlmostEqual(self.duration(output), 1.0, delta=.15)
        result = subprocess.run([self.tools.find("ffprobe"), "-v", "error", "-show_streams", "-of", "json", output],
                                capture_output=True, check=True)
        streams = json.loads(result.stdout)["streams"]
        durations = [float(s["duration"]) for s in streams]
        self.assertLess(max(durations) - min(durations), .15)

    def test_repeated_exports_do_not_overwrite(self):
        service = DownloadService(self.tools, ProcessRunner(), lambda change: None)
        first = service.edit(self.source, self.root, EditOptions())
        second = service.edit(self.source, self.root, EditOptions())
        self.assertNotEqual(first, second)
        self.assertTrue(Path(first).is_file())
        self.assertTrue(Path(second).is_file())

    def test_download_with_real_ytdlp_from_local_http(self):
        try:
            self.tools.downloader()
        except RuntimeError:
            self.skipTest("yt-dlp not installed")
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(self.root)))
        except PermissionError:
            self.skipTest("Sandbox denies network sockets, including localhost")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/" + __import__("urllib.parse", fromlist=["quote"]).quote(self.source.name)
            changes = []
            job = Job(url, DownloadOptions(folder=str(self.root / "download"), format="mp3"))
            service = DownloadService(self.tools, ProcessRunner(), changes.append)
            output = service.download(job)
            self.assertEqual(len(output), 1)
            self.assertEqual(Path(output[0]).suffix, ".mp3")
            self.assertAlmostEqual(self.duration(output[0]), 4, delta=.2)
            self.assertTrue(any("title" in change for change in changes))
            self.assertTrue(any("progress" in change for change in changes))
        finally:
            server.shutdown()
            server.server_close()

    def test_real_ytdlp_output_protocol_without_network(self):
        try:
            self.tools.downloader()
        except RuntimeError:
            self.skipTest("yt-dlp not installed")
        class LocalFileRunner(ProcessRunner):
            def run(self, args, callback):
                # Only the fixture enables file://; the application never enables it.
                index = args.index("--")
                return super().run(args[:index] + ["--enable-file-urls"] + args[index:], callback)
        changes = []
        options = DownloadOptions(folder=str(self.root / "offline-download"), format="mp3")
        service = DownloadService(self.tools, LocalFileRunner(), changes.append)
        output = service.download(Job(self.source.as_uri(), options))
        self.assertEqual(len(output), 1)
        self.assertEqual(Path(output[0]).suffix, ".mp3")
        self.assertAlmostEqual(self.duration(output[0]), 4, delta=.2)
        self.assertTrue(any("title" in change for change in changes))
        self.assertTrue(any("progress" in change for change in changes))
