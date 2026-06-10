"""Folha de estilo Qt (QSS) inspirada no visual do WinBox."""

WINBOX_QSS = """
* { font-size: 12px; }

QMainWindow, QDialog, QWidget#loginRoot {
    background: #ececec;
}

QToolBar {
    background: #e4e4e4;
    border-bottom: 1px solid #b8b8b8;
    spacing: 4px;
    padding: 2px;
}

QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #fdfdfd, stop:1 #dcdcdc);
    border: 1px solid #9a9a9a;
    border-radius: 2px;
    padding: 3px 14px;
    min-height: 18px;
}
QPushButton:hover  { background: #eaf2fb; border-color: #5e90c8; }
QPushButton:pressed { background: #d2e3f5; }
QPushButton:disabled { color: #9a9a9a; background: #e6e6e6; }

QPushButton#dangerBtn:hover { background: #fbeaea; border-color: #c85e5e; }

QLineEdit, QSpinBox, QComboBox {
    background: white;
    border: 1px solid #9a9a9a;
    border-radius: 2px;
    padding: 2px 4px;
    min-height: 18px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #4a7fb5; }

QTableWidget, QTreeWidget, QListWidget {
    background: white;
    border: 1px solid #9a9a9a;
    gridline-color: #e0e0e0;
    selection-background-color: #316ac5;
    selection-color: white;
    alternate-background-color: #f4f7fb;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #fbfbfb, stop:1 #dfdfdf);
    border: 1px solid #c0c0c0;
    border-left: none;
    padding: 3px 6px;
}

QListWidget#sideMenu {
    background: #f5f5f5;
    border: none;
    border-right: 1px solid #b8b8b8;
    outline: none;
}
QListWidget#sideMenu::item {
    padding: 6px 10px;
    border-bottom: 1px solid #e6e6e6;
}
QListWidget#sideMenu::item:selected {
    background: #316ac5;
    color: white;
}
QListWidget#sideMenu::item:hover { background: #dde8f5; }

QMdiArea { background: #cfcfcf; }

QPlainTextEdit#terminal {
    background: #101418;
    color: #d7e3ee;
    font-family: "Consolas", "DejaVu Sans Mono", monospace;
    border: 1px solid #555;
}

QPlainTextEdit#cliPreview {
    background: #fffdf2;
    color: #333;
    font-family: "Consolas", "DejaVu Sans Mono", monospace;
    border: 1px solid #c8b97a;
}

QStatusBar { background: #e4e4e4; border-top: 1px solid #b8b8b8; }

QLabel#titleLabel { font-size: 15px; font-weight: bold; color: #234; }
QLabel#connBadge {
    color: white; background: #2e8b57;
    border-radius: 2px; padding: 2px 8px; font-weight: bold;
}
"""
