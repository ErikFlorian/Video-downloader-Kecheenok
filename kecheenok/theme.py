from .qt import QIcon, QPixmap, QPainter, QSvgRenderer, Qt

PATHS = {
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
    "history": '<path d="M3 11a9 9 0 1 1 2.4 7M3 4v7h7m2-4v5l3 2"/>',
    "sliders": '<path d="M4 7h7m6 0h3M4 17h3m6 0h7"/><circle cx="14" cy="7" r="3"/><circle cx="10" cy="17" r="3"/>',
    "settings": '<path d="m9 3-1 3-3 1-1 3 2 2-2 2 1 3 3 1 1 3h6l1-3 3-1 1-3-2-2 2-2-1-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
    "folder": '<path d="M3 6h7l2 2h9v12H3Z"/>',
    "paste": '<path d="M9 5H5v16h14V5h-4"/><rect x="9" y="3" width="6" height="4" rx="1"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "retry": '<path d="M20 11a8 8 0 1 0-2 7M20 4v7h-7"/>',
    "play": '<path d="m8 4 12 8-12 8Z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "link": '<path d="m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m0 2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(2 0)"/>',
    "video": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 8 6 4-6 4Z"/>',
    "audio": '<path d="M10 18V5l10-2v13M10 8l10-2"/><ellipse cx="6" cy="18" rx="4" ry="3"/><ellipse cx="16" cy="16" rx="4" ry="3"/>',
    "alert": '<path d="m12 3 10 18H2Zm0 6v5m0 3v1"/>',
    "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
}


def icon(name: str, color: str = "#a5adbb", size: int = 22) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


STYLE = """
QWidget { background: #101216; color: #eef1f5; font-size: 13px; }
QMainWindow { background: #101216; }
QLabel { background: transparent; }
QLabel#muted { color: #929aa8; }
QLabel#eyebrow { color: #83d9ba; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel#title { font-size: 30px; font-weight: 650; letter-spacing: -1px; }
QLabel#sectionTitle { font-size: 17px; font-weight: 600; }
QLabel#brand { font-size: 22px; font-weight: 650; letter-spacing: -1px; }
QLabel#badge { background: #20362e; color: #98e1c4; border-radius: 12px; padding: 6px 12px; font-size: 11px; }
QLabel#warning { background: #382d20; color: #edc28a; border-radius: 8px; padding: 10px; }
QFrame#sidebar { background: #15181e; border-right: 1px solid #272c35; }
QFrame#panel, QFrame#job { background: #191d24; border: 1px solid #2b303a; border-radius: 12px; }
QFrame#divider { background: #2b303a; border: none; min-height: 1px; max-height: 1px; }
QPushButton { background: #252b35; border: 1px solid #353d4b; border-radius: 7px; padding: 9px 13px; font-weight: 500; }
QPushButton:hover { background: #303847; border-color: #536174; }
QPushButton:pressed { background: #384454; }
QPushButton:disabled { color: #626b78; background: #1d222a; border-color: #2b303a; }
QPushButton#primary { background: #98e4c5; color: #10241c; border: 1px solid #98e4c5; font-weight: 650; padding: 10px 17px; }
QPushButton#primary:hover { background: #b4f1d8; border-color: #b4f1d8; }
QPushButton#primary:disabled { background: #294238; color: #77998a; border-color: #294238; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #a8b1bf; padding: 6px 9px; }
QPushButton#quiet:hover { background: #292f39; color: #fff; }
QPushButton#nav { background: transparent; border: none; border-radius: 7px; text-align: left; padding: 12px 16px; color: #a2aaba; }
QPushButton#nav:hover { background: #222832; }
QPushButton#nav:checked { background: #263a32; color: #acf0d2; }
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #12161c; border: 1px solid #343c49; border-radius: 7px; padding: 8px 10px; selection-background-color: #355c4d; }
QFrame#panel QPlainTextEdit, QFrame#panel QLineEdit, QFrame#panel QComboBox,
QFrame#panel QSpinBox, QFrame#panel QDoubleSpinBox { background: #12161c; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #92dabc; }
QLineEdit:disabled, QComboBox:disabled { color: #67707e; }
QComboBox { padding-right: 27px; min-height: 17px; }
QComboBox::drop-down { width: 24px; border: none; }
QComboBox::down-arrow { image: url("__ASSET_ROOT__/chevron.svg"); width: 12px; height: 12px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 21px; border: none; background: #252b35; border-top-right-radius: 6px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 21px; border: none; background: #252b35; border-bottom-right-radius: 6px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("__ASSET_ROOT__/chevron-up.svg"); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("__ASSET_ROOT__/chevron.svg"); width: 10px; height: 10px; }
QComboBox QAbstractItemView { background: #222832; selection-background-color: #355c4d; border: 1px solid #3c4656; padding: 5px; }
QCheckBox { spacing: 9px; color: #b8c1ce; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #586373; border-radius: 4px; background: #14181f; }
QCheckBox::indicator:checked { background: #98e4c5; border-color: #98e4c5; image: url("__ASSET_ROOT__/check.svg"); }
QProgressBar { border: none; background: #303643; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #98e4c5; border-radius: 3px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #414957; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QDialog { background: #191d24; }
QToolTip { background: #303847; color: #eef1f5; border: 1px solid #4c586a; padding: 6px; }
"""
