#!/usr/bin/env python3
"""Kecheenok v3 entry point, including packaged startup diagnostics."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback


def main() -> int:
    parser = argparse.ArgumentParser(description="Kecheenok v3 desktop app")
    parser.add_argument("--self-test", action="store_true", help="Test GUI initialization and bundled tools without network access")
    parser.add_argument("--report", type=Path, help="Write the self-test result to JSON")
    parser.add_argument("--screenshot", type=Path, help="Save a screenshot during the self-test")
    args = parser.parse_args()
    # PyInstaller's Windows GUI bootloader supplies no standard streams.
    for name in ("stdout", "stderr", "stdin"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "r" if name == "stdin" else "w"))
    from kecheenok.core import Store, Tools, data_dir
    root = data_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[RotatingFileHandler(root / "kecheenok.log", maxBytes=2_000_000,
                                                           backupCount=2, encoding="utf-8")])
        from kecheenok.qt import QApplication, QFont, QMessageBox, QLockFile, QLocale
        from kecheenok.ui import MainWindow
        QLocale.setDefault(QLocale("en_US"))
        app = QApplication(sys.argv[:1])
        app.setApplicationName("Kecheenok")
        app.setOrganizationName("Kecheenok")
        app.setFont(QFont("Segoe UI" if sys.platform == "win32" else "DejaVu Sans", 10))
        if args.self_test:
            with tempfile.TemporaryDirectory(prefix="kecheenok-self-test-") as temp:
                window = MainWindow(Store(Path(temp)), auto_start=False)
                window.show()
                app.processEvents()
                report = {"platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False)), "gui": True, "tools": {}}
                tools = Tools()
                commands = {"yt-dlp": tools.downloader() + ["--version"]}
                for name in ("ffmpeg", "ffprobe", "deno"):
                    executable = tools.find(name)
                    if executable:
                        commands[name] = [executable, "--version" if name == "deno" else "-version"]
                    else:
                        report["tools"][name] = {"ok": False, "error": "missing"}
                for name, command in commands.items():
                    try:
                        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
                        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                                text=True, encoding="utf-8", errors="replace", timeout=30, **kwargs)
                        report["tools"][name] = {"ok": result.returncode == 0, "version": (result.stdout or result.stderr).splitlines()[0]}
                    except Exception as exc:
                        report["tools"][name] = {"ok": False, "error": str(exc)}
                report["ok"] = all(tool["ok"] for tool in report["tools"].values())
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    if not window.grab().save(str(args.screenshot)):
                        raise RuntimeError("Could not save screenshot")
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                print(json.dumps(report, indent=2, ensure_ascii=False))
                window.close()
                return 0 if report["ok"] else 1
        lock = QLockFile(str(root / "app.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            QMessageBox.information(None, "Kecheenok is already running", "The app is already open. Use its existing window.")
            return 0
        window = MainWindow()
        window.show()
        return app.exec()
    except Exception:
        details = traceback.format_exc()
        logging.exception("Application startup failed")
        if args.report:
            try:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps({"ok": False, "error": details}, indent=2), encoding="utf-8")
            except OSError:
                pass
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None,
                f"Kecheenok could not start.\n\nDetails: {root / 'kecheenok.log'}\n\n{details[-1500:]}",
                "Kecheenok · Startup Error", 0x10)
        else:
            print(details, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
