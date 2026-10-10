"""Fetch verified Windows runtimes from their official release publishers."""
from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

UA = "Kecheenok/3.0 (desktop runtime installer)"


def request(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60)


def release_asset(repo: str, tag: str, filename: str) -> tuple[str, str, str]:
    with request(f"https://api.github.com/repos/{repo}/releases/{tag}") as response:
        release = json.load(response)
    asset = next((a for a in release["assets"] if a["name"] == filename), None)
    if not asset:
        raise RuntimeError(f"{filename} is missing from the official {repo} release.")
    digest = asset.get("digest") or ""
    expected = digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        checksum = next((a for a in release["assets"] if a["name"] in {
            "SHA2-256SUMS", "checksums.sha256", filename + ".sha256sum", filename + ".sha256"}), None)
        if checksum:
            with request(checksum["browser_download_url"]) as response:
                checksums = response.read().decode("utf-8")
            for line in checksums.splitlines():
                fields = line.split()
                if fields and re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]) and (
                    len(fields) == 1 and checksum["name"].startswith(filename) or
                    len(fields) > 1 and fields[-1].lstrip("*") == filename):
                    expected = fields[0]
                    break
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise RuntimeError(f"The publisher does not provide a SHA-256 checksum for {filename}; installation was stopped.")
    return asset["browser_download_url"], expected, release["tag_name"]


def verified_download(url: str, destination: Path, expected: str, emit) -> None:
    digest = hashlib.sha256()
    with request(url) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        last_percent = -1
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            percent = int(done * 100 / total) if total else 0
            if percent != last_percent:
                emit(f"Downloading {destination.name} · {percent}%" if total else f"Downloading {destination.name}…")
                last_percent = percent
    if digest.hexdigest().lower() != expected.lower():
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"The checksum for {destination.name} does not match. The file was not used.")


def extract_named(archive: Path, destination: Path, names: set[str]) -> None:
    # Extract just the executables by basename; never trust archive directory paths.
    with zipfile.ZipFile(archive) as zipped:
        for name in names:
            candidates = [i for i in zipped.infolist() if Path(i.filename).name == name and not i.is_dir()]
            if len(candidates) != 1:
                raise RuntimeError(f"Archive {archive.name} does not contain exactly one {name}.")
            with zipped.open(candidates[0]) as source, (destination / name).open("wb") as output:
                shutil.copyfileobj(source, output)


def extract_notices(archive: Path, destination: Path, prefix: str) -> None:
    with zipfile.ZipFile(archive) as zipped:
        for index, item in enumerate(zipped.infolist()):
            name = Path(item.filename).name
            if not item.is_dir() and item.file_size < 512_000 and name.lower().startswith(("license", "copying", "copyright", "notice")):
                with zipped.open(item) as source, (destination / f"{prefix}-{index}-{name}").open("wb") as output:
                    shutil.copyfileobj(source, output)


def provision_windows(destination: Path, emit=lambda message: None) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("Automatic tool installation is available on Windows. On Linux, install yt-dlp, FFmpeg, and Deno.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kecheenok-tools-", dir=destination.parent) as work:
        stage = Path(work)
        payload = stage / "payload"
        payload.mkdir()
        manifest = {}
        specs = [
            ("yt-dlp/yt-dlp", "latest", "yt-dlp.exe", {"yt-dlp.exe"}),
            ("denoland/deno", "latest", "deno-x86_64-pc-windows-msvc.zip", {"deno.exe"}),
            ("yt-dlp/FFmpeg-Builds", "tags/latest", "ffmpeg-master-latest-win64-gpl.zip", {"ffmpeg.exe", "ffprobe.exe"}),
        ]
        for repo, tag, filename, names in specs:
            emit(f"Checking the {repo} release…")
            url, digest, version = release_asset(repo, tag, filename)
            archive = stage / filename
            verified_download(url, archive, digest, emit)
            if filename.endswith(".zip"):
                extract_named(archive, payload, names)
                extract_notices(archive, payload, repo.split("/")[-1])
            else:
                shutil.copyfile(archive, payload / filename)
            for name in names:
                with (payload / name).open("rb") as handle:
                    if handle.read(2) != b"MZ":
                        raise RuntimeError(f"{name} is not a Windows executable.")
            manifest[repo] = {"version": version, "url": url, "sha256": digest}
        for repo, filename in (("yt-dlp/yt-dlp", "LICENSE"), ("yt-dlp/yt-dlp", "THIRD_PARTY_LICENSES.txt"), ("denoland/deno", "LICENSE.md")):
            tag = manifest[repo]["version"]
            with request(f"https://raw.githubusercontent.com/{repo}/{tag}/{filename}") as response:
                (payload / f"{repo.split('/')[-1]}-{filename}.txt").write_bytes(response.read())
        (payload / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        destination.mkdir(parents=True, exist_ok=True)
        for path in payload.iterdir():
            shutil.copyfile(path, destination / (path.name + ".new"))
        for path in payload.iterdir():
            (destination / (path.name + ".new")).replace(destination / path.name)
        emit("Tools are ready.")
        return manifest


def update_downloader(destination: Path, emit=lambda message: None) -> dict:
    """Install the publisher's latest yt-dlp nightly for this OS, atomically."""
    if sys.platform == "win32":
        asset, target = "yt-dlp.exe", "yt-dlp.exe"
    elif sys.platform == "darwin":
        asset, target = "yt-dlp_macos", "yt-dlp"
    elif sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        if machine in {"x86_64", "amd64"}:
            asset = "yt-dlp_linux"
        elif machine in {"aarch64", "arm64"}:
            asset = "yt-dlp_linux_aarch64"
        else:
            raise RuntimeError(f"yt-dlp updates do not support the Linux {machine} architecture.")
        target = "yt-dlp"
    else:
        raise RuntimeError(f"Automatic yt-dlp updates are not supported on {sys.platform}.")

    emit("Checking the latest yt-dlp nightly release…")
    url, digest, version = release_asset("yt-dlp/yt-dlp-nightly-builds", "latest", asset)
    destination.mkdir(parents=True, exist_ok=True)
    # Stage beside the target so replace() stays atomic and never touches system paths.
    with tempfile.TemporaryDirectory(prefix="kecheenok-ytdlp-", dir=destination) as work:
        staged = Path(work) / target
        verified_download(url, staged, digest, emit)
        if staged.stat().st_size < 1024:
            raise RuntimeError("The downloaded yt-dlp file is incomplete; the installed version was kept.")
        with staged.open("rb") as executable:
            header = executable.read(64)
        valid = False
        if sys.platform == "win32":
            valid = header.startswith(b"MZ")
        elif sys.platform.startswith("linux"):
            machine_id = int.from_bytes(header[18:20], "little") if header.startswith(b"\x7fELF") else 0
            expected_machine = 183 if asset.endswith("aarch64") else 62
            valid = header.startswith(b"\x7fELF") and machine_id == expected_machine
        else:
            valid = header[:4] in {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                                   b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
        if not valid:
            raise RuntimeError(f"The downloaded file {asset} is not an executable for this system.")
        if sys.platform != "win32":
            staged.chmod(0o755)
        final = destination / target
        result = {"version": version, "url": url, "sha256": digest, "path": str(final)}
        staged_manifest = Path(work) / "nightly-manifest.json"
        staged_manifest.write_text(json.dumps(result, indent=2), encoding="utf-8")
        staged.replace(final)
        staged_manifest.replace(destination / "nightly-manifest.json")
    emit(f"yt-dlp {version} is ready.")
    return result


def provision_tools(destination: Path, emit=lambda message: None) -> dict:
    """Repair Windows runtimes when needed, then update yt-dlp in user storage."""
    destination = Path(destination)
    if sys.platform == "win32":
        # Keep the full runtime installer for first-time/repair installs. Normal
        # updates only replace yt-dlp and leave the large runtimes untouched.
        from .core import Tools
        tools = Tools(root=destination.parent)
        needed = ("deno", "ffmpeg", "ffprobe")
        if not all(tools.find(name) for name in needed):
            provision_windows(destination, emit)
        return update_downloader(destination, emit)

    missing = []
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        missing.append("FFmpeg and FFprobe")
    if not (shutil.which("deno") or shutil.which("node")):
        missing.append("Deno or Node.js")
    if missing:
        raise RuntimeError("Missing " + " and ".join(missing) + ". Install them with your system package manager, then retry the update.")
    return update_downloader(destination, emit)
