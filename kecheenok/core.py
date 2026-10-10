from __future__ import annotations

import dataclasses
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlsplit
import uuid

APP_NAME = "Kecheenok"
FORMATS = {"mp4": "Video · MP4", "mkv": "Video · MKV", "mp3": "Audio · MP3",
           "m4a": "Audio · M4A", "flac": "Audio · FLAC", "wav": "Audio · WAV"}
AUDIO_FORMATS = {"mp3", "m4a", "flac", "wav"}
ACTIVE_STATES = {"waiting", "running", "processing"}
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


def bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def default_download_dir() -> Path:
    # The UI supplies Qt's platform-aware Downloads location when available.
    return Path.home() / "Downloads" / APP_NAME


def parse_time(value: str) -> float | None:
    if not value.strip():
        return None
    parts = value.strip().split(":")
    if len(parts) > 3 or any(not re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
        raise ValueError("Enter time in seconds or as MM:SS / HH:MM:SS.")
    numbers = [float(p) for p in parts]
    if any(not math.isfinite(n) for n in numbers) or any(n >= 60 for n in numbers[1:]):
        raise ValueError("Minutes and seconds after a colon must be less than 60.")
    return sum(n * 60 ** i for i, n in enumerate(reversed(numbers)))


def extract_urls(text: str) -> list[str]:
    urls = list(dict.fromkeys(URL_RE.findall(text.strip())))
    if not urls:
        raise ValueError("Paste a video link. You can add multiple links, one per line.")
    for url in urls:
        parsed = urlsplit(url)
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("The link must be a valid HTTP or HTTPS address without login credentials.")
    return urls


@dataclasses.dataclass
class EditOptions:
    start: float | None = None
    end: float | None = None
    speed: float = 1.0
    pitch: int = 0
    bass: int = 0
    reverb: int = 0
    echo: int = 0
    stereo: float = 1.0
    normalize: bool = False

    @property
    def enabled(self) -> bool:
        return self != EditOptions()

    def validate(self) -> None:
        for value in (self.start, self.end):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("Trim times must be zero or greater.")
        if self.end is not None and self.end <= (self.start or 0):
            raise ValueError("The trim end must be after the start.")
        if not 0.5 <= self.speed <= 2 or not -12 <= self.pitch <= 12:
            raise ValueError("Speed must be 0.5–2× and pitch must be −12 to +12 semitones.")
        if not 0 <= self.bass <= 20 or not 0 <= self.reverb <= 70 or not 0 <= self.echo <= 800:
            raise ValueError("Invalid audio effect intensity.")
        if not 1 <= self.stereo <= 3:
            raise ValueError("Stereo width must be 1–3×.")

    def audio_filter(self) -> str:
        filters = []
        if self.pitch:
            ratio = 2 ** (self.pitch / 12)
            # Normalize the input sample rate first: asetrate alone shifts 48 kHz sources incorrectly.
            filters.extend(["aresample=48000", f"asetrate={48000 * ratio:.6f}",
                            "aresample=48000", f"atempo={1 / ratio:.8f}"])
        if self.speed != 1:
            filters.append(f"atempo={self.speed:.6f}")
        if self.bass:
            filters.extend([f"bass=g={self.bass}:f=100:w=0.6", "alimiter=limit=0.95:level=false"])
        if self.reverb:
            filters.append(f"aecho=0.8:0.88:60:{self.reverb / 100:.2f}")
        if self.echo:
            filters.append(f"aecho=0.8:0.6:{self.echo}:0.4")
        if self.stereo != 1:
            filters.append(f"stereotools=slev={self.stereo:.2f}")
        if self.normalize:
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        return ",".join(filters)


@dataclasses.dataclass
class DownloadOptions:
    folder: str = dataclasses.field(default_factory=lambda: str(default_download_dir()))
    format: str = "mp4"
    height: int = 1080
    playlist: bool = False
    playlist_limit: int = 100
    subtitles: bool = False
    cookies_file: str = ""
    edit: EditOptions = dataclasses.field(default_factory=EditOptions)

    def validate(self) -> None:
        if self.format not in FORMATS or self.height not in {0, 360, 480, 720, 1080, 1440, 2160}:
            raise ValueError("Invalid format or resolution.")
        if not self.folder.strip():
            raise ValueError("Choose a destination folder.")
        if not 1 <= self.playlist_limit <= 500:
            raise ValueError("The playlist limit must be between 1 and 500 videos.")
        if self.cookies_file and not Path(self.cookies_file).is_file():
            raise ValueError("The cookies file no longer exists. Select it again in Settings.")
        self.edit.validate()


@dataclasses.dataclass
class Job:
    url: str
    options: DownloadOptions
    id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)
    title: str = "Loading information…"
    state: str = "waiting"
    progress: float = 0
    detail: str = "Waiting in queue"
    files: list[str] = dataclasses.field(default_factory=list)
    error: str = ""
    warnings: list[str] = dataclasses.field(default_factory=list)
    created: float = dataclasses.field(default_factory=time.time)
    local: bool = False

    @classmethod
    def from_dict(cls, value: dict) -> Job:
        value = dict(value)
        opts = dict(value.pop("options"))
        opts["edit"] = EditOptions(**opts.get("edit", {}))
        job = cls(options=DownloadOptions(**opts), **value)
        if job.state in ACTIVE_STATES:
            job.state = "cancelled"
            job.detail = "Interrupted when the app closed · can be retried"
        return job


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        self.last_error = ""

    def load(self, name: str, default):
        try:
            return json.loads((self.root / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as exc:
            self.last_error = f"Could not load {name}: {exc}"
            return default

    def save(self, name: str, value) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        temp = target.with_suffix(".tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
        except OSError:
            temp.unlink(missing_ok=True)
            raise


class Tools:
    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()

    def find(self, name: str) -> str | None:
        filename = name + (".exe" if sys.platform == "win32" else "")
        for folder in (self.root / "tools", bundle_dir() / "tools", bundle_dir() / "vendor"):
            candidate = folder / filename
            if candidate.is_file():
                return str(candidate)
        return shutil.which(filename)

    def downloader(self) -> list[str]:
        binary = self.find("yt-dlp")
        if binary:
            return [binary]
        if not getattr(sys, "frozen", False) and importlib.util.find_spec("yt_dlp"):
            return [sys.executable, "-m", "yt_dlp"]
        raise RuntimeError("yt-dlp is missing. Use the complete app package or repair the tools in Settings.")

    def check(self) -> dict[str, bool]:
        try:
            self.downloader()
            downloader = True
        except RuntimeError:
            downloader = False
        return {"yt-dlp": downloader, "FFmpeg": bool(self.find("ffmpeg") and self.find("ffprobe")),
                "JavaScript": bool(self.find("deno") or self.find("node"))}


def download_command(url: str, options: DownloadOptions, tools: Tools) -> list[str]:
    options.validate()
    ffmpeg = tools.find("ffmpeg")
    if not ffmpeg or not tools.find("ffprobe"):
        raise RuntimeError("FFmpeg / FFprobe is missing. In Settings, choose ‘Repair / update tools’.")
    args = tools.downloader() + ["--ignore-config", "--encoding", "utf-8", "--no-color",
        "--newline", "--progress", "--progress-delta", "0.25", "--no-simulate", "--no-overwrites",
        "--socket-timeout", "25", "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "5",
        "--retry-sleep", "http:exp=1:16", "--retry-sleep", "fragment:exp=1:16", "--verbose",
        "--windows-filenames", "--trim-filenames", "180", "--ffmpeg-location", str(Path(ffmpeg).parent),
        "--paths", options.folder, "-o", "%(title).150B [%(id)s].%(ext)s",
        "--print", "before_dl:__K_META__%(.{title,uploader,duration})j",
        "--print", "after_move:__K_FILE__%(filepath)j",
        "--progress-template", "download:__K_PROGRESS__%(progress.{downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta,status})j",
        "--progress-template", "postprocess:__K_PROCESS__%(progress.status)s"]
    deno, node = tools.find("deno"), tools.find("node")
    if deno:
        args += ["--js-runtimes", "deno:" + deno]
    elif node:
        args += ["--js-runtimes", "node:" + node]
    elif "youtube" in (urlsplit(url).hostname or "") or "youtu.be" in url:
        raise RuntimeError("A JavaScript runtime is missing for YouTube. Repair the tools in Settings.")
    args += ["--yes-playlist", "--playlist-end", str(options.playlist_limit)] if options.playlist else ["--no-playlist"]
    if options.cookies_file:
        args += ["--cookies", options.cookies_file]
    if options.subtitles:
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", "cs.*,en.*", "--sub-format", "srt/best",
                 "--convert-subs", "srt"]
    if options.format in AUDIO_FORMATS:
        args += ["-f", "ba/b", "-x", "--audio-format", options.format, "--audio-quality", "0"]
    else:
        cap = f"[height<={options.height}]" if options.height else ""
        fmt = (f"bv*[ext=mp4]{cap}+ba[ext=m4a]/b[ext=mp4]{cap}/bv*{cap}+ba/b{cap}"
               if options.format == "mp4" else f"bv*{cap}+ba/b{cap}")
        args += ["-f", fmt, "--merge-output-format", options.format, "--remux-video", options.format]
    return args + ["--", url]


class Cancelled(Exception):
    pass


class WindowsJob:
    """A kernel job owns descendants, including FFmpeg and Deno subprocesses."""
    def __init__(self, process):
        import ctypes as c
        from ctypes import wintypes as w
        class IO(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Basic(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_longlong), ("PerJobUserTimeLimit", c.c_longlong),
                ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t), ("MaximumWorkingSetSize", c.c_size_t),
                ("ActiveProcessLimit", w.DWORD), ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", c.c_size_t),
                ("JobMemoryLimit", c.c_size_t), ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.kernel.SetInformationJobObject.restype = w.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = w.BOOL
        self.kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.kernel.TerminateJobObject.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)) or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = c.get_last_error()
            self.close()
            raise c.WinError(error)

    def terminate(self):
        return bool(self.kernel.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ProcessRunner:
    """Own a subprocess tree so cancellation includes FFmpeg and JavaScript children."""
    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.windows_job = None

    def cancel(self) -> None:
        self.cancelled.set()
        with self.lock:
            process = self.process
            if process is None:
                return
            if sys.platform == "win32":
                if self.windows_job and self.windows_job.terminate():
                    return
                # Fallback for hosts that disallow job assignment, such as older Wine.
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=15, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def run(self, args: list[str], on_line: Callable[[str], None]) -> int:
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
        with self.lock:
            if self.cancelled.is_set():
                raise Cancelled()
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **kwargs)
            self.process = process
            if sys.platform == "win32":
                try:
                    self.windows_job = WindowsJob(process)
                except OSError:
                    self.windows_job = None
        try:
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    on_line(ANSI_RE.sub("", line.rstrip()))
            code = process.wait()
            if self.cancelled.is_set():
                raise Cancelled()
            return code
        finally:
            if process.poll() is None:
                self.cancel()
                process.wait(timeout=20)
            with self.lock:
                self.process = None
                if self.windows_job:
                    self.windows_job.close()
                    self.windows_job = None


def error_message(lines: list[str]) -> str:
    errors = [line.removeprefix("ERROR: ") for line in lines if "ERROR:" in line]
    message = "\n".join(errors[-3:]) or "\n".join(lines[-3:]) or "The download ended without saving a file."
    lower = message.lower()
    if any(s in lower for s in ("sign in", "confirm you", "not a bot", "age-restricted")):
        message += "\nYou can add a cookies file in Settings to access content through your account."
    elif "403" in lower or "signature" in lower:
        message += "\nUpdate yt-dlp to the latest nightly build in Settings, then retry the task."
    elif "429" in lower:
        message += "\nThe server temporarily limited requests. Wait a while, then retry the task."
    elif any(s in lower for s in ("unable to download", "name resolution", "network is unreachable")):
        message += "\nCheck your internet connection and try the download again."
    return message[-2200:]


class DownloadService:
    def __init__(self, tools: Tools, runner: ProcessRunner, emit: Callable[[dict], None]):
        self.tools, self.runner, self.emit = tools, runner, emit

    def download(self, job: Job) -> list[str]:
        files, lines = [], []
        Path(job.options.folder).mkdir(parents=True, exist_ok=True)
        self.emit({"state": "running", "detail": "Getting video information…"})

        def on_line(line: str):
            if line.startswith("__K_META__"):
                try:
                    info = json.loads(line[len("__K_META__"):])
                    self.emit({"title": info.get("title") or job.url, "detail": info.get("uploader") or "Downloading…"})
                except (ValueError, AttributeError):
                    pass
            elif line.startswith("__K_PROGRESS__"):
                try:
                    progress = json.loads(line[len("__K_PROGRESS__"):])
                    total = progress.get("total_bytes") or progress.get("total_bytes_estimate") or 0
                    percent = min(99.0, 100 * (progress.get("downloaded_bytes") or 0) / total) if total else 0
                    speed, eta = progress.get("speed") or 0, progress.get("eta")
                    detail = f"{percent:.0f} % · {speed / 1048576:.1f} MB/s"
                    if isinstance(eta, (float, int)):
                        detail += f" · {int(eta) // 60}:{int(eta) % 60:02d} remaining"
                    self.emit({"state": "running", "progress": percent, "detail": detail})
                except (ValueError, TypeError, AttributeError):
                    pass
            elif line.startswith("__K_PROCESS__"):
                self.emit({"state": "processing", "detail": "Finishing file…"})
            elif line.startswith("__K_FILE__"):
                try:
                    filename = json.loads(line[len("__K_FILE__"):])
                    if isinstance(filename, str) and Path(filename).is_file() and filename not in files:
                        files.append(filename)
                        self.emit({"files": list(files)})
                except ValueError:
                    pass
            elif line:
                lines.append(line)
                del lines[:-30]
                self.emit({"log": line})

        # Subtitles are optional; fetch them after the media so a caption 429 cannot abort it.
        media_options = dataclasses.replace(job.options, subtitles=False)
        code = self.runner.run(download_command(job.url, media_options, self.tools), on_line)
        if code or not files:
            raise RuntimeError(error_message(lines))
        if job.options.subtitles:
            self.download_subtitles(job)
        if job.options.edit.enabled:
            edited = []
            for filename in files:
                result = self.edit(Path(filename), Path(job.options.folder), job.options.edit)
                edited.append(result)
                self.emit({"files": list(files) + edited})
            return edited
        return files

    def download_subtitles(self, job: Job) -> None:
        self.emit({"state": "processing", "detail": "Video saved. Downloading optional subtitles…"})
        args = download_command(job.url, dataclasses.replace(job.options, subtitles=True), self.tools)
        args[-2:-2] = ["--skip-download", "--sleep-subtitles", "2"]
        lines = []
        def on_line(line):
            if not line.startswith("__K_"):
                lines.append(line)
                del lines[:-30]
                self.emit({"log": line})
        try:
            code = self.runner.run(args, on_line)
        except Cancelled:
            raise
        except OSError as exc:
            code = 1
            lines.append(str(exc))
        if code:
            self.emit({"warning": "Video saved, but some subtitles could not be downloaded.\n" + error_message(lines)})

    def edit(self, source: Path, folder: Path, options: EditOptions) -> str:
        options.validate()
        if not source.is_file():
            raise ValueError("The input file no longer exists.")
        ffmpeg, ffprobe = self.tools.find("ffmpeg"), self.tools.find("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("FFmpeg / FFprobe is missing. Repair the tools in Settings.")
        probe_lines = []
        code = self.runner.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)], probe_lines.append)
        if code:
            raise RuntimeError("Could not read the file: " + "\n".join(probe_lines[-5:]))
        info = json.loads("\n".join(probe_lines))
        video = any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in info["streams"])
        audio = any(s.get("codec_type") == "audio" for s in info["streams"])
        if not video and not audio:
            raise ValueError("The file contains neither video nor audio.")
        duration = float(info.get("format", {}).get("duration") or 0)
        start = options.start or 0
        if duration and start >= duration:
            raise ValueError("The trim start is past the end of the file.")
        length = max(0, min(options.end or duration, duration) - start) / options.speed if duration else 0
        folder.mkdir(parents=True, exist_ok=True)
        ext = source.suffix.lower() if source.suffix.lower()[1:] in AUDIO_FORMATS and not video else ".mp4" if video else ".m4a"
        output = folder / f"{source.stem[:140]} [edit]{ext}"
        index = 2
        while output.exists():
            output = folder / f"{source.stem[:140]} [edit {index}]{ext}"
            index += 1
        # Work on a unique temporary file; never leave a failed export as a completed file.
        temp = output.with_name(f".kecheenok-{uuid.uuid4().hex}{ext}")
        args = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
        if start:
            args += ["-ss", str(start)]
        if options.end is not None:
            args += ["-t", str(options.end - start)]
        args += ["-i", str(source)]
        if video:
            args += ["-map", "0:v:0", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
            if options.speed != 1:
                args += ["-vf", f"setpts=(PTS-STARTPTS)/{options.speed}"]
        else:
            args += ["-vn"]
        if audio:
            args += ["-map", "0:a:0"]
            if filters := options.audio_filter():
                args += ["-af", filters]
            codec = {".mp3": "libmp3lame", ".flac": "flac", ".wav": "pcm_s16le"}.get(ext, "aac")
            args += ["-c:a", codec]
            if codec in {"libmp3lame", "aac"}:
                args += ["-b:a", "320k" if codec == "libmp3lame" else "192k"]
        if ext in {".mp4", ".m4a"}:
            args += ["-movflags", "+faststart"]
        args += ["-progress", "pipe:1", "-nostats", str(temp)]
        errors = []
        self.emit({"state": "processing", "detail": "Exporting edited copy…", "progress": 0})

        def on_line(line):
            if line.startswith("out_time_us=") and length:
                try:
                    self.emit({"progress": min(99, float(line.split("=", 1)[1]) / 1000000 / length * 100)})
                except ValueError:
                    pass
            else:
                errors.append(line)
                del errors[:-15]
                self.emit({"log": line})

        try:
            code = self.runner.run(args, on_line)
            if code or not temp.is_file() or not temp.stat().st_size:
                raise RuntimeError("Export failed: " + "\n".join(errors[-8:]))
            temp.replace(output)
            return str(output)
        finally:
            temp.unlink(missing_ok=True)
