"""Folha de estilo Qt (QSS) inspirada no visual do WinBox.

Todas as cores — inclusive a cor do texto — são definidas explicitamente
para que o aplicativo fique legível em qualquer sistema, mesmo quando o
tema do sistema operacional é escuro (caso contrário o Qt herda texto
branco sobre os fundos claros e nada fica visível).
"""

import tempfile

_CHECK_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 13 13'>"
    "<rect width='13' height='13' rx='2' fill='#316ac5'/>"
    "<polyline points='2,7 5,10 11,3' stroke='white' stroke-width='2.2' "
    "fill='none' stroke-linecap='round' stroke-linejoin='round'/>"
    "</svg>"
)
_tmp = tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w")
_tmp.write(_CHECK_SVG)
_tmp.flush()
_CHECK_PATH = _tmp.name.replace("\\", "/")

_QSS_TEMPLATE = """
* {
    font-size: 12px;
    color: #1c1c1c;
}

QMainWindow, QDialog, QWidget {
    background: #ececec;
    color: #1c1c1c;
}

QToolBar {
    background: #e4e4e4;
    border-bottom: 1px solid #b8b8b8;
    spacing: 4px;
    padding: 2px;
}

QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #fdfdfd, stop:1 #d6d6d6);
    color: #1c1c1c;
    border: 1px solid #8a8a8a;
    border-radius: 2px;
    padding: 3px 14px;
    min-height: 18px;
}
QPushButton:hover  { background: #dcebfb; border-color: #4a7fb5; }
QPushButton:pressed { background: #c4dcf2; }
QPushButton:disabled { color: #8c8c8c; background: #e6e6e6; }

QPushButton#dangerBtn:hover { background: #fbdcdc; border-color: #c85e5e; }

QLineEdit, QSpinBox, QComboBox {
    background: #ffffff;
    color: #111111;
    border: 1px solid #8a8a8a;
    border-radius: 2px;
    padding: 2px 4px;
    min-height: 18px;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #316ac5; }
QLineEdit::placeholder { color: #8c8c8c; }
QComboBox QAbstractItemView {
    background: #ffffff;
    color: #111111;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
}

QCheckBox, QLabel, QRadioButton { color: #1c1c1c; background: transparent; }

QCheckBox::indicator {
    width: 13px;
    height: 13px;
    border: 1px solid #7a8a9a;
    border-radius: 2px;
    background: #ffffff;
}
QCheckBox::indicator:hover  { border-color: #316ac5; }
QCheckBox::indicator:checked {
    border-color: #1d4e9e;
    image: url(__CHECK_PATH__);
}
QCheckBox::indicator:disabled { background: #e0e0e0; border-color: #b8b8b8; }

QTableWidget, QTreeWidget, QListWidget {
    background: #ffffff;
    color: #111111;
    border: 1px solid #8a8a8a;
    gridline-color: #d8d8d8;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
    alternate-background-color: #eef3fa;
}
QTableWidget::item, QListWidget::item { color: #111111; }
QTableWidget::item:selected, QListWidget::item:selected {
    background: #316ac5;
    color: #ffffff;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #fbfbfb, stop:1 #d9d9d9);
    color: #1c1c1c;
    border: 1px solid #b4b4b4;
    border-left: none;
    padding: 3px 6px;
}
QTableCornerButton::section { background: #d9d9d9; border: 1px solid #b4b4b4; }

QListWidget#sideMenu {
    background: #f3f3f3;
    border: none;
    border-right: 1px solid #b0b0b0;
    outline: none;
}
QListWidget#sideMenu::item {
    color: #1c1c1c;
    padding: 6px 10px;
    border-bottom: 1px solid #e2e2e2;
}
QListWidget#sideMenu::item:selected {
    background: #316ac5;
    color: #ffffff;
}
QListWidget#sideMenu::item:hover { background: #d5e4f5; color: #1c1c1c; }

QMdiArea { background: #c4c4c4; }
QMdiSubWindow { background: #ececec; }

QPlainTextEdit, QTextEdit {
    background: #ffffff;
    color: #1c1c1c;
    border: 1px solid #8a8a8a;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
}

QPlainTextEdit#terminal {
    background: #101418;
    color: #d7e3ee;
    font-family: "Consolas", "DejaVu Sans Mono", monospace;
    border: 1px solid #555555;
}

QPlainTextEdit#cliPreview {
    background: #fffbe8;
    color: #3a3000;
    font-family: "Consolas", "DejaVu Sans Mono", monospace;
    border: 1px solid #c8b97a;
}

QStatusBar { background: #e4e4e4; color: #1c1c1c; border-top: 1px solid #b8b8b8; }
QStatusBar QLabel { color: #1c1c1c; }

QMenu { background: #f7f7f7; color: #1c1c1c; border: 1px solid #9a9a9a; }
QMenu::item:selected { background: #316ac5; color: #ffffff; }

QScrollBar:vertical {
    background: #e4e4e4; width: 14px; border: 1px solid #c4c4c4;
}
QScrollBar::handle:vertical {
    background: #bdbdbd; min-height: 24px; border-radius: 2px; margin: 1px;
}
QScrollBar::handle:vertical:hover { background: #9fb8d4; }
QScrollBar:horizontal {
    background: #e4e4e4; height: 14px; border: 1px solid #c4c4c4;
}
QScrollBar::handle:horizontal {
    background: #bdbdbd; min-width: 24px; border-radius: 2px; margin: 1px;
}
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }

QToolTip { background: #fffbe8; color: #1c1c1c; border: 1px solid #9a9a9a; }

QLabel#titleLabel { font-size: 15px; font-weight: bold; color: #223344; }
QLabel#connBadge {
    color: #ffffff; background: #2e8b57;
    border-radius: 2px; padding: 2px 8px; font-weight: bold;
}

/* ───────────────────────── Chrome estilo WinBox ───────────────────── */
QMenuBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #f5f5f5, stop:1 #e2e2e2);
    color: #1c1c1c;
    border-bottom: 1px solid #b8b8b8;
}
QMenuBar::item { background: transparent; padding: 4px 10px; }
QMenuBar::item:selected { background: #316ac5; color: #ffffff; }

QToolBar#winToolbar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #eef0f2, stop:1 #dde1e5);
    border: none;
    border-bottom: 1px solid #b4b8bd;
    padding: 3px 6px;
    spacing: 5px;
}
QToolBar#winToolbar QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 2px 5px;
}
QToolBar#winToolbar QToolButton:hover {
    background: #dcebfb; border-color: #9cc0e6;
}
QToolBar#winToolbar QToolButton:disabled { color: #a0a0a0; }

QPushButton#safeMode {
    padding: 3px 12px;
    border: 1px solid #8a8a8a;
    border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #fdfdfd, stop:1 #dadada);
}
QPushButton#safeMode:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #cdeccd, stop:1 #95cd95);
    border-color: #4f9a4f; color: #133f0d; font-weight: bold;
}

QLabel#sessionField {
    background: #ffffff;
    border: 1px solid #9aa0a6;
    border-radius: 2px;
    padding: 2px 8px;
    color: #14304f;
}
QLabel#loadText { color: #243240; }

/* Faixa vertical com o nome do produto (como "RouterOS WinBox") */
QLabel#prodStrip {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2f5fa0, stop:1 #4078c4);
    color: #eaf2fc;
}

/* Menu lateral com ícones */
QListWidget#sideMenu::item { padding: 7px 8px; }

/* Abas (janelas internas estilo WinBox) */
QTabWidget::pane { border: 1px solid #9aa0a6; top: -1px; background: #ececec; }
QTabBar::tab {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #f3f3f3, stop:1 #dadada);
    color: #1c1c1c;
    border: 1px solid #a8acb1;
    border-bottom: none;
    padding: 3px 12px;
    margin-right: 1px;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
}
QTabBar::tab:selected { background: #ffffff; }
QTabBar::tab:hover { background: #e6f0fb; }

/* Barra de ferramentas dos painéis (+ − ✓ ✗ … Find) */
QFrame#panelToolbar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #f1f3f5, stop:1 #e2e5e9);
    border: 1px solid #c2c6cb;
    border-radius: 2px;
}
QFrame#panelToolbar QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 2px 6px;
    color: #1c1c1c;
}
QFrame#panelToolbar QToolButton:hover { background: #dcebfb; border-color: #9cc0e6; }
QFrame#panelToolbar QToolButton:pressed { background: #c4dcf2; }
QFrame#panelToolbar QToolButton:disabled { color: #9a9a9a; }

QLineEdit#findBox {
    background: #ffffff;
    border: 1px solid #9aa0a6;
    border-radius: 2px;
    padding: 2px 6px 2px 6px;
    min-width: 130px;
}
QLabel#itemCount { color: #3a4a5a; }
"""

WINBOX_QSS = _QSS_TEMPLATE.replace("__CHECK_PATH__", _CHECK_PATH)
