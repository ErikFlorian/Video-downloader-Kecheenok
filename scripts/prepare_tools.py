"""Prepare source-mode runtimes without touching the system PATH."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kecheenok.core import Tools, data_dir
from kecheenok.tooling import provision_tools

if __name__ == "__main__":
    destination = data_dir() / "tools"
    filename = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
    if not (destination / filename).is_file() or not all(Tools().check().values()):
        provision_tools(destination, print)
