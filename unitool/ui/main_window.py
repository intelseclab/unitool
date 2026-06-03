import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QPushButton, QLabel, QComboBox,
    QListWidget, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QGroupBox, QRadioButton, QCheckBox,
    QFileDialog, QMessageBox, QStatusBar,
    QAbstractItemView, QHeaderView, QMenu, QButtonGroup,
    QFrame, QStackedWidget,
)
from PyQt6.QtCore import Qt, QSize, QEvent
from PyQt6.QtGui import QColor, QBrush, QFont, QCursor

from ..scanner import Scanner, FILE_TYPE_EXTENSIONS, fmt_size
from ..deleter import Deleter
from ..translations import tr, set_language, current_language, available_languages, is_rtl
from ..config import load as load_config, save as save_config
from ..cache import load_hash_cache, save_session, load_session, clear_session
from ..platform_utils import open_path, open_folder
from .search_widget import SearchWidget
from .settings_widget import SettingsWidget
from .privacy_widget import PrivacyWidget
from .junk_widget import JunkWidget
from .startup_widget import StartupWidget
from .browser_widget import BrowserWidget
from .uninstaller_widget import UninstallerWidget
from .syscheck_widget import SysCheckWidget
from .installer_widget import InstallerWidget
from .netmon_widget import NetMonWidget
from .dpi_widget import DpiWidget
from ..updater import UpdateChecker, current_version, RELEASES_URL

_COLOR_KEEP   = QColor(15, 40, 15)
_COLOR_DELETE = QColor(58, 12, 12)

# QTreeWidgetItem data roles
_ROLE_FILE  = Qt.ItemDataRole.UserRole       # {path, size, reason_key}
_ROLE_GROUP = Qt.ItemDataRole.UserRole + 1   # {n, count, wasted, reason_key}


class _NavItem(QPushButton):
    """Sidebar nav button with a fixed-width icon column so text always aligns."""

    _COLOR_ACTIVE  = 'rgba(255,255,255,0.95)'
    _COLOR_HOVER   = 'rgba(255,255,255,0.88)'
    _COLOR_NORMAL  = 'rgba(255,255,255,0.55)'

    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName('navItem')
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setFixedHeight(38)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(0)

        # Fixed-width icon area — emoji and symbols both stay in their lane
        self._icon_lbl = QLabel(icon)
        self._icon_lbl.setFixedWidth(28)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._text_lbl = QLabel(label)
        self._text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        lay.addWidget(self._icon_lbl)
        lay.addWidget(self._text_lbl, 1)

        self.toggled.connect(lambda _: self._sync_colors())
        self._sync_colors()

    # Keep backward-compat with places that call setText() during retranslation
    def setText(self, _text: str):
        pass   # Text is managed via set_nav_text(); ignore legacy calls

    def set_nav_text(self, icon: str, label: str):
        self._icon_lbl.setText(icon)
        self._text_lbl.setText(label)
        self._sync_colors()

    def enterEvent(self, e):
        super().enterEvent(e)
        if not self.isChecked():
            self._apply(self._COLOR_HOVER, '400')

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._sync_colors()

    def _sync_colors(self):
        if self.isChecked():
            self._apply(self._COLOR_ACTIVE, '600')
        else:
            self._apply(self._COLOR_NORMAL, '400')

    def _apply(self, color: str, weight: str):
        base = 'background: transparent; border: none;'
        self._icon_lbl.setStyleSheet(f'{base} color: {color}; font-size: 14px;')
        self._text_lbl.setStyleSheet(f'{base} color: {color}; font-size: 13px; font-weight: {weight};')


class _StatChip(QFrame):
    """Stat card: small muted label on top, bold value below, colored accent top-border."""

    def __init__(self, label: str, value: str = '—', accent: str = '#4CC2FF', parent=None):
        super().__init__(parent)
        self.setStyleSheet(f'''
            QFrame {{
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-top: 2px solid {accent};
                border-radius: 6px;
                min-width: 112px;
            }}
        ''')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(2)
        self._lbl = QLabel(label.upper())
        self._lbl.setStyleSheet(
            'font-size: 9px; font-weight: 700; color: rgba(255,255,255,0.32); '
            'letter-spacing: 0.8px; background: transparent; border: none;'
        )
        self._val = QLabel(value)
        self._val.setStyleSheet(
            'font-size: 17px; font-weight: 600; color: rgba(255,255,255,0.88); '
            'background: transparent; border: none;'
        )
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)

    def set_value(self, v: str):
        self._val.setText(v)

    def set_label(self, label: str):
        self._lbl.setText(label.upper())


class FolderDropList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        existing = {self.item(i).text() for i in range(self.count())}
        for url in event.mimeData().urls():
            path = os.path.normpath(url.toLocalFile())
            if not path:
                continue
            if os.path.isfile(path):
                path = os.path.dirname(path)
            if os.path.isdir(path) and path not in existing:
                self.addItem(path)
                existing.add(path)
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._scanner: Scanner | None = None
        self._deleter: Deleter | None = None
        self._delete_items: dict[str, QTreeWidgetItem] = {}
        self._group_count = 0
        # stored stat values for retranslation
        self._sv_groups: int | None = None
        self._sv_dupes:  int | None = None
        self._sv_bytes:  int | None = None
        self._type_keys = list(FILE_TYPE_EXTENSIONS.keys())
        # session / cache state
        self._session_groups: list[dict] = []
        self._last_folders: list[str] = []
        self._last_file_type: str = 'All Files'
        self._saved_session: dict | None = None
        self._setup_ui()
        self._apply_saved_language()
        self._start_update_check()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        self.resize(1280, 780)
        self._build_central()
        self._build_statusbar()
        self._check_session()

    def _build_central(self):
        self._search_widget      = SearchWidget()
        self._settings_widget    = SettingsWidget()
        self._privacy_widget     = PrivacyWidget()
        self._junk_widget        = JunkWidget()
        self._startup_widget     = StartupWidget()
        self._browser_widget     = BrowserWidget()
        self._uninstaller_widget = UninstallerWidget()
        self._syscheck_widget    = SysCheckWidget()
        self._installer_widget   = InstallerWidget()
        self._netmon_widget      = NetMonWidget()
        self._dpi_widget         = DpiWidget()

        # ── Sidebar ──
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(220)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(8, 16, 8, 12)
        sb_layout.setSpacing(2)

        brand = QLabel('UniTool')
        brand.setObjectName('appBrand')
        brand.setContentsMargins(12, 0, 0, 0)
        sb_layout.addWidget(brand)
        sb_layout.addSpacing(12)

        self._nav_dupes       = _NavItem('⦿', tr('tab_duplicates'))
        self._nav_search      = _NavItem('⌕', tr('tab_search'))
        self._nav_privacy     = _NavItem('🛡', tr('tab_privacy'))
        self._nav_junk        = _NavItem('🗑', tr('tab_junk'))
        self._nav_startup     = _NavItem('🚀', tr('tab_startup'))
        self._nav_browser     = _NavItem('🌐', tr('tab_browser'))
        self._nav_uninstaller = _NavItem('📦', tr('tab_uninstaller'))
        self._nav_syscheck    = _NavItem('🔍', tr('tab_syscheck'))
        self._nav_installer   = _NavItem('⬇', tr('tab_installer'))
        self._nav_netmon      = _NavItem('◉', tr('tab_netmon'))
        self._nav_dpi         = _NavItem('🌐', tr('tab_dpi'))
        self._nav_settings    = _NavItem('⚙', tr('tab_settings'))

        sb_layout.addWidget(self._nav_dupes)
        sb_layout.addWidget(self._nav_search)
        sb_layout.addWidget(self._nav_privacy)
        sb_layout.addWidget(self._nav_junk)
        sb_layout.addWidget(self._nav_startup)
        sb_layout.addWidget(self._nav_browser)
        sb_layout.addWidget(self._nav_uninstaller)
        sb_layout.addWidget(self._nav_syscheck)
        sb_layout.addWidget(self._nav_installer)
        sb_layout.addWidget(self._nav_netmon)
        sb_layout.addWidget(self._nav_dpi)
        sb_layout.addStretch()

        # Thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet('background: rgba(255,255,255,0.08); max-height: 1px; border: none;')
        sb_layout.addWidget(sep)
        sb_layout.addSpacing(4)

        sb_layout.addWidget(self._nav_settings)
        sb_layout.addSpacing(8)

        # Language row
        lang_row = QHBoxLayout()
        lang_row.setContentsMargins(12, 0, 4, 0)
        lang_row.setSpacing(6)
        self._lang_lbl = QLabel(tr('lang_label') + ':')
        self._lang_lbl.setStyleSheet('color: rgba(255,255,255,0.35); font-size: 11px;')
        self._lang_combo = QComboBox()
        self._lang_combo.setFixedHeight(26)
        # Populate from the JSON language files discovered at runtime
        for code, display in available_languages():
            self._lang_combo.addItem(display, code)
        lang_row.addWidget(self._lang_lbl)
        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()
        sb_layout.addLayout(lang_row)

        # ── Stacked pages ──
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_duplicates_page())  # index 0
        self._stack.addWidget(self._search_widget)            # index 1
        self._stack.addWidget(self._privacy_widget)           # index 2
        self._stack.addWidget(self._junk_widget)              # index 3
        self._stack.addWidget(self._startup_widget)           # index 4
        self._stack.addWidget(self._browser_widget)           # index 5
        self._stack.addWidget(self._uninstaller_widget)       # index 6
        self._stack.addWidget(self._syscheck_widget)          # index 7
        self._stack.addWidget(self._installer_widget)          # index 8
        self._stack.addWidget(self._netmon_widget)            # index 9
        self._stack.addWidget(self._dpi_widget)               # index 10
        self._stack.addWidget(self._settings_widget)          # index 11

        # ── Root layout ──
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(sidebar)

        # Wrap stack in a VBox so the update banner can sit above it
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._build_update_banner())
        right_layout.addWidget(self._stack, 1)
        root_layout.addWidget(right, 1)

        self.setCentralWidget(root)

        # ── Wire nav buttons ──
        self._nav_items = [
            self._nav_dupes, self._nav_search, self._nav_privacy,
            self._nav_junk, self._nav_startup, self._nav_browser,
            self._nav_uninstaller, self._nav_syscheck, self._nav_installer,
            self._nav_netmon, self._nav_dpi, self._nav_settings,
        ]
        for idx, btn in enumerate(self._nav_items):
            btn.clicked.connect(lambda checked, i=idx: self._on_nav(i))

        # Start on duplicates page
        self._nav_dupes.setChecked(True)
        self._stack.setCurrentIndex(0)

    # ── Update banner ─────────────────────────────────────────────────────────

    def _build_update_banner(self) -> QFrame:
        self._update_frame = QFrame()
        self._update_frame.setObjectName('updateBanner')
        self._update_frame.setVisible(False)
        self._update_frame.setFixedHeight(40)
        self._update_frame.setStyleSheet(
            'QFrame#updateBanner {'
            '  background: rgba(76,222,128,0.09);'
            '  border-bottom: 1px solid rgba(76,222,128,0.20);'
            '}'
        )
        lay = QHBoxLayout(self._update_frame)
        lay.setContentsMargins(16, 0, 10, 0)
        lay.setSpacing(10)

        self._update_lbl = QLabel()
        self._update_lbl.setStyleSheet(
            'color: rgba(76,222,128,0.90); font-size: 12px;')

        self._btn_download = QPushButton(tr('upd_download'))
        self._btn_download.setStyleSheet(
            'QPushButton {'
            '  background: rgba(76,222,128,0.15);'
            '  border: 1px solid rgba(76,222,128,0.30);'
            '  border-radius: 4px; color: #4ADE80;'
            '  font-size: 11px; font-weight: 600; padding: 2px 12px;'
            '  min-height: 0; max-height: 24px;'
            '}'
            'QPushButton:hover { background: rgba(76,222,128,0.25); }'
        )
        self._btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_download.clicked.connect(self._open_releases)

        self._btn_upd_dismiss = QPushButton('✕')
        self._btn_upd_dismiss.setFixedSize(22, 22)
        self._btn_upd_dismiss.setStyleSheet(
            'QPushButton { background: transparent; border: none;'
            '  color: rgba(76,222,128,0.55); font-size: 12px; }'
            'QPushButton:hover { color: rgba(76,222,128,0.90); }'
        )
        self._btn_upd_dismiss.clicked.connect(
            lambda: self._update_frame.setVisible(False))

        lay.addWidget(self._update_lbl, 1)
        lay.addWidget(self._btn_download)
        lay.addWidget(self._btn_upd_dismiss)
        return self._update_frame

    def _start_update_check(self):
        """Start background version check 3 s after launch (non-blocking)."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(3000, self._run_update_checker)

    def _run_update_checker(self):
        self._update_checker = UpdateChecker(self)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    def _on_update_available(self, latest: str, url: str):
        self._update_release_url = url
        cur = current_version()
        self._update_lbl.setText(tr('upd_available', current=cur, latest=latest))
        self._btn_download.setText(tr('upd_download'))
        self._update_frame.setVisible(True)

    def _open_releases(self):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        url = getattr(self, '_update_release_url', RELEASES_URL)
        QDesktopServices.openUrl(QUrl(url))

    def _retranslate_update_banner(self):
        self._btn_download.setText(tr('upd_download'))
        # Refresh label text if banner is visible (version strings don't change)

    def _build_duplicates_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName('pageArea')
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        # ── Command bar ──
        cmd_bar = QWidget()
        cmd_bar.setObjectName('cmdBar')
        cmd_bar.setFixedHeight(52)
        cmd_layout = QHBoxLayout(cmd_bar)
        cmd_layout.setContentsMargins(12, 0, 12, 0)
        cmd_layout.setSpacing(6)

        self._btn_scan = QPushButton('▶  Scan')
        self._btn_scan.setObjectName('primaryCmd')
        self._btn_scan.clicked.connect(self._start_scan)

        self._btn_stop = QPushButton('■  Stop')
        self._btn_stop.setObjectName('cmdBtn')
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_scan)

        cmd_sep1 = QFrame()
        cmd_sep1.setFrameShape(QFrame.Shape.VLine)
        cmd_sep1.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._btn_auto = QPushButton('☑  Auto-select Dupes')
        self._btn_auto.setObjectName('cmdBtn')
        self._btn_auto.clicked.connect(self._auto_select)

        self._btn_none = QPushButton('☐  Deselect All')
        self._btn_none.setObjectName('cmdBtn')
        self._btn_none.clicked.connect(self._deselect_all)

        cmd_sep2 = QFrame()
        cmd_sep2.setFrameShape(QFrame.Shape.VLine)
        cmd_sep2.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._btn_delete = QPushButton('\U0001F5D1  Delete Selected')
        self._btn_delete.setObjectName('dangerCmd')
        self._btn_delete.clicked.connect(self._delete_selected)

        cmd_layout.addWidget(self._btn_scan)
        cmd_layout.addWidget(self._btn_stop)
        cmd_layout.addSpacing(4)
        cmd_layout.addWidget(cmd_sep1)
        cmd_layout.addSpacing(4)
        cmd_layout.addWidget(self._btn_auto)
        cmd_layout.addWidget(self._btn_none)
        cmd_layout.addSpacing(4)
        cmd_layout.addWidget(cmd_sep2)
        cmd_layout.addSpacing(4)
        cmd_layout.addWidget(self._btn_delete)
        cmd_layout.addStretch()

        page_layout.addWidget(cmd_bar)

        # ── Splitter ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([240, 1040])

        page_layout.addWidget(splitter)
        return page

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('leftPanel')
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Folder list ──
        self._grp_folders = QGroupBox(tr('grp_locations'))
        fl = QVBoxLayout(self._grp_folders)
        fl.setSpacing(4)
        self._folder_list = FolderDropList()
        self._folder_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._folder_list.setToolTip(tr('folder_list_tip'))
        self._folder_list.setMaximumHeight(100)
        self._folder_list.itemDoubleClicked.connect(lambda _: self._remove_folder())
        fl.addWidget(self._folder_list)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self._btn_add = QPushButton(tr('btn_add'))
        self._btn_add.setFixedHeight(28)
        self._btn_add.clicked.connect(self._add_folder)
        self._btn_rem = QPushButton(tr('btn_remove'))
        self._btn_rem.setFixedHeight(28)
        self._btn_rem.clicked.connect(self._remove_folder)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_rem)
        fl.addLayout(btn_row)
        layout.addWidget(self._grp_folders)

        # ── File type ──
        self._grp_type = QGroupBox(tr('grp_filetype'))
        tl = QVBoxLayout(self._grp_type)
        tl.setSpacing(3)
        self._type_group = QButtonGroup(self)
        self._type_radios: list[QRadioButton] = []
        for i, key in enumerate(self._type_keys):
            rb = QRadioButton(tr(f'ft_{key}'))
            rb.setChecked(key == 'All Files')
            self._type_group.addButton(rb, i)
            self._type_radios.append(rb)
            tl.addWidget(rb)
        layout.addWidget(self._grp_type)

        # ── Detection ──
        self._grp_detection = QGroupBox(tr('grp_detection'))
        dl = QVBoxLayout(self._grp_detection)
        dl.setSpacing(3)
        self._cb_hash = QCheckBox(tr('cb_hash'))
        self._cb_hash.setChecked(True)
        self._cb_hash.setEnabled(False)
        self._cb_prefilter = QCheckBox(tr('cb_prefilter'))
        self._cb_prefilter.setChecked(True)
        self._cb_prefilter.setEnabled(False)
        self._cb_similarity = QCheckBox(tr('cb_similarity'))
        self._cb_similarity.setChecked(True)
        dl.addWidget(self._cb_hash)
        dl.addWidget(self._cb_prefilter)
        dl.addWidget(self._cb_similarity)
        layout.addWidget(self._grp_detection)

        layout.addStretch()
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 14, 10, 8)

        # ── Stats row ──
        row = QHBoxLayout()
        row.setSpacing(10)
        self._chip_groups   = _StatChip(tr('stat_groups_lbl'),   accent='#4CC2FF')
        self._chip_dupes    = _StatChip(tr('stat_dupes_lbl'),    accent='#FFA040')
        self._chip_size     = _StatChip(tr('stat_size_lbl'),     accent='#4DDB8A')
        self._chip_selected = _StatChip(tr('stat_selected_lbl'), accent='#FF6060')
        for chip in (self._chip_groups, self._chip_dupes, self._chip_size, self._chip_selected):
            row.addWidget(chip)
        row.addStretch()
        layout.addLayout(row)

        # ── Session banner ──
        self._session_frame = QFrame()
        self._session_frame.setObjectName('sessionBanner')
        self._session_frame.setVisible(False)
        sb_layout = QHBoxLayout(self._session_frame)
        sb_layout.setContentsMargins(10, 6, 10, 6)
        self._session_lbl = QLabel()
        self._session_lbl.setStyleSheet('color: rgba(255,255,255,0.72); font-size: 12px;')
        self._btn_restore = QPushButton(tr('session_restore'))
        self._btn_restore.setObjectName('cmdBtn')
        self._btn_restore.setStyleSheet(
            'padding: 3px 12px; font-weight: 600; font-size: 12px; '
            'background: rgba(76,194,255,0.15); color: #4CC2FF; '
            'border: 1px solid rgba(76,194,255,0.30); border-radius: 4px;'
        )
        self._btn_restore.clicked.connect(self._restore_session)
        self._btn_dismiss = QPushButton(tr('session_dismiss'))
        self._btn_dismiss.setFixedWidth(26)
        self._btn_dismiss.clicked.connect(self._dismiss_session)
        sb_layout.addWidget(self._session_lbl)
        sb_layout.addStretch()
        sb_layout.addWidget(self._btn_restore)
        sb_layout.addWidget(self._btn_dismiss)
        layout.addWidget(self._session_frame)

        # ── Results card ──
        results_card = QFrame()
        results_card.setObjectName('resultsCard')
        card_layout = QVBoxLayout(results_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels([
            tr('col_name'), tr('col_size'), tr('col_modified'),
            tr('col_match'), tr('col_path'),
        ])
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        self._tree.itemChanged.connect(self._on_check_changed)

        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.resizeSection(0, 240)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setMaximumHeight(4)

        self._hint_lbl = QLabel('Add folders  ·  then click  ▶  Scan')
        self._hint_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._hint_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.15); font-size: 11px;'
            ' padding: 4px 14px 4px 0; letter-spacing: 0.3px;'
        )

        card_layout.addWidget(self._tree)
        card_layout.addWidget(self._hint_lbl)
        card_layout.addWidget(self._progress)
        layout.addWidget(results_card)

        return panel

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_lbl = QLabel(tr('status_ready'))
        sb.addWidget(self._status_lbl)

        credits = QLabel(
            'Made with ♥ by  '
            '<a href="https://github.com/atiilla" style="color:rgba(255,255,255,0.75);'
            ' text-decoration:none; font-weight:600;">atiilla</a>'
            '  &nbsp;·&nbsp;  '
            '<a href="https://intelseclab.com" style="color:#4CC2FF;'
            ' text-decoration:none; font-weight:600;">IntelSecLab</a>'
        )
        credits.setOpenExternalLinks(True)
        credits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 11px;')
        sb.addWidget(credits, 1)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_nav(self, idx: int):
        for i, btn in enumerate(self._nav_items):
            btn.setChecked(i == idx)
        self._stack.setCurrentIndex(idx)
        if self._stack.widget(idx) is self._settings_widget:
            self._settings_widget.refresh_stats()

    def _go_to_settings(self):
        self._on_nav(self._stack.indexOf(self._settings_widget))
        self._settings_widget.trigger_index()

    # ── Language ──────────────────────────────────────────────────────────────

    def _apply_saved_language(self):
        cfg = load_config()
        lang = cfg.get('language', 'en')
        set_language(lang)
        self._lang_combo.blockSignals(True)
        idx = self._lang_combo.findData(lang)
        self._lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._lang_combo.blockSignals(False)
        self._lang_combo.currentIndexChanged.connect(self._change_language)
        self._apply_layout_direction(lang)
        self._retranslate_ui()

    def _change_language(self):
        lang = self._lang_combo.currentData()
        set_language(lang)
        cfg = load_config()
        cfg['language'] = lang
        save_config(cfg)
        self._apply_layout_direction(lang)
        self._retranslate_ui()

    def _apply_layout_direction(self, lang: str):
        """Switch the whole window to RTL for right-to-left languages (e.g. fa)."""
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl(lang)
            else Qt.LayoutDirection.LeftToRight
        )

    def _retranslate_ui(self):
        self.setWindowTitle(tr('app_title'))
        self._retranslate_update_banner()
        self._search_widget.retranslate()
        self._settings_widget.retranslate()
        self._privacy_widget.retranslate()
        self._junk_widget.retranslate()
        self._startup_widget.retranslate()
        self._browser_widget.retranslate()
        self._uninstaller_widget.retranslate()
        self._syscheck_widget.retranslate()
        self._installer_widget.retranslate()
        self._netmon_widget.retranslate()
        self._dpi_widget.retranslate()
        # nav items
        self._nav_dupes.set_nav_text('⦿', tr('tab_duplicates'))
        self._nav_search.set_nav_text('⌕', tr('tab_search'))
        self._nav_privacy.set_nav_text('🛡', tr('tab_privacy'))
        self._nav_junk.set_nav_text('🗑', tr('tab_junk'))
        self._nav_startup.set_nav_text('🚀', tr('tab_startup'))
        self._nav_browser.set_nav_text('🌐', tr('tab_browser'))
        self._nav_uninstaller.set_nav_text('📦', tr('tab_uninstaller'))
        self._nav_syscheck.set_nav_text('🔍', tr('tab_syscheck'))
        self._nav_installer.set_nav_text('⬇', tr('tab_installer'))
        self._nav_netmon.set_nav_text('◉', tr('tab_netmon'))
        self._nav_dpi.set_nav_text('🛰', tr('tab_dpi'))
        self._nav_settings.set_nav_text('⚙', tr('tab_settings'))
        # command buttons
        self._btn_scan.setText(tr('act_scan'))
        self._btn_stop.setText(tr('act_stop'))
        self._btn_auto.setText(tr('act_auto'))
        self._btn_none.setText(tr('act_none'))
        self._btn_delete.setText(tr('act_delete'))
        # folder buttons
        self._btn_add.setText(tr('btn_add'))
        self._btn_rem.setText(tr('btn_remove'))
        # groups
        self._grp_folders.setTitle(tr('grp_locations'))
        self._grp_type.setTitle(tr('grp_filetype'))
        self._grp_detection.setTitle(tr('grp_detection'))
        # lang label
        self._lang_lbl.setText(tr('lang_label') + ':')
        self._folder_list.setToolTip(tr('folder_list_tip'))
        # radio buttons
        for rb, key in zip(self._type_radios, self._type_keys):
            rb.setText(tr(f'ft_{key}'))
        # checkboxes
        self._cb_hash.setText(tr('cb_hash'))
        self._cb_prefilter.setText(tr('cb_prefilter'))
        self._cb_similarity.setText(tr('cb_similarity'))
        # tree columns
        self._tree.setHeaderLabels([
            tr('col_name'), tr('col_size'), tr('col_modified'),
            tr('col_match'), tr('col_path'),
        ])
        # stat chips — labels and values
        self._chip_groups.set_label(tr('stat_groups_lbl'))
        self._chip_dupes.set_label(tr('stat_dupes_lbl'))
        self._chip_size.set_label(tr('stat_size_lbl'))
        self._chip_selected.set_label(tr('stat_selected_lbl'))
        self._update_stat_labels()
        self._refresh_selected_label()
        # session banner
        if self._session_frame.isVisible():
            self._update_session_banner()
        # status bar (only when idle)
        if self._scanner is None or not self._scanner.isRunning():
            if self._sv_groups is None:
                self._status_lbl.setText(tr('status_ready'))
        # tree: group headers + match-type column
        for i in range(self._tree.topLevelItemCount()):
            g = self._tree.topLevelItem(i)
            data = g.data(0, _ROLE_GROUP)
            if data:
                reason = tr(data['reason_key'])
                g.setText(0, tr('group_header',
                                n=data['n'], count=data['count'],
                                size=fmt_size(data['wasted']), reason=reason))
            for j in range(g.childCount()):
                child = g.child(j)
                fd = child.data(0, _ROLE_FILE)
                if fd and 'reason_key' in fd:
                    child.setText(3, tr(fd['reason_key']))

    # ── Session banner ────────────────────────────────────────────────────────

    def _check_session(self):
        session = load_session()
        if not session or not session.get('groups'):
            return
        self._saved_session = session
        self._update_session_banner()
        self._session_frame.setVisible(True)

    def _update_session_banner(self):
        if not self._saved_session:
            return
        s = self._saved_session
        ts = s.get('timestamp', '')[:16].replace('T', ' ')
        raw_folders = s.get('folders', [])
        shown = [os.path.basename(f.rstrip('/\\')) or f for f in raw_folders[:2]]
        folder_str = ', '.join(shown)
        if len(raw_folders) > 2:
            folder_str += f'  +{len(raw_folders)-2}'
        self._session_lbl.setText(
            tr('session_banner', date=ts, folders=folder_str, groups=s.get('group_count', 0)))
        self._btn_restore.setText(tr('session_restore'))
        self._btn_dismiss.setText(tr('session_dismiss'))

    def _restore_session(self):
        session = self._saved_session
        if not session:
            return
        self._session_frame.setVisible(False)
        self._tree.clear()
        self._group_count = 0
        self._sv_groups = None
        self._sv_dupes = None
        self._sv_bytes = None
        self._session_groups = []

        # Restore folder list (add any missing folders)
        existing = {self._folder_list.item(i).text() for i in range(self._folder_list.count())}
        for f in session.get('folders', []):
            if f not in existing:
                self._folder_list.addItem(f)
                existing.add(f)

        # Re-populate tree from saved groups
        for group in session.get('groups', []):
            self._on_group_found(group['files'])

        # Update stats
        self._sv_groups = session.get('group_count', self._group_count)
        self._sv_dupes  = session.get('dupe_count', 0)
        self._sv_bytes  = session.get('bytes_saved', 0)
        self._update_stat_labels()
        self._refresh_selected_label()

        ts = session.get('timestamp', '')[:16].replace('T', ' ')
        self._status_lbl.setText(tr('session_restored', groups=self._sv_groups, date=ts))

    def _dismiss_session(self):
        clear_session()
        self._saved_session = None
        self._session_frame.setVisible(False)

    # ── Folder management ────────────────────────────────────────────────────

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr('act_add'), '')
        if not folder:
            return
        existing = {self._folder_list.item(i).text()
                    for i in range(self._folder_list.count())}
        if folder not in existing:
            self._folder_list.addItem(folder)

    def _remove_folder(self):
        for item in self._folder_list.selectedItems():
            self._folder_list.takeItem(self._folder_list.row(item))

    # ── Scan lifecycle ───────────────────────────────────────────────────────

    def _selected_type(self) -> str:
        idx = self._type_group.checkedId()
        return self._type_keys[idx] if idx >= 0 else 'All Files'

    def _start_scan(self):
        folders = [self._folder_list.item(i).text()
                   for i in range(self._folder_list.count())]
        if not folders:
            QMessageBox.information(self, tr('dlg_no_folders'), tr('dlg_no_folders_msg'))
            return
        self._hint_lbl.setVisible(False)

        file_type = self._selected_type()

        self._tree.clear()
        self._group_count = 0
        self._session_groups = []
        self._last_folders = folders
        self._last_file_type = file_type
        self._session_frame.setVisible(False)
        self._reset_stats()
        self._progress.setValue(0)

        self._scanner = Scanner(
            folders,
            file_type=file_type,
            check_similarity=self._cb_similarity.isChecked(),
            hash_cache=load_hash_cache(),
        )
        self._scanner.progress.connect(self._on_progress)
        self._scanner.group_found.connect(self._on_group_found)
        self._scanner.finished.connect(self._on_finished)
        self._scanner.error_occurred.connect(self._on_error)

        self._btn_scan.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._status_lbl.setText(tr('status_scanning'))
        self._scanner.start()

    def _stop_scan(self):
        if self._scanner:
            self._scanner.stop()
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status_lbl.setText(tr('status_stopped'))

    # ── Scanner signal handlers ──────────────────────────────────────────────

    def _on_progress(self, pct: int, msg: str):
        self._progress.setValue(pct)
        self._status_lbl.setText(msg)

    def _on_group_found(self, files: list):
        self._group_count += 1
        wasted     = files[0]['size'] * (len(files) - 1)
        reason_key = files[0].get('reason_key', 'reason_content')
        reason_display = tr(reason_key)

        self._tree.blockSignals(True)

        header = QTreeWidgetItem(self._tree)
        header.setText(0, tr('group_header',
                              n=self._group_count, count=len(files),
                              size=fmt_size(wasted), reason=reason_display))
        bold = QFont()
        bold.setBold(True)
        header.setFont(0, bold)
        header.setFlags(Qt.ItemFlag.ItemIsEnabled)
        header.setExpanded(True)
        header.setData(0, _ROLE_GROUP, {
            'n': self._group_count,
            'count': len(files),
            'wasted': wasted,
            'reason_key': reason_key,
        })

        for f in files:
            child = QTreeWidgetItem(header)
            child.setText(0, f['name'])
            child.setText(1, fmt_size(f['size']))
            child.setText(2, datetime.fromtimestamp(f['mtime']).strftime('%Y-%m-%d  %H:%M'))
            child.setText(3, reason_display)
            child.setText(4, f['path'])
            child.setData(0, _ROLE_FILE, {
                'path': f['path'],
                'size': f['size'],
                'reason_key': reason_key,
            })
            child.setFlags(
                Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsUserCheckable |
                Qt.ItemFlag.ItemIsSelectable
            )
            keep = f.get('keep', False)
            child.setCheckState(0, Qt.CheckState.Unchecked if keep else Qt.CheckState.Checked)
            self._color_row(child, marked=not keep)

        self._tree.blockSignals(False)
        self._chip_groups.set_value(str(self._group_count))

        # Only accumulate during active scan (not during restore)
        if self._scanner and self._scanner.isRunning():
            self._session_groups.append({'reason_key': reason_key, 'files': list(files)})

    def _on_finished(self, groups: int, dupes: int, saved: int):
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._progress.setValue(100)
        self._sv_groups = groups
        self._sv_dupes  = dupes
        self._sv_bytes  = saved
        self._update_stat_labels()
        self._refresh_selected_label()
        self._status_lbl.setText(tr('status_complete',
                                    groups=groups, dupes=dupes,
                                    size=fmt_size(saved)))
        if groups > 0:
            save_session(
                folders=self._last_folders,
                file_type=self._last_file_type,
                group_count=groups,
                dupe_count=dupes,
                bytes_saved=saved,
                groups=self._session_groups,
            )

    def _on_error(self, msg: str):
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        QMessageBox.critical(self, tr('dlg_scan_err'), msg)

    # ── Checkbox handling ────────────────────────────────────────────────────

    def _on_check_changed(self, item: QTreeWidgetItem, col: int):
        if col != 0 or item.parent() is None:
            return
        self._color_row(item, item.checkState(0) == Qt.CheckState.Checked)
        self._refresh_selected_label()

    def _color_row(self, item: QTreeWidgetItem, marked: bool):
        brush = QBrush(_COLOR_DELETE if marked else _COLOR_KEEP)
        for c in range(self._tree.columnCount()):
            item.setBackground(c, brush)

    def _update_stat_labels(self):
        self._chip_groups.set_value(f'{self._sv_groups:,}' if self._sv_groups is not None else '—')
        self._chip_dupes.set_value(f'{self._sv_dupes:,}' if self._sv_dupes is not None else '—')
        self._chip_size.set_value(fmt_size(self._sv_bytes) if self._sv_bytes is not None else '—')

    def _refresh_selected_label(self):
        count = size = 0
        for i in range(self._tree.topLevelItemCount()):
            g = self._tree.topLevelItem(i)
            for j in range(g.childCount()):
                child = g.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    count += 1
                    fd = child.data(0, _ROLE_FILE)
                    if fd:
                        size += fd['size']
        self._chip_selected.set_value(f'{count:,}  ({fmt_size(size)})' if count else '—')

    # ── Batch selection ──────────────────────────────────────────────────────

    def _auto_select(self):
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            g = self._tree.topLevelItem(i)
            for j in range(g.childCount()):
                child = g.child(j)
                keep = (j == 0)
                child.setCheckState(0, Qt.CheckState.Unchecked if keep else Qt.CheckState.Checked)
                self._color_row(child, not keep)
        self._tree.blockSignals(False)
        self._refresh_selected_label()

    def _deselect_all(self):
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            g = self._tree.topLevelItem(i)
            for j in range(g.childCount()):
                child = g.child(j)
                child.setCheckState(0, Qt.CheckState.Unchecked)
                self._color_row(child, False)
        self._tree.blockSignals(False)
        self._refresh_selected_label()

    # ── Context menu ─────────────────────────────────────────────────────────

    def _context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None or item.parent() is None:
            return
        menu = QMenu(self)
        act_view   = menu.addAction(tr('ctx_view'))
        act_open   = menu.addAction(tr('ctx_open'))
        menu.addSeparator()
        act_keep   = menu.addAction(tr('ctx_keep'))
        act_mark   = menu.addAction(tr('ctx_mark'))
        act_unmark = menu.addAction(tr('ctx_unmark'))
        choice = menu.exec(QCursor.pos())

        fd = item.data(0, _ROLE_FILE)
        if choice == act_view:
            if fd:
                open_path(fd['path'])
        elif choice == act_open:
            if fd:
                open_folder(fd['path'])
        elif choice == act_keep:
            self._set_keep_only(item)
        elif choice in (act_mark, act_unmark):
            checked = choice == act_mark
            self._tree.blockSignals(True)
            item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._color_row(item, checked)
            self._tree.blockSignals(False)
            self._refresh_selected_label()

    def _set_keep_only(self, target: QTreeWidgetItem):
        group = target.parent()
        self._tree.blockSignals(True)
        for j in range(group.childCount()):
            child = group.child(j)
            keep = child is target
            child.setCheckState(0, Qt.CheckState.Unchecked if keep else Qt.CheckState.Checked)
            self._color_row(child, not keep)
        self._tree.blockSignals(False)
        self._refresh_selected_label()

    # ── Delete ───────────────────────────────────────────────────────────────

    def _delete_selected(self):
        to_delete: list[tuple[QTreeWidgetItem, str, int]] = []
        for i in range(self._tree.topLevelItemCount()):
            g = self._tree.topLevelItem(i)
            for j in range(g.childCount()):
                child = g.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    fd = child.data(0, _ROLE_FILE)
                    if fd:
                        to_delete.append((child, fd['path'], fd['size']))

        if not to_delete:
            QMessageBox.information(self, tr('dlg_nothing'), tr('dlg_nothing_msg'))
            return

        total_size = sum(s for _, _, s in to_delete)
        reply = QMessageBox.question(
            self, tr('dlg_confirm'),
            tr('dlg_confirm_msg', n=len(to_delete), size=fmt_size(total_size)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            import send2trash  # noqa: F401
        except ImportError:
            QMessageBox.critical(self, tr('dlg_no_pkg'), tr('dlg_no_pkg_msg'))
            return

        self._delete_items = {path: child for child, path, _ in to_delete}
        self._deleter = Deleter([path for _, path, _ in to_delete], self)
        self._deleter.progress.connect(self._on_delete_progress)
        self._deleter.finished.connect(self._on_delete_finished)

        self._progress.setValue(0)
        self._btn_delete.setEnabled(False)
        self._btn_scan.setEnabled(False)
        self._deleter.start()

    def _on_delete_progress(self, pct: int, msg: str):
        self._progress.setValue(pct)
        self._status_lbl.setText(msg)

    def _on_delete_finished(self, deleted_paths: list, errors: list):
        self._btn_delete.setEnabled(True)
        if self._scanner is None or not self._scanner.isRunning():
            self._btn_scan.setEnabled(True)

        self._tree.blockSignals(True)
        groups_to_remove: list[int] = []
        for path in deleted_paths:
            child = self._delete_items.get(path)
            if child is None:
                continue
            group = child.parent()
            if group is None:
                continue
            group.removeChild(child)
            if group.childCount() <= 1:
                idx = self._tree.indexOfTopLevelItem(group)
                if idx >= 0 and idx not in groups_to_remove:
                    groups_to_remove.append(idx)
        for idx in sorted(groups_to_remove, reverse=True):
            self._tree.takeTopLevelItem(idx)
        self._tree.blockSignals(False)
        self._delete_items = {}

        self._sv_groups = self._tree.topLevelItemCount()
        self._update_stat_labels()
        self._refresh_selected_label()

        if errors:
            msg = tr('status_done', n=len(deleted_paths))
            msg += f'\n\n{tr("dlg_errors")}\n' + '\n'.join(errors[:10])
            QMessageBox.warning(self, tr('dlg_errors'), msg)
        else:
            self._status_lbl.setText(tr('status_done', n=len(deleted_paths)))

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _reset_stats(self):
        self._sv_groups = None
        self._sv_dupes  = None
        self._sv_bytes  = None
        self._update_stat_labels()
        self._chip_selected.set_value('—')
