"""Use PySide6 in releases; support the system Qt binding for source development."""
try:
    from PySide6.QtCore import Qt, Signal, QThread, QTimer, QSize, QUrl, QStandardPaths, QLockFile, QLocale
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QDesktopServices, QKeySequence, QShortcut
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
        QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QScrollArea, QPlainTextEdit,
        QLineEdit, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox, QProgressBar, QFileDialog,
        QMessageBox, QDialog, QDialogButtonBox, QSizePolicy)
except ImportError:
    from PyQt6.QtCore import Qt, pyqtSignal as Signal, QThread, QTimer, QSize, QUrl, QStandardPaths, QLockFile, QLocale
    from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QDesktopServices, QKeySequence, QShortcut
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
        QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QScrollArea, QPlainTextEdit,
        QLineEdit, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox, QProgressBar, QFileDialog,
        QMessageBox, QDialog, QDialogButtonBox, QSizePolicy)
