import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor, QIcon
from unitool.config import load as load_config
from unitool.translations import set_language
from unitool.ui.main_window import MainWindow


def _resource(rel: str) -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _dark_palette() -> QPalette:
    p = QPalette()
    c = p.setColor
    W   = QPalette.ColorRole.Window
    WT  = QPalette.ColorRole.WindowText
    B   = QPalette.ColorRole.Base
    AB  = QPalette.ColorRole.AlternateBase
    T   = QPalette.ColorRole.Text
    BT  = QPalette.ColorRole.Button
    BTT = QPalette.ColorRole.ButtonText
    H   = QPalette.ColorRole.Highlight
    HT  = QPalette.ColorRole.HighlightedText
    TT  = QPalette.ColorRole.ToolTipBase
    TTT = QPalette.ColorRole.ToolTipText
    PT  = QPalette.ColorRole.PlaceholderText
    # Win11 Mica-inspired dark tokens
    c(W,   QColor(28, 28, 28))    # #1c1c1c  base bg
    c(WT,  QColor(224, 224, 224)) # text primary ~rgba(255,255,255,0.88)
    c(B,   QColor(30, 30, 30))    # #1e1e1e  input base
    c(AB,  QColor(36, 36, 36))    # #242424  alternate row
    c(T,   QColor(224, 224, 224))
    c(BT,  QColor(46, 46, 46))    # #2e2e2e  button/control surface
    c(BTT, QColor(224, 224, 224))
    c(H,   QColor(76, 194, 255))  # #4CC2FF  Win11 accent blue
    c(HT,  QColor(0,   0,   0))
    c(TT,  QColor(36, 36, 36))
    c(TTT, QColor(224, 224, 224))
    c(PT,  QColor(100, 100, 100))
    return p


def _make_qss(check_icon: str) -> str:
    # check_icon must use forward slashes (Qt requirement on all platforms)
    return """
/* ── Global typography ── */
* {
    font-family: 'Segoe UI Variable', 'Segoe UI', -apple-system, 'Helvetica Neue',
                 'Ubuntu', 'Noto Sans', Arial, sans-serif;
    font-size: 13px;
}

/* ── Main window ── */
QMainWindow {
    background: #1c1c1c;
}

/* ── Sidebar ── */
#sidebar {
    background: #202020;
    border-right: 1px solid rgba(255,255,255,0.08);
}

/* ── App brand label ── */
#appBrand {
    font-size: 15px;
    font-weight: 700;
    color: rgba(255,255,255,0.88);
    letter-spacing: 0.3px;
    background: transparent;
}

/* ── Nav items (text/icon colors managed by _NavItem._apply()) ── */
QPushButton#navItem {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
    text-align: left;
}
QPushButton#navItem:hover {
    background: rgba(255,255,255,0.07);
}
QPushButton#navItem:checked {
    background: rgba(76,194,255,0.14);
}
QPushButton#navItem:pressed {
    background: rgba(76,194,255,0.10);
}

/* ── Primary command button ── */
QPushButton#primaryCmd {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #5BCFFF, stop:1 #35AEDE);
    color: #07090b;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.1px;
    border: 1px solid rgba(0,0,0,0.30);
    border-top-color: rgba(255,255,255,0.18);
    border-radius: 5px;
    padding: 4px 16px;
    min-height: 26px;
}
QPushButton#primaryCmd:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #6ED8FF, stop:1 #43BEF0);
}
QPushButton#primaryCmd:pressed {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #2E9FC8, stop:1 #2E9FC8);
    border-top-color: rgba(0,0,0,0.20);
}
QPushButton#primaryCmd:disabled {
    background: rgba(76,194,255,0.18);
    border-color: rgba(76,194,255,0.10);
    color: rgba(255,255,255,0.22);
}

/* ── Ghost / outline command buttons ── */
QPushButton#cmdBtn {
    background: rgba(255,255,255,0.03);
    color: rgba(255,255,255,0.60);
    font-size: 12px;
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 5px;
    padding: 4px 12px;
    min-height: 26px;
}
QPushButton#cmdBtn:hover {
    background: rgba(255,255,255,0.07);
    color: rgba(255,255,255,0.85);
    border-color: rgba(255,255,255,0.16);
}
QPushButton#cmdBtn:pressed {
    background: rgba(255,255,255,0.04);
    color: rgba(255,255,255,0.70);
}
QPushButton#cmdBtn:disabled {
    color: rgba(255,255,255,0.18);
    border-color: rgba(255,255,255,0.06);
    background: transparent;
}

/* ── Danger command button ── */
QPushButton#dangerCmd {
    background: rgba(255,255,255,0.03);
    color: rgba(255,110,110,0.80);
    font-size: 12px;
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 5px;
    padding: 4px 12px;
    min-height: 26px;
}
QPushButton#dangerCmd:hover {
    background: rgba(220,50,50,0.12);
    border-color: rgba(255,80,80,0.28);
    color: #ff8585;
}
QPushButton#dangerCmd:pressed {
    background: rgba(200,40,40,0.09);
    color: rgba(255,110,110,0.90);
}
QPushButton#dangerCmd:disabled {
    color: rgba(255,80,80,0.22);
    border-color: rgba(255,255,255,0.06);
    background: transparent;
}

/* ── Location pill chip ── */
QPushButton#locationChip {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 16px;
    color: rgba(255,255,255,0.72);
    padding: 3px 12px;
    font-size: 12px;
}
QPushButton#locationChip:hover {
    background: rgba(255,255,255,0.10);
    color: rgba(255,255,255,0.92);
}

/* ── Add location dashed pill ── */
QPushButton#addLocationBtn {
    background: transparent;
    border: 1px dashed rgba(255,255,255,0.20);
    border-radius: 16px;
    color: rgba(255,255,255,0.45);
    padding: 3px 14px;
    font-size: 12px;
}
QPushButton#addLocationBtn:hover {
    border-color: rgba(76,194,255,0.50);
    color: #4CC2FF;
}

/* ── Results card ── */
QFrame#resultsCard {
    background: #242424;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
}

/* ── Session banner ── */
QFrame#sessionBanner {
    background: rgba(76,194,255,0.08);
    border: 1px solid rgba(76,194,255,0.22);
    border-radius: 6px;
}

/* ── Page area ── */
#pageArea {
    background: #1c1c1c;
}

/* ── Command bar strip ── */
#cmdBar {
    background: #1c1c1c;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}

/* ── Left panel ── */
#leftPanel {
    background: #181818;
    border-right: 1px solid rgba(255,255,255,0.07);
}
#leftPanel QGroupBox::title {
    background: #181818;
}

/* ── Generic push buttons (fallback) ── */
QPushButton {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 5px;
    color: rgba(255,255,255,0.62);
    font-size: 12px;
    padding: 3px 10px;
    min-height: 24px;
}
QPushButton:hover {
    background: rgba(255,255,255,0.08);
    color: rgba(255,255,255,0.85);
    border-color: rgba(255,255,255,0.16);
}
QPushButton:pressed {
    background: rgba(255,255,255,0.03);
    color: rgba(255,255,255,0.70);
}
QPushButton:disabled {
    color: rgba(255,255,255,0.18);
    border-color: rgba(255,255,255,0.06);
    background: transparent;
}

/* ── Tab widget ── */
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabWidget::tab-bar {
    alignment: left;
}
QTabBar::tab {
    background: transparent;
    color: rgba(255,255,255,0.45);
    font-size: 12px;
    padding: 7px 18px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 2px;
}
QTabBar::tab:hover {
    color: rgba(255,255,255,0.75);
    background: rgba(255,255,255,0.04);
}
QTabBar::tab:selected {
    color: rgba(255,255,255,0.92);
    border-bottom: 2px solid #4CC2FF;
    background: rgba(76,194,255,0.06);
}
QTabBar::tab:disabled {
    color: rgba(255,255,255,0.20);
}

/* ── Group boxes ── */
QGroupBox {
    font-size: 10px;
    font-weight: 700;
    color: rgba(255,255,255,0.35);
    border: none;
    border-top: 1px solid rgba(255,255,255,0.08);
    margin-top: 16px;
    padding-top: 10px;
    letter-spacing: 0.8px;
    text-transform: uppercase;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 0px;
    padding: 0 6px 0 0;
    background: #1c1c1c;
}

/* ── Line edit (Win11 TextBox style) ── */
QLineEdit {
    padding: 7px 12px;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.14);
    border-radius: 4px 4px 0 0;
    background: rgba(255,255,255,0.05);
    color: rgba(255,255,255,0.88);
    font-size: 13px;
    selection-background-color: rgba(76,194,255,0.35);
    selection-color: #ffffff;
}
QLineEdit:focus {
    border-bottom: 2px solid #4CC2FF;
    background: rgba(255,255,255,0.07);
}
QLineEdit:hover:!focus {
    background: rgba(255,255,255,0.07);
    border-bottom-color: rgba(255,255,255,0.28);
}

/* ── Combo box ── */
QComboBox {
    padding: 4px 10px;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 6px;
    background: rgba(255,255,255,0.05);
    color: rgba(255,255,255,0.72);
    font-size: 12px;
    min-height: 24px;
}
QComboBox:hover {
    border-color: rgba(255,255,255,0.22);
    background: rgba(255,255,255,0.08);
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    width: 10px;
    height: 10px;
}
QComboBox QAbstractItemView {
    background: #2e2e2e;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 6px;
    color: rgba(255,255,255,0.80);
    selection-background-color: rgba(76,194,255,0.22);
    selection-color: #ffffff;
    outline: none;
    padding: 4px;
}

/* ── All item views ── */
QListView, QTreeView, QTableView,
QListWidget, QTreeWidget, QTableWidget {
    background-color: transparent;
    alternate-background-color: rgba(255,255,255,0.02);
    border: none;
    border-radius: 0;
    color: rgba(255,255,255,0.80);
    outline: none;
    gridline-color: rgba(255,255,255,0.05);
    selection-background-color: rgba(76,194,255,0.18);
    selection-color: #ffffff;
    show-decoration-selected: 1;
}
QListView::item, QTreeView::item, QTableView::item,
QListWidget::item, QTreeWidget::item, QTableWidget::item {
    padding: 4px 6px;
    border: none;
    min-height: 24px;
}
QListView::item:hover, QTreeView::item:hover,
QTableView::item:hover, QListWidget::item:hover,
QTreeWidget::item:hover, QTableWidget::item:hover {
    background-color: rgba(255,255,255,0.05);
    color: rgba(255,255,255,0.92);
}
QListView::item:selected, QTreeView::item:selected,
QTableView::item:selected, QListWidget::item:selected,
QTreeWidget::item:selected, QTableWidget::item:selected {
    background-color: rgba(76,194,255,0.18);
    color: #ffffff;
}

/* ── Header ── */
QHeaderView {
    background: transparent;
    border: none;
}
QHeaderView::section {
    background: rgba(255,255,255,0.03);
    border: none;
    border-right: 1px solid rgba(255,255,255,0.06);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding: 5px 10px;
    font-size: 10px;
    font-weight: 700;
    color: rgba(255,255,255,0.35);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* ── Scrollbars (6px thin) ── */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.18);
    min-height: 32px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255,255,255,0.32);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: rgba(255,255,255,0.18);
    min-width: 32px;
    border-radius: 3px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(255,255,255,0.32);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── Progress bar (4px thin bar) ── */
QProgressBar {
    border: none;
    border-radius: 2px;
    background: rgba(255,255,255,0.08);
    color: transparent;
    text-align: center;
    max-height: 4px;
}
QProgressBar::chunk {
    border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4CC2FF, stop:1 #38aee8);
}

/* ── Check / Radio ── */
QCheckBox, QRadioButton {
    color: rgba(255,255,255,0.65);
    spacing: 8px;
    font-size: 12px;
    background: transparent;
}
QCheckBox:hover, QRadioButton:hover {
    color: rgba(255,255,255,0.88);
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px;
    height: 15px;
}
QCheckBox::indicator:unchecked {
    background: transparent;
    border: 1px solid rgba(255,255,255,0.28);
    border-radius: 3px;
}
QCheckBox::indicator:unchecked:hover {
    border-color: rgba(76,194,255,0.70);
}
QCheckBox::indicator:checked {
    background: #4CC2FF;
    border: none;
    border-radius: 3px;
    image: url(""" + check_icon + """);
}

/* ── Item-view checkboxes (QTreeWidget / QTableWidget rows) ── */
QTreeView::indicator, QTreeWidget::indicator,
QTableView::indicator, QTableWidget::indicator {
    width: 15px;
    height: 15px;
}
QTreeView::indicator:unchecked, QTreeWidget::indicator:unchecked,
QTableView::indicator:unchecked, QTableWidget::indicator:unchecked {
    background: transparent;
    border: 1px solid rgba(255,255,255,0.25);
    border-radius: 3px;
}
QTreeView::indicator:unchecked:hover, QTreeWidget::indicator:unchecked:hover,
QTableView::indicator:unchecked:hover, QTableWidget::indicator:unchecked:hover {
    border-color: rgba(76,194,255,0.70);
}
QTreeView::indicator:checked, QTreeWidget::indicator:checked,
QTableView::indicator:checked, QTableWidget::indicator:checked {
    background: #4CC2FF;
    border: none;
    border-radius: 3px;
    image: url(""" + check_icon + """);
}

/* ── Splitter ── */
QSplitter::handle {
    background: rgba(255,255,255,0.06);
}
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical   { height: 1px; }

/* ── Status bar ── */
QStatusBar {
    background: #181818;
    border-top: 1px solid rgba(255,255,255,0.06);
    color: rgba(255,255,255,0.35);
    font-size: 12px;
    padding: 0 8px;
}
QStatusBar::item { border: none; }

/* ── Scroll area ── */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ── Tooltip ── */
QToolTip {
    background: #2e2e2e;
    color: rgba(255,255,255,0.88);
    border: 1px solid rgba(255,255,255,0.14);
    padding: 5px 10px;
    border-radius: 6px;
    font-size: 12px;
}

"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('UniTool')
    app.setWindowIcon(QIcon(_resource('icon.png')))
    app.setStyle('Fusion')
    app.setPalette(_dark_palette())
    check_icon = _resource(os.path.join('resources', 'check.svg')).replace('\\', '/')
    app.setStyleSheet(_make_qss(check_icon))

    set_language(load_config().get('language', 'en'))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
