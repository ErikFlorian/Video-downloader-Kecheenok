"""Build on native 64-bit Windows, then test the actual windowed executable."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distribution, version
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kecheenok import __version__
from kecheenok.tooling import provision_windows, update_downloader


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-tools", action="store_true", help="Reuse an already verified vendor directory")
    args = parser.parse_args()
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise SystemExit("Windows EXE must be built using 64-bit Windows Python. A Linux binary renamed .exe will not work.")
    vendor = ROOT / "vendor"
    if not args.reuse_tools:
        provision_windows(vendor, print)
        update_downloader(vendor, print)
    for name in ("yt-dlp.exe", "deno.exe", "ffmpeg.exe", "ffprobe.exe"):
        if not (vendor / name).is_file() or (vendor / name).open("rb").read(2) != b"MZ":
            raise SystemExit(f"Missing Windows tool: {name}")
    from kecheenok.qt import QApplication
    from kecheenok.theme import icon
    app = QApplication([])
    app_icon = ROOT / "build" / "kecheenok.ico"
    app_icon.parent.mkdir(exist_ok=True)
    if not icon("download", "#98e4c5", 256).pixmap(256, 256).save(str(app_icon)):
        raise SystemExit("Could not generate application icon")
    manifest = {"version": __version__, "python": sys.version, "platform": sys.platform,
                "PySide6": version("PySide6"), "PyInstaller": version("pyinstaller"),
                "tools": json.loads((vendor / "manifest.json").read_text(encoding="utf-8")),
                "nightly_downloader": json.loads((vendor / "nightly-manifest.json").read_text(encoding="utf-8")) if (vendor / "nightly-manifest.json").is_file() else None}
    (vendor / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
               "--noupx", "--name", "Kecheenok", "--icon", str(app_icon),
               "--add-data", f"{vendor};tools", "--add-data", f"{ROOT / 'assets'};assets",
               "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.txt'};.",
               "--exclude-module", "PyQt6", "--exclude-module", "tkinter", str(ROOT / "KecheenokV3.py")]
    subprocess.run(command, cwd=ROOT, check=True)
    output = ROOT / "dist" / "Kecheenok"
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    for package in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pyinstaller"):
        dist = distribution(package)
        destination = licenses / package
        destination.mkdir(exist_ok=True)
        for index, file in enumerate(dist.files or []):
            if "licenses" in file.parts or file.name.lower().startswith(("license", "copying", "copyright", "notice")):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    shutil.copyfile(source, destination / f"{index}-{file.name}")
    exe = output / "Kecheenok.exe"
    with exe.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise SystemExit("Output is not a Windows PE executable")
        handle.seek(0x3c)
        offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(offset)
        if handle.read(4) != b"PE\0\0":
            raise SystemExit("Invalid Windows PE header")
    report = ROOT / "dist" / "windows-self-test.json"
    subprocess.run([str(exe), "--self-test", "--report", str(report)], cwd=output, check=True, timeout=180)
    if not json.loads(report.read_text(encoding="utf-8")).get("ok"):
        raise SystemExit("Packaged application failed its startup test")
    shutil.copyfile(ROOT / "WINDOWS.txt", output / "READ_ME.txt")
    shutil.copyfile(ROOT / "THIRD_PARTY_NOTICES.txt", output / "THIRD_PARTY_NOTICES.txt")
    shutil.copyfile(report, output / "startup-test.json")
    archive = shutil.make_archive(str(ROOT / "dist" / f"Kecheenok-v{__version__}-windows-x64"), "zip", ROOT / "dist", "Kecheenok")
    with Path(archive).open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    Path(archive + ".sha256").write_text(f"{digest}  {Path(archive).name}\n", encoding="ascii")
    print(f"Windows package tested: {archive}")


if __name__ == "__main__":
    main()
