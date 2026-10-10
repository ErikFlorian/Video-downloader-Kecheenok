from __future__ import annotations

import dataclasses
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
import threading

from . import __version__
from .core import (ACTIVE_STATES, AUDIO_FORMATS, FORMATS, Cancelled, DownloadOptions,
                   DownloadService, EditOptions, Job, ProcessRunner, Store, Tools, bundle_dir, extract_urls, parse_time)
from .qt import *
from .theme import STYLE, icon

STATE_LABELS = {"waiting": "QUEUED", "running": "DOWNLOADING", "processing": "PROCESSING",
                "done": "DONE", "error": "ERROR", "cancelled": "CANCELLED"}
COLORS = {"done": "#98e4c5", "error": "#f29898", "cancelled": "#b1bac8", "waiting": "#929aa8",
          "running": "#a5c6ff", "processing": "#e4c19a"}


def label(text="", name="", wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, name="", glyph="", action=None):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    if glyph:
        widget.setIcon(icon(glyph, "#10241c" if name == "primary" else "#a8b1bf"))
        widget.setIconSize(QSize(17, 17))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if action:
        widget.clicked.connect(action)
    return widget


def panel():
    widget = QFrame()
    widget.setObjectName("panel")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(14)
    return widget, layout


def scroll_content():
    area = QScrollArea()
    area.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.setSpacing(12)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    area.setWidget(content)
    return area, layout


class Worker(QThread):
    changed = Signal(dict)
    outcome = Signal(str, list, str)

    def __init__(self, job: Job, tools: Tools):
        super().__init__()
        self.job, self.tools = job, tools
        self.runner = ProcessRunner()

    def run(self):
        try:
            service = DownloadService(self.tools, self.runner, self.changed.emit)
            if self.job.local:
                files = [service.edit(Path(self.job.url), Path(self.job.options.folder), self.job.options.edit)]
            else:
                files = service.download(self.job)
            self.outcome.emit("done", files, "")
        except Cancelled:
            self.outcome.emit("cancelled", [], "")
        except Exception as exc:
            logging.exception("Job %s failed", self.job.id)
            self.outcome.emit("error", [], str(exc))

    def cancel(self):
        threading.Thread(target=self.runner.cancel, daemon=True).start()


class ToolWorker(QThread):
    message = Signal(str)
    outcome = Signal(bool, str)

    def __init__(self, destination: Path):
        super().__init__()
        self.destination = destination

    def run(self):
        try:
            from .tooling import provision_tools
            provision_tools(self.destination, self.message.emit)
            self.outcome.emit(True, "Tools updated successfully.")
        except Exception as exc:
            logging.exception("Runtime update failed")
            self.outcome.emit(False, f"Update failed: {exc}")


class EditDialog(QDialog):
    def __init__(self, options: EditOptions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trim and audio effects")
        self.setMinimumWidth(490)
        self.options = dataclasses.replace(options)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(18)
        layout.addWidget(label("Fine-tune your media", "sectionTitle"))
        layout.addWidget(label("Edits are saved as a new copy. Your original stays intact.", "muted", True))
        trim = QGridLayout()
        trim.addWidget(label("Start", "muted"), 0, 0)
        trim.addWidget(label("End", "muted"), 0, 1)
        self.start = QLineEdit(str(options.start) if options.start is not None else "")
        self.end = QLineEdit(str(options.end) if options.end is not None else "")
        self.start.setPlaceholderText("0:00")
        self.end.setPlaceholderText("To the end")
        trim.addWidget(self.start, 1, 0)
        trim.addWidget(self.end, 1, 1)
        layout.addLayout(trim)
        effects = QGridLayout()
        effects.setVerticalSpacing(12)
        self.controls = {}
        for row, (key, title, lo, hi, step, suffix) in enumerate([
            ("speed", "Speed", .5, 2, .05, " ×"), ("pitch", "Pitch", -12, 12, 1, " st"),
            ("bass", "Bass boost", 0, 20, 1, " dB"), ("reverb", "Reverb", 0, 70, 5, " %"),
            ("echo", "Echo", 0, 800, 50, " ms"), ("stereo", "Stereo width", 1, 3, .1, " ×")]):
            control = QDoubleSpinBox() if key in {"speed", "stereo"} else QSpinBox()
            control.setRange(lo, hi)
            control.setSingleStep(step)
            control.setSuffix(suffix)
            control.setValue(getattr(options, key))
            effects.addWidget(label(title), row, 0)
            effects.addWidget(control, row, 1)
            self.controls[key] = control
        layout.addLayout(effects)
        self.normalize = QCheckBox("Normalize loudness")
        self.normalize.setChecked(options.normalize)
        layout.addWidget(self.normalize)
        actions = QHBoxLayout()
        actions.addWidget(button("Reset", "quiet", action=self.reset))
        actions.addStretch()
        actions.addWidget(button("Cancel", action=self.reject))
        actions.addWidget(button("Apply changes", "primary", action=self.accept_options))
        layout.addLayout(actions)

    def reset(self):
        self.start.clear()
        self.end.clear()
        for key, control in self.controls.items():
            control.setValue(getattr(EditOptions(), key))
        self.normalize.setChecked(False)

    def accept_options(self):
        try:
            values = {key: control.value() for key, control in self.controls.items()}
            self.options = EditOptions(start=parse_time(self.start.text()), end=parse_time(self.end.text()),
                                       normalize=self.normalize.isChecked(), **values)
            self.options.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Check your edits", str(exc))
            return
        self.accept()


class JobCard(QFrame):
    cancel_requested = Signal(str)
    retry_requested = Signal(str)
    details_requested = Signal(str)

    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self.setObjectName("job")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(16)
        glyph = label()
        audio = job.options.format in AUDIO_FORMATS or job.local
        glyph.setPixmap(icon("audio" if audio else "video", "#98e4c5", 28).pixmap(28, 28))
        glyph.setFixedSize(42, 42)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setStyleSheet("background:#263a32;border-radius:10px;")
        outer.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(7)
        self.title_label = label()
        self.title_label.setStyleSheet("font-size:14px;font-weight:600;")
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.title_label)
        meta = ("Local file" if job.local else job.options.format.upper())
        if job.options.format not in AUDIO_FORMATS and not job.local:
            meta += "  ·  " + (f"{job.options.height}p" if job.options.height else "Best quality")
        if job.options.playlist:
            meta += "  ·  Playlist"
        if job.options.edit.enabled:
            meta += "  ·  Edits"
        body.addWidget(label(meta, "muted"))
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        body.addWidget(self.progress)
        self.detail = label("", "muted", True)
        self.detail.setStyleSheet("font-size:11px;")
        body.addWidget(self.detail)
        outer.addLayout(body, 1)
        side = QVBoxLayout()
        side.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.state = label()
        side.addWidget(self.state, 0, Qt.AlignmentFlag.AlignRight)
        actions = QHBoxLayout()
        self.open_btn = button("", "quiet", "folder", self.open_folder)
        self.open_btn.setToolTip("Open containing folder")
        self.play_btn = button("", "quiet", "play", self.open_file)
        self.play_btn.setToolTip("Open saved file")
        self.cancel_btn = button("", "quiet", "close", lambda: self.cancel_requested.emit(job.id))
        self.cancel_btn.setToolTip("Cancel task")
        self.retry_btn = button("", "quiet", "retry", lambda: self.retry_requested.emit(job.id))
        self.retry_btn.setToolTip("Retry with the same settings")
        self.info_btn = button("Details", "quiet", action=lambda: self.details_requested.emit(job.id))
        for btn in (self.play_btn, self.open_btn, self.retry_btn, self.cancel_btn, self.info_btn):
            actions.addWidget(btn)
        side.addLayout(actions)
        outer.addLayout(side)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.elide_title()

    def elide_title(self):
        text = self.job.url if self.job.title == "Loading information…" else self.job.title
        self.title_label.setText(self.title_label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(60, self.title_label.width())))

    def refresh(self):
        job = self.job
        self.elide_title()
        self.title_label.setToolTip(job.title + "\n" + job.url)
        self.progress.setValue(int(job.progress * 10))
        self.progress.setVisible(job.state in {"running", "processing"})
        self.detail.setText(job.detail)
        self.state.setText(STATE_LABELS.get(job.state, job.state))
        self.state.setStyleSheet(f"color:{COLORS.get(job.state, '#929aa8')};font-size:10px;font-weight:600;")
        self.cancel_btn.setVisible(job.state in ACTIVE_STATES)
        self.retry_btn.setVisible(job.state in {"cancelled", "error"})
        self.info_btn.setVisible(job.state == "error" or bool(job.warnings))
        self.open_btn.setVisible(bool(job.files))
        self.play_btn.setVisible(job.state == "done" and bool(job.files))

    def open_file(self):
        existing = next((p for p in reversed(self.job.files) if Path(p).is_file()), None)
        if existing:
            QDesktopServices.openUrl(QUrl.fromLocalFile(existing))
        else:
            QMessageBox.information(self, "File not found", "The file was moved or deleted.")

    def open_folder(self):
        folder = Path(self.job.files[-1]).parent if self.job.files else Path(self.job.options.folder)
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            QMessageBox.information(self, "Folder not found", "The folder was moved or deleted.")


class MainWindow(QMainWindow):
    def __init__(self, store: Store | None = None, auto_start=True):
        super().__init__()
        self.store = store or Store()
        self.tools = Tools(self.store.root)
        self.worker: Worker | None = None
        self.tool_worker: ToolWorker | None = None
        self.current: Job | None = None
        self.paused = False
        self.closing = False
        self.jobs: list[Job] = []
        self.cards: dict[str, JobCard] = {}
        self.history_cards: dict[str, JobCard] = {}
        self.edit = EditOptions()
        self.studio_edit = EditOptions()
        settings = self.store.load("settings.json", {})
        self.settings = settings if isinstance(settings, dict) else {}
        history = self.store.load("history.json", [])
        if isinstance(history, list):
            for raw in history[-300:]:
                try:
                    self.jobs.append(Job.from_dict(raw))
                except (ValueError, TypeError, KeyError, AttributeError):
                    logging.warning("Skipping invalid history entry")
        self.setWindowTitle(f"Kecheenok v3 · {__version__}")
        self.setWindowIcon(icon("download", "#98e4c5", 64))
        self.resize(1180, 830)
        self.setMinimumSize(980, 740)
        self.setStyleSheet(STYLE.replace("__ASSET_ROOT__", (bundle_dir() / "assets").as_posix()))
        self.persist_timer = QTimer(self)
        self.persist_timer.setSingleShot(True)
        self.persist_timer.timeout.connect(self.save_state)
        self.build_ui()
        self.refresh_counts()
        self.refresh_tools()
        for job in self.jobs:
            self.add_history_card(job)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.enqueue)
        if auto_start:
            QTimer.singleShot(0, self.report_startup)

    def build_ui(self):
        shell = QWidget()
        self.setCentralWidget(shell)
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(202)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 30, 18, 24)
        side.setSpacing(8)
        brand = QHBoxLayout()
        mark = label()
        mark.setPixmap(icon("download", "#98e4c5", 28).pixmap(28, 28))
        brand.addWidget(mark)
        brand.addWidget(label("kecheenok", "brand"))
        side.addLayout(brand)
        side.addWidget(label("  DESKTOP / V3", "muted"))
        side.addSpacing(34)
        self.nav = []
        for i, (title, glyph) in enumerate([("Downloads", "download"), ("History", "history"),
                                            ("Studio", "sliders"), ("Settings", "settings")]):
            nav = button("  " + title, "nav", glyph, lambda checked=False, index=i: self.change_page(index))
            nav.setCheckable(True)
            self.nav.append(nav)
            side.addWidget(nav)
        side.addStretch()
        self.side_count = label("", "muted")
        side.addWidget(self.side_count)
        line = QFrame()
        line.setObjectName("divider")
        side.addWidget(line)
        side.addSpacing(10)
        side.addWidget(label("Your media.\nReady for offline.", "muted"))
        side.addSpacing(8)
        side.addWidget(label(f"VERSION {__version__}", "muted"))
        row.addWidget(sidebar)
        main = QWidget()
        content = QVBoxLayout(main)
        content.setContentsMargins(32, 28, 28, 20)
        content.setSpacing(20)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(6)
        titles.addWidget(label("KECHEENOK / YOUR OFFLINE LIBRARY", "eyebrow"))
        self.page_title = label("Downloads", "title")
        titles.addWidget(self.page_title)
        self.page_subtitle = label("From a link to your folder.", "muted")
        titles.addWidget(self.page_subtitle)
        heading.addLayout(titles, 1)
        self.health = label("", "badge")
        heading.addWidget(self.health, 0, Qt.AlignmentFlag.AlignTop)
        content.addLayout(heading)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.download_page())
        self.pages.addWidget(self.history_page())
        self.pages.addWidget(self.studio_page())
        self.pages.addWidget(self.settings_page())
        content.addWidget(self.pages, 1)
        self.notice = label("Paste a link and add it to the queue.  Ctrl+Enter to add quickly.", "muted", True)
        self.notice.setStyleSheet("color:#929aa8;font-size:11px;")
        content.addWidget(self.notice)
        row.addWidget(main, 1)
        self.change_page(0)

    def change_page(self, index):
        self.pages.setCurrentIndex(index)
        for i, nav in enumerate(self.nav):
            nav.setChecked(i == index)
        texts = [("Downloads", "From a link to your folder."),
                 ("History", "Everything you've saved, in one place."),
                 ("Studio", "Trim files, change speed, or fine-tune audio."),
                 ("Settings", "Customize downloads and keep your tools up to date.")]
        self.page_title.setText(texts[index][0])
        self.page_subtitle.setText(texts[index][1])

    def download_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        composer, form = panel()
        header = QHBoxLayout()
        header.addWidget(label("Add a link", "sectionTitle"))
        header.addStretch()
        header.addWidget(button("Paste from clipboard", "quiet", "paste", self.paste))
        form.addLayout(header)
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("https://www.youtube.com/watch?v=…\nMultiple links? Put each one on a separate line.")
        self.urls.setFixedHeight(79)
        self.urls.setTabChangesFocus(True)
        form.addWidget(self.urls)
        selectors = QHBoxLayout()
        selectors.setSpacing(12)
        fmt_col = QVBoxLayout()
        fmt_col.setSpacing(6)
        fmt_col.addWidget(label("FORMAT", "muted"))
        self.format = QComboBox()
        for value, title in FORMATS.items():
            self.format.addItem(title, value)
        fmt_index = self.format.findData(self.settings.get("format", "mp4"))
        self.format.setCurrentIndex(max(0, fmt_index))
        fmt_col.addWidget(self.format)
        selectors.addLayout(fmt_col, 1)
        quality_col = QVBoxLayout()
        quality_col.setSpacing(6)
        quality_col.addWidget(label("QUALITY", "muted"))
        self.quality = QComboBox()
        for value, title in [(0, "Best available"), (2160, "4K · 2160p"), (1440, "QHD · 1440p"),
                             (1080, "Full HD · 1080p"), (720, "HD · 720p"), (480, "480p"), (360, "360p")]:
            self.quality.addItem(title, value)
        self.quality.setCurrentIndex(max(0, self.quality.findData(self.settings.get("height", 1080))))
        quality_col.addWidget(self.quality)
        selectors.addLayout(quality_col, 1)
        edits_col = QVBoxLayout()
        edits_col.setSpacing(6)
        edits_col.addWidget(label("TRIM & EFFECTS", "muted"))
        self.edit_button = button("No edits", glyph="sliders", action=self.configure_edit)
        edits_col.addWidget(self.edit_button)
        selectors.addLayout(edits_col, 1)
        form.addLayout(selectors)
        self.format.currentIndexChanged.connect(self.format_changed)
        self.format_changed()
        bottom = QHBoxLayout()
        self.playlist = QCheckBox("Full playlist")
        self.playlist.setChecked(bool(self.settings.get("playlist", False)))
        self.subtitles = QCheckBox("Subtitles CZ / EN")
        self.subtitles.setChecked(bool(self.settings.get("subtitles", False)))
        bottom.addWidget(self.playlist)
        bottom.addWidget(self.subtitles)
        bottom.addStretch()
        form.addLayout(bottom)
        destination = QHBoxLayout()
        destination.addWidget(label("Save to", "muted"))
        self.folder = QLineEdit(self.settings.get("folder") or self.download_location())
        self.folder.setReadOnly(True)
        destination.addWidget(self.folder, 1)
        destination.addWidget(button("", "quiet", "folder", self.choose_folder))
        self.add_button = button("Add to queue", "primary", "plus", self.enqueue)
        destination.addWidget(self.add_button)
        form.addLayout(destination)
        layout.addWidget(composer)
        queue_header = QHBoxLayout()
        self.queue_title = label("Queue", "sectionTitle")
        queue_header.addWidget(self.queue_title)
        queue_header.addStretch()
        self.pause_button = button("Pause queue", "quiet", "pause", self.toggle_pause)
        self.pause_button.setToolTip("The current task will finish; the next tasks will wait.")
        queue_header.addWidget(self.pause_button)
        queue_header.addWidget(button("Clear completed", "quiet", action=self.clear_queue))
        layout.addLayout(queue_header)
        queue, self.queue_layout = scroll_content()
        self.queue_empty = QWidget()
        empty = QVBoxLayout(self.queue_empty)
        empty.setContentsMargins(20, 24, 20, 20)
        glyph = label()
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setPixmap(icon("download", "#4b6d5e", 48).pixmap(48, 48))
        empty.addWidget(glyph)
        empty.addSpacing(10)
        title = label("Your next download starts here")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(title)
        hint = label("Video, music, or a whole playlist. Just add a link.", "muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(hint)
        self.queue_layout.addWidget(self.queue_empty)
        layout.addWidget(queue, 1)
        return page

    @staticmethod
    def download_location():
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        return str(Path(folder or str(Path.home() / "Downloads")) / "Kecheenok")

    def history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by title or link…")
        self.search.textChanged.connect(self.filter_history)
        top.addWidget(self.search, 1)
        top.addWidget(button("Clear history", "quiet", action=self.clear_history))
        layout.addLayout(top)
        area, self.history_layout = scroll_content()
        self.history_empty = label("No history yet. Completed tasks will appear here.", "muted", True)
        self.history_layout.addWidget(self.history_empty)
        layout.addWidget(area)
        return page

    def studio_page(self):
        area, layout = scroll_content()
        card, body = panel()
        body.addWidget(label("New edit", "sectionTitle"))
        body.addWidget(label("Choose a local video or audio file. The result is saved as a separate copy.", "muted", True))
        file_row = QHBoxLayout()
        self.studio_source = QLineEdit()
        self.studio_source.setPlaceholderText("Choose a file from your computer")
        file_row.addWidget(self.studio_source, 1)
        file_row.addWidget(button("Choose file", glyph="folder", action=self.choose_source))
        body.addLayout(file_row)
        self.studio_edit_button = button("Set trim and effects", glyph="sliders", action=lambda: self.configure_edit(True))
        body.addWidget(self.studio_edit_button)
        body.addWidget(label("Output folder", "muted"))
        self.studio_folder = QLineEdit(self.settings.get("folder") or self.download_location())
        dest = QHBoxLayout()
        dest.addWidget(self.studio_folder, 1)
        dest.addWidget(button("", "quiet", "folder", lambda: self.choose_folder(True)))
        body.addLayout(dest)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Export copy", "primary", "arrow", self.enqueue_local))
        body.addLayout(actions)
        layout.addWidget(card)
        tips, tip = panel()
        tip.addWidget(label("Small edits. Big difference.", "sectionTitle"))
        tip.addWidget(label("Trim · keep only the part you need\nSpeed · speed up or slow down while preserving pitch\nAudio · bass, reverb, echo, stereo width, and loudness normalization", "muted", True))
        layout.addWidget(tips)
        return area

    def settings_page(self):
        area, layout = scroll_content()
        general, body = panel()
        body.addWidget(label("Downloads", "sectionTitle"))
        limit = QHBoxLayout()
        limit.addWidget(label("Maximum videos per playlist"))
        limit.addStretch()
        self.playlist_limit = QSpinBox()
        self.playlist_limit.setRange(1, 500)
        self.playlist_limit.setValue(int(self.settings.get("playlist_limit", 100)))
        limit.addWidget(self.playlist_limit)
        body.addLayout(limit)
        body.addWidget(label("FLAC and WAV preserve source quality; conversion cannot recover lost detail.", "muted", True))
        body.addWidget(label("Cookies file (optional)", "muted"))
        cookies_row = QHBoxLayout()
        self.cookies = QLineEdit(self.settings.get("cookies_file", ""))
        self.cookies.setPlaceholderText("Netscape cookies.txt · for videos requiring sign-in")
        cookies_row.addWidget(self.cookies, 1)
        cookies_row.addWidget(button("Choose", action=self.choose_cookies))
        cookies_row.addWidget(button("Remove", "quiet", action=self.cookies.clear))
        body.addLayout(cookies_row)
        body.addWidget(label("Cookies provide access to your account. Use a file from your browser; the app stores only its path.", "muted", True))
        body.addWidget(button("Save settings", glyph="check", action=self.save_state))
        layout.addWidget(general)
        runtime, body = panel()
        body.addWidget(label("Tools", "sectionTitle"))
        self.tool_status = label("", "muted", True)
        body.addWidget(self.tool_status)
        self.update_button = button("Repair / update tools", glyph="retry", action=self.update_tools)
        body.addWidget(self.update_button)
        self.update_status = label("Updates help keep up with changes on YouTube.", "muted", True)
        body.addWidget(self.update_status)
        layout.addWidget(runtime)
        diagnostic, body = panel()
        body.addWidget(label("Diagnostics", "sectionTitle"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMinimumHeight(160)
        self.log.setPlaceholderText("Download details and errors appear here.")
        self.log.setStyleSheet("font-family:monospace;font-size:11px;")
        body.addWidget(self.log)
        actions = QHBoxLayout()
        actions.addWidget(button("Open app data folder", "quiet", "folder", self.open_data))
        actions.addStretch()
        actions.addWidget(button("Save log", "quiet", action=self.export_log))
        body.addLayout(actions)
        layout.addWidget(diagnostic)
        return area

    def paste(self):
        text = QApplication.clipboard().text().strip()
        if text:
            existing = self.urls.toPlainText().strip()
            self.urls.setPlainText((existing + "\n" if existing else "") + text)

    def format_changed(self):
        audio = self.format.currentData() in AUDIO_FORMATS
        self.quality.setEnabled(not audio)
        self.quality.setToolTip("Uses the best available audio." if audio else "Maximum resolution; depends on the source.")

    def choose_folder(self, studio=False):
        field = self.studio_folder if studio else self.folder
        folder = QFileDialog.getExistingDirectory(self, "Destination folder", field.text())
        if folder:
            field.setText(folder)
            self.schedule_save()

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Video or audio file", "",
                                              "Media (*.mp4 *.mkv *.webm *.mov *.mp3 *.wav *.flac *.m4a *.ogg *.opus);;All files (*)")
        if path:
            self.studio_source.setText(path)

    def choose_cookies(self):
        path, _ = QFileDialog.getOpenFileName(self, "Cookies file", "", "Text file (*.txt);;All files (*)")
        if path:
            self.cookies.setText(path)

    def configure_edit(self, studio=False):
        dialog = EditDialog(self.studio_edit if studio else self.edit, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if studio:
                self.studio_edit = dialog.options
                self.studio_edit_button.setText("Edits configured" if dialog.options.enabled else "Set trim and effects")
            else:
                self.edit = dialog.options
                self.edit_button.setText("Edits enabled" if dialog.options.enabled else "No edits")

    def current_options(self):
        options = DownloadOptions(folder=self.folder.text(), format=self.format.currentData(), height=self.quality.currentData(),
                                  playlist=self.playlist.isChecked(), playlist_limit=self.playlist_limit.value(),
                                  subtitles=self.subtitles.isChecked(), cookies_file=self.cookies.text().strip(),
                                  edit=dataclasses.replace(self.edit))
        options.validate()
        return options

    def enqueue(self):
        if self.pages.currentIndex() != 0:
            return
        try:
            urls = extract_urls(self.urls.toPlainText())
            options = self.current_options()
            added = 0
            for url in urls:
                if any(j.url == url and j.options == options and j.state in ACTIVE_STATES for j in self.jobs):
                    continue
                self.add_job(Job(url=url, options=dataclasses.replace(options, edit=dataclasses.replace(options.edit))))
                added += 1
            self.urls.clear()
            self.notice.setText(f"Added to queue: {added}." if added else "These links are already queued.")
            self.save_state()
            self.start_next()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Cannot add link", str(exc))

    def enqueue_local(self):
        path = Path(self.studio_source.text())
        if not path.is_file():
            QMessageBox.warning(self, "Choose a file", "Choose an existing video or audio file.")
            return
        try:
            options = DownloadOptions(folder=self.studio_folder.text(), edit=dataclasses.replace(self.studio_edit))
            options.validate()
            self.add_job(Job(url=str(path), options=options, title=path.name, local=True))
            self.save_state()
            self.change_page(0)
            self.start_next()
        except ValueError as exc:
            QMessageBox.warning(self, "Check your settings", str(exc))

    def add_job(self, job):
        self.jobs.append(job)
        card = self.make_card(job)
        self.cards[job.id] = card
        self.queue_layout.addWidget(card)
        self.refresh_counts()

    def make_card(self, job):
        card = JobCard(job)
        card.cancel_requested.connect(self.cancel_job)
        card.retry_requested.connect(self.retry_job)
        card.details_requested.connect(self.job_details)
        return card

    def add_history_card(self, job):
        if job.id not in self.history_cards:
            card = self.make_card(job)
            self.history_cards[job.id] = card
            self.history_layout.insertWidget(0, card)
        self.filter_history()

    def start_next(self):
        if self.worker or self.tool_worker or self.paused or self.closing:
            return
        job = next((j for j in self.jobs if j.state == "waiting"), None)
        if job is None:
            self.refresh_counts()
            return
        self.current = job
        job.state = "running"
        self.worker = Worker(job, self.tools)
        self.worker.changed.connect(self.on_changed)
        self.worker.outcome.connect(self.on_outcome)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        self.refresh_card(job)
        self.refresh_counts()

    def on_changed(self, change):
        job = self.current
        if job is None:
            return
        if "log" in change:
            self.log.appendPlainText(change["log"])
            logging.info("%s", change["log"])
        if "warning" in change and change["warning"] not in job.warnings:
            job.warnings.append(change["warning"])
            self.log.appendPlainText(change["warning"])
            logging.warning("%s", change["warning"])
        for key in ("title", "state", "progress", "detail", "files"):
            if key in change:
                setattr(job, key, change[key])
        self.refresh_card(job)
        if "files" in change or "title" in change:
            self.schedule_save()

    def on_outcome(self, state, files, error):
        job = self.current
        if job is None:
            return
        job.state = state
        if files:
            job.files = files
        job.error = error
        job.progress = 100 if state == "done" else job.progress
        file_count = len(job.files)
        job.detail = {"done": f"Saved · {file_count} {'file' if file_count == 1 else 'files'} · {datetime.now():%H:%M}",
                      "cancelled": "Cancelled · retry to resume partial downloads",
                      "error": error.splitlines()[0][:160] if error else "Download failed"}[state]
        if state == "done" and job.warnings:
            job.detail += " · Subtitle warning"
        self.add_history_card(job)
        self.refresh_card(job)
        self.save_state()
        self.notice.setText(f"Done: {job.title}" if state == "done" else job.detail)

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        self.current = None
        if worker:
            worker.deleteLater()
        self.refresh_counts()
        if self.closing:
            self.close()
        else:
            QTimer.singleShot(150, self.start_next)

    def refresh_card(self, job):
        for cards in (self.cards, self.history_cards):
            if job.id in cards:
                cards[job.id].refresh()

    def cancel_job(self, job_id):
        job = next((j for j in self.jobs if j.id == job_id), None)
        if job is None:
            return
        if self.current and self.current.id == job_id and self.worker:
            job.detail = "Cancelling task…"
            self.worker.cancel()
        elif job.state == "waiting":
            job.state = "cancelled"
            job.detail = "Removed from queue"
            self.add_history_card(job)
            self.save_state()
        self.refresh_card(job)
        self.refresh_counts()

    def retry_job(self, job_id):
        old = next((j for j in self.jobs if j.id == job_id), None)
        if old:
            if any(j.url == old.url and j.options == old.options and j.state in ACTIVE_STATES for j in self.jobs):
                self.notice.setText("This task is already queued.")
                return
            self.add_job(Job(url=old.url, options=dataclasses.replace(old.options, edit=dataclasses.replace(old.options.edit)),
                             title=old.title, local=old.local))
            self.save_state()
            self.change_page(0)
            self.start_next()

    def job_details(self, job_id):
        job = next((j for j in self.jobs if j.id == job_id), None)
        if job:
            QMessageBox.warning(self, "Task details", job.error or "\n\n".join(job.warnings) or job.detail)

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_button.setText("Resume queue" if self.paused else "Pause queue")
        self.pause_button.setIcon(icon("play" if self.paused else "pause"))
        self.notice.setText("The current task will finish. The next tasks will wait." if self.paused else "Queue resumed.")
        self.start_next()

    def refresh_counts(self):
        waiting = sum(j.state == "waiting" for j in self.jobs)
        done = sum(j.state == "done" for j in self.jobs)
        self.queue_title.setText(f"Queue  ·  {waiting} waiting" if waiting else "Queue")
        self.side_count.setText(f"{done} completed {'task' if done == 1 else 'tasks'}")
        self.queue_empty.setVisible(not self.cards)
        self.update_button.setEnabled(self.worker is None and self.tool_worker is None)

    def clear_queue(self):
        for job_id, card in list(self.cards.items()):
            if card.job.state not in ACTIVE_STATES:
                card.deleteLater()
                del self.cards[job_id]
        self.refresh_counts()

    def clear_history(self):
        if not self.history_cards:
            return
        answer = QMessageBox.question(self, "Clear history", "Clear all history entries? Downloaded files will stay on disk.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = set(self.history_cards)
        self.jobs = [j for j in self.jobs if j.id not in removed]
        for cards in (self.history_cards, self.cards):
            for job_id in removed:
                card = cards.pop(job_id, None)
                if card:
                    card.deleteLater()
        self.filter_history()
        self.refresh_counts()
        self.save_state()

    def filter_history(self):
        query = self.search.text().casefold()
        visible = 0
        for card in self.history_cards.values():
            matches = query in (card.job.title + " " + card.job.url).casefold()
            card.setVisible(matches)
            visible += int(matches)
        self.history_empty.setVisible(not visible)
        self.history_empty.setText("No results for this search." if query else "No history yet. Completed tasks will appear here.")

    def schedule_save(self):
        self.persist_timer.start(600)

    def save_state(self):
        self.persist_timer.stop()
        options = {"folder": self.folder.text(), "format": self.format.currentData(), "height": self.quality.currentData(),
                   "playlist": self.playlist.isChecked(), "playlist_limit": self.playlist_limit.value(),
                   "subtitles": self.subtitles.isChecked(), "cookies_file": self.cookies.text().strip()}
        try:
            self.store.save("settings.json", options)
            active = [j for j in self.jobs if j.state in ACTIVE_STATES]
            finished = [j for j in self.jobs if j.state not in ACTIVE_STATES][-300:]
            self.store.save("history.json", [dataclasses.asdict(j) for j in finished + active])
        except OSError as exc:
            logging.exception("Could not persist state")
            self.notice.setText(f"Could not save settings or history: {exc}")

    def report_startup(self):
        if self.store.last_error:
            self.notice.setText(self.store.last_error)
        elif not all(self.tools.check().values()):
            self.notice.setText("Some tools are missing. Open Settings to check their status.")

    def refresh_tools(self):
        status = self.tools.check()
        self.health.setText("●  Ready" if all(status.values()) else "●  Check tools")
        self.tool_status.setText("\n".join(f"{'✓' if installed else '—'}  {name}  ·  {'ready' if installed else 'missing'}" for name, installed in status.items()))

    def update_tools(self):
        if self.worker or self.tool_worker:
            return
        self.tool_worker = ToolWorker(self.store.root / "tools")
        self.tool_worker.message.connect(self.update_status.setText)
        self.tool_worker.outcome.connect(self.tools_updated)
        self.tool_worker.finished.connect(self.tool_worker_finished)
        self.tool_worker.start()
        self.refresh_counts()

    def tools_updated(self, success, message):
        self.update_status.setText(message)
        self.refresh_tools()

    def tool_worker_finished(self):
        worker = self.tool_worker
        self.tool_worker = None
        if worker:
            worker.deleteLater()
        self.refresh_counts()
        self.start_next()

    def open_data(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostics", "kecheenok-log.txt", "Text file (*.txt)")
        if path:
            try:
                Path(path).write_text(self.log.toPlainText(), encoding="utf-8")
                self.notice.setText("Diagnostics saved.")
            except OSError as exc:
                QMessageBox.warning(self, "Cannot save log", str(exc))

    def closeEvent(self, event):
        if self.tool_worker:
            self.notice.setText("Tools are being updated. Close the app when the update finishes.")
            event.ignore()
            return
        if self.worker:
            self.closing = True
            self.paused = True
            self.notice.setText("Stopping the current task…")
            self.worker.cancel()
            event.ignore()
            return
        self.save_state()
        event.accept()
