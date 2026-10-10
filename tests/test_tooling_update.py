import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kecheenok import tooling


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class DownloaderUpdateTests(unittest.TestCase):
    def test_verified_update_replaces_target_and_sets_executable_mode(self):
        payload = bytearray(2048)
        payload[:20] = b"\x7fELF\x02\x01\x01" + bytes(11) + (62).to_bytes(2, "little")
        payload = bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root)
            old = destination / "yt-dlp"
            old.write_bytes(b"old downloader")
            with patch.object(tooling.sys, "platform", "linux"), \
                 patch.object(tooling.platform, "machine", return_value="x86_64"), \
                 patch.object(tooling, "release_asset", return_value=("https://example.test/yt-dlp", expected, "nightly-test")), \
                 patch.object(tooling, "request", return_value=Response(payload)):
                result = tooling.update_downloader(destination)
            self.assertEqual(Path(result["path"]), old)
            self.assertEqual(old.read_bytes(), payload)
            if os.name != "nt":
                self.assertTrue(os.access(old, os.X_OK))
                self.assertEqual(old.stat().st_mode & 0o777, 0o755)
            self.assertEqual({p.name for p in destination.iterdir()}, {"yt-dlp", "nightly-manifest.json"})
            self.assertEqual(tooling.json.loads((destination / "nightly-manifest.json").read_text()), result)

    def test_checksum_failure_preserves_existing_downloader_and_cleans_stage(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root)
            old = destination / "yt-dlp"
            old.write_bytes(b"known-good")
            with patch.object(tooling.sys, "platform", "linux"), \
                 patch.object(tooling.platform, "machine", return_value="x86_64"), \
                 patch.object(tooling, "release_asset", return_value=("https://example.test/yt-dlp", "0" * 64, "nightly-test")), \
                 patch.object(tooling, "request", return_value=Response(b"bad payload")):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    tooling.update_downloader(destination)
            self.assertEqual(old.read_bytes(), b"known-good")
            self.assertEqual({p.name for p in destination.iterdir()}, {"yt-dlp"})

    def test_windows_uses_exe_asset(self):
        payload = b"MZ" + b"x" * 2048
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            with patch.object(tooling.sys, "platform", "win32"), \
                 patch.object(tooling, "release_asset", return_value=("https://example.test/yt-dlp.exe", expected, "nightly-test")) as release, \
                 patch.object(tooling, "request", return_value=Response(payload)):
                result = tooling.update_downloader(Path(root))
            release.assert_called_once_with("yt-dlp/yt-dlp-nightly-builds", "latest", "yt-dlp.exe")
            self.assertTrue(Path(result["path"]).is_file())

    def test_linux_arm64_selects_matching_asset(self):
        payload = bytearray(2048)
        payload[:20] = b"\x7fELF\x02\x01\x01" + bytes(11) + (183).to_bytes(2, "little")
        payload = bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            with patch.object(tooling.sys, "platform", "linux"), \
                 patch.object(tooling.platform, "machine", return_value="aarch64"), \
                 patch.object(tooling, "release_asset", return_value=("https://example.test/yt-dlp", expected, "nightly-test")) as release, \
                 patch.object(tooling, "request", return_value=Response(payload)):
                tooling.update_downloader(Path(root))
            release.assert_called_once_with("yt-dlp/yt-dlp-nightly-builds", "latest", "yt-dlp_linux_aarch64")

    def test_invalid_native_binary_preserves_existing_target(self):
        payload = b"not an ELF file" + b"x" * 2048
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root)
            old = destination / "yt-dlp"
            old.write_bytes(b"known-good")
            with patch.object(tooling.sys, "platform", "linux"), \
                 patch.object(tooling.platform, "machine", return_value="x86_64"), \
                 patch.object(tooling, "release_asset", return_value=("https://example.test/yt-dlp", expected, "nightly-test")), \
                 patch.object(tooling, "request", return_value=Response(payload)):
                with self.assertRaisesRegex(RuntimeError, "not an executable for this system"):
                    tooling.update_downloader(destination)
            self.assertEqual(old.read_bytes(), b"known-good")
            self.assertEqual({p.name for p in destination.iterdir()}, {"yt-dlp"})


if __name__ == "__main__":
    unittest.main()
