"""
UniTool/ui/installer_widget.py
Ninite-style App Installer tab — winget backend.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QProgressBar,
    QTextEdit, QLineEdit, QTabWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont

from ..installer import (
    AppEntry, InstalledEntry, CATEGORIES, get_catalog,
    is_winget_available, refresh_status, install_app,
    get_all_installed_apps, winget_search,
)
from ..translations import tr

# ── Status / dim palette ────────────────────────────────────────────────────────

_FG_INSTALLED = QColor(77,  219, 138)
_FG_UPDATE    = QColor(255, 160,  64)
_FG_NONE      = QColor(100, 100, 100)
_FG_DIM       = QColor(88,   88,  88)
_BG_INSTALLED = QColor(16,   46,  26)
_BG_UPDATE    = QColor(60,   40,   8)
_BG_DEFAULT   = QColor(28,   28,  28)
_BG_DIM       = QColor(20,   20,  20)

# ── Widget-level stylesheets ────────────────────────────────────────────────────

_TAB_STYLE = (
    'QTabWidget::pane { border: none; background: #1c1c1c; }'
    'QTabBar { background: #161616; }'
    'QTabBar::tab {'
    '  background: #161616; color: rgba(255,255,255,0.40);'
    '  font-size: 11px; font-weight: 600;'
    '  padding: 8px 22px; border: none;'
    '  border-bottom: 2px solid transparent;'
    '  min-width: 100px;'
    '}'
    'QTabBar::tab:selected {'
    '  color: #4CC2FF; background: #1c1c1c;'
    '  border-bottom: 2px solid #4CC2FF;'
    '}'
    'QTabBar::tab:hover:!selected {'
    '  color: rgba(255,255,255,0.65); background: #1a1a1a;'
    '}'
)

_CAT_STYLE = (
    'QListWidget {'
    '  background: transparent; border: none; outline: none;'
    '}'
    'QListWidget::item {'
    '  color: rgba(255,255,255,0.52); padding: 7px 10px;'
    '  border-radius: 4px; font-size: 12px;'
    '}'
    'QListWidget::item:selected {'
    '  background: rgba(76,194,255,0.13); color: rgba(255,255,255,0.92);'
    '}'
    'QListWidget::item:hover:!selected {'
    '  background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.72);'
    '}'
)


# ── Worker: status check ───────────────────────────────────────────────────────

class _CheckWorker(QThread):
    done = pyqtSignal(list)   # list[InstalledEntry]

    def __init__(self, apps: list[AppEntry], parent=None):
        super().__init__(parent)
        self._apps = apps

    def run(self):
        refresh_status(self._apps)
        self.done.emit(get_all_installed_apps())


# ── Worker: winget search ──────────────────────────────────────────────────────

class _SearchWorker(QThread):
    done = pyqtSignal(list)   # list[AppEntry]  (synthetic, from search results)

    def __init__(self, query: str, installed_ids: set[str], parent=None):
        super().__init__(parent)
        self._query         = query
        self._installed_ids = installed_ids

    def run(self):
        raw = winget_search(self._query)
        results: list[AppEntry] = []
        for pkg_id, name, version, match, source in raw:
            # publisher field carries the winget ID so it's visible in col 2
            # description carries version (+ match tag when present)
            desc = version
            if match:
                desc = f'{version}  ·  {match}' if version else match
            app = AppEntry(
                winget_id=pkg_id,
                name=name,
                publisher=pkg_id,
                category='search',
                description=desc,
            )
            if pkg_id.lower() in self._installed_ids:
                app.installed = True
                app.installed_version = version
            results.append(app)
        self.done.emit(results)


# ── Worker: installation ───────────────────────────────────────────────────────

class _InstallWorker(QThread):
    progress = pyqtSignal(str, int, str)    # status_msg, pct, raw_line
    app_done = pyqtSignal(str, bool, str)   # name, ok, error_msg
    all_done = pyqtSignal(int, int)         # ok_count, total

    def __init__(self, queue: list[tuple[AppEntry, str]], parent=None):
        super().__init__(parent)
        self._queue  = queue
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ok_count = 0
        for app, action in self._queue:
            if self._cancel:
                break
            self.progress.emit(tr('inst_installing').format(name=app.name), -1, '')
            success, err = install_app(app, action=action, progress_cb=self._cb)
            if success:
                ok_count += 1
            self.app_done.emit(app.name, success, err)
        self.all_done.emit(ok_count, len(self._queue))

    def _cb(self, status: str, pct: int, raw: str):
        self.progress.emit(status, pct, raw)


# ── Main widget ────────────────────────────────────────────────────────────────

class InstallerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._apps: list[AppEntry]                  = get_catalog()
        self._all_installed: list[InstalledEntry]   = []
        self._selected_cat: str | None              = None
        self._check_worker:   _CheckWorker   | None = None
        self._install_worker: _InstallWorker | None = None
        self._search_worker:  _SearchWorker  | None = None
        self._winget_ok: bool | None                = None
        self._first_show_done: bool                 = False
        # search-mode state
        self._in_search_mode: bool                  = False
        self._search_results: list[AppEntry]        = []
        # widgets set during construction — start as None so guards work
        self._search_edit:     QLineEdit     | None = None
        self._inst_search:     QLineEdit     | None = None
        self._catalog_table:   QTableWidget  | None = None
        self._installed_table: QTableWidget  | None = None
        self._tabs:            QTabWidget    | None = None
        self._search_banner:   QFrame        | None = None
        self._setup_ui()

    # ── Auto-check on first view ───────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._first_show_done:
            self._first_show_done = True
            QTimer.singleShot(400, self._check_status)

    # ── UI construction ────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_cmd_bar())

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self._build_tabs())
        vsplit.addWidget(self._build_log_panel())
        vsplit.setSizes([520, 140])
        layout.addWidget(vsplit, 1)

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(6)

        self._btn_check = QPushButton(tr('inst_check_btn'))
        self._btn_check.setObjectName('primaryCmd')
        self._btn_check.clicked.connect(self._check_status)

        self._btn_install = QPushButton(tr('inst_install_btn'))
        self._btn_install.setObjectName('cmdBtn')
        self._btn_install.setEnabled(False)
        self._btn_install.clicked.connect(self._install_selected)

        self._btn_update_all = QPushButton(tr('inst_update_all_btn'))
        self._btn_update_all.setObjectName('cmdBtn')
        self._btn_update_all.setEnabled(False)
        self._btn_update_all.clicked.connect(self._update_all)

        self._btn_cancel = QPushButton(tr('inst_cancel_btn'))
        self._btn_cancel.setObjectName('dangerCmd')
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel_install)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedWidth(130)
        self._progress.setVisible(False)

        self._status_lbl = QLabel(tr('inst_status_idle'))
        self._status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.45); font-size: 11px;'
            ' background: rgba(255,255,255,0.04);'
            ' border-radius: 3px; padding: 2px 10px;'
        )

        for w in [self._btn_check, None, self._btn_install, None,
                  self._btn_update_all, None, self._btn_cancel]:
            if w is None:
                lay.addWidget(self._make_sep())
            else:
                lay.addWidget(w)
                lay.addSpacing(2)
        lay.addStretch()
        lay.addWidget(self._progress)
        lay.addSpacing(10)
        lay.addWidget(self._status_lbl)
        return bar

    @staticmethod
    def _make_sep() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')
        return f

    # ── Tab widget ─────────────────────────────────────────────────────────────

    def _build_tabs(self) -> QTabWidget:
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_TAB_STYLE)
        self._tabs.addTab(self._build_catalog_tab(), tr('inst_tab_catalog'))
        self._tabs.addTab(self._build_installed_tab(), tr('inst_tab_installed'))
        self._tabs.currentChanged.connect(self._on_tab_changed)
        return self._tabs

    # ── Catalog tab ────────────────────────────────────────────────────────────

    def _build_catalog_tab(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.setChildrenCollapsible(False)
        hsplit.addWidget(self._build_category_panel())

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 10, 12, 8)
        rl.setSpacing(8)
        rl.addWidget(self._build_search_bar())
        rl.addWidget(self._build_search_banner())
        rl.addWidget(self._build_catalog_table(), 1)

        hsplit.addWidget(right)
        hsplit.setSizes([210, 1000])
        outer.addWidget(hsplit, 1)
        return container

    def _build_search_bar(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr('inst_search_placeholder'))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._search_edit.returnPressed.connect(self._trigger_winget_search)

        self._btn_select_all = QPushButton(tr('inst_select_all'))
        self._btn_select_all.setObjectName('cmdBtn')
        self._btn_select_all.clicked.connect(lambda: self._set_catalog_checks(True))

        self._btn_deselect_all = QPushButton(tr('inst_deselect_all'))
        self._btn_deselect_all.setObjectName('cmdBtn')
        self._btn_deselect_all.clicked.connect(lambda: self._set_catalog_checks(False))

        lay.addWidget(self._search_edit, 1)
        lay.addWidget(self._btn_select_all)
        lay.addWidget(self._btn_deselect_all)
        return row

    def _build_search_banner(self) -> QFrame:
        """Orange info banner shown when displaying winget search results."""
        self._search_banner = QFrame()
        self._search_banner.setObjectName('searchBanner')
        self._search_banner.setStyleSheet(
            'QFrame#searchBanner {'
            '  background: rgba(255,160,64,0.10);'
            '  border: 1px solid rgba(255,160,64,0.28);'
            '  border-radius: 4px;'
            '  min-height: 34px; max-height: 34px;'
            '}'
        )
        self._search_banner.setVisible(False)

        lay = QHBoxLayout(self._search_banner)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(10)

        self._banner_lbl = QLabel()
        self._banner_lbl.setStyleSheet(
            'color: rgba(255,190,100,0.90); font-size: 12px;'
            ' background: transparent; border: none; padding: 0;'
        )
        lay.addWidget(self._banner_lbl, 1)

        btn_back = QPushButton(tr('inst_back_catalog'))
        # Inline stylesheet — avoids inheriting global min-height from app theme
        btn_back.setStyleSheet(
            'QPushButton {'
            '  background: rgba(255,160,64,0.18);'
            '  border: 1px solid rgba(255,160,64,0.38);'
            '  border-radius: 3px;'
            '  color: rgba(255,200,120,0.92);'
            '  font-size: 11px;'
            '  padding: 3px 10px;'
            '  min-height: 0; max-height: 24px;'
            '}'
            'QPushButton:hover  { background: rgba(255,160,64,0.28); }'
            'QPushButton:pressed{ background: rgba(255,160,64,0.40); }'
        )
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.clicked.connect(self._clear_search_mode)
        self._btn_back_catalog = btn_back
        lay.addWidget(btn_back)
        return self._search_banner

    def _build_category_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('leftPanel')
        panel.setFixedWidth(210)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 16, 10, 10)
        lay.setSpacing(6)

        hdr = QLabel('CATEGORIES')
        hdr.setStyleSheet(
            'color: rgba(255,255,255,0.28); font-size: 9px; font-weight: 700;'
            ' letter-spacing: 1.2px; padding-left: 10px;'
        )
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._cat_list = QListWidget()
        self._cat_list.setStyleSheet(_CAT_STYLE)
        self._cat_list.setSpacing(1)
        self._cat_list.setFrameShape(QFrame.Shape.NoFrame)

        all_item = QListWidgetItem(f'◉  {tr("inst_all_apps")}')
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self._cat_list.addItem(all_item)

        self._cat_items: dict[str, QListWidgetItem] = {}
        for cat_id, icon, tr_key in CATEGORIES:
            item = QListWidgetItem(f'{icon}  {tr(tr_key)}')
            item.setData(Qt.ItemDataRole.UserRole, cat_id)
            self._cat_list.addItem(item)
            self._cat_items[cat_id] = item

        self._cat_list.setCurrentRow(0)
        self._cat_list.currentItemChanged.connect(self._on_cat_changed)
        lay.addWidget(self._cat_list, 1)
        return panel

    def _build_catalog_table(self) -> QTableWidget:
        self._catalog_table = QTableWidget()
        self._catalog_table.setColumnCount(5)
        self._catalog_table.setHorizontalHeaderLabels([
            '', tr('inst_col_name'), tr('inst_col_publisher'),
            tr('inst_col_desc'), tr('inst_col_status'),
        ])
        self._catalog_table.setAlternatingRowColors(False)
        self._catalog_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._catalog_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._catalog_table.setShowGrid(False)
        self._catalog_table.verticalHeader().setVisible(False)
        self._catalog_table.setSortingEnabled(True)
        self._catalog_table.itemChanged.connect(self._on_catalog_check_changed)

        hdr = self._catalog_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._catalog_table.setColumnWidth(0, 32)

        self._populate_catalog_table()
        return self._catalog_table

    # ── Installed tab ──────────────────────────────────────────────────────────

    def _build_installed_tab(self) -> QWidget:
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(12, 10, 12, 8)
        lay.setSpacing(8)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        self._inst_search = QLineEdit()
        self._inst_search.setPlaceholderText(tr('inst_search_placeholder'))
        self._inst_search.setClearButtonEnabled(True)
        self._inst_search.textChanged.connect(self._apply_installed_filter)

        self._btn_inst_sel = QPushButton(tr('inst_select_all'))
        self._btn_inst_sel.setObjectName('cmdBtn')
        self._btn_inst_sel.clicked.connect(lambda: self._set_installed_checks(True))

        self._btn_inst_desel = QPushButton(tr('inst_deselect_all'))
        self._btn_inst_desel.setObjectName('cmdBtn')
        self._btn_inst_desel.clicked.connect(lambda: self._set_installed_checks(False))

        rl.addWidget(self._inst_search, 1)
        rl.addWidget(self._btn_inst_sel)
        rl.addWidget(self._btn_inst_desel)
        lay.addWidget(row)

        self._installed_table = QTableWidget()
        self._installed_table.setColumnCount(6)
        self._installed_table.setHorizontalHeaderLabels([
            '', tr('inst_col_name'), tr('inst_col_id'),
            tr('inst_col_version'), tr('inst_col_available'), tr('inst_col_source'),
        ])
        self._installed_table.setAlternatingRowColors(False)
        self._installed_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._installed_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._installed_table.setShowGrid(False)
        self._installed_table.verticalHeader().setVisible(False)
        self._installed_table.setSortingEnabled(True)
        self._installed_table.itemChanged.connect(self._on_installed_check_changed)

        hdr = self._installed_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._installed_table.setColumnWidth(0, 32)

        lay.addWidget(self._installed_table, 1)
        return container

    # ── Catalog population ─────────────────────────────────────────────────────

    def _populate_catalog_table(self, source: list[AppEntry] | None = None):
        """Populate the catalog table. Uses `source` when given (search mode),
        otherwise falls back to self._apps (catalog mode)."""
        if self._catalog_table is None:
            return
        apps = source if source is not None else self._apps

        self._catalog_table.setSortingEnabled(False)
        self._catalog_table.blockSignals(True)
        self._catalog_table.setRowCount(len(apps))

        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(9)

        q = self._search_edit.text().strip().lower() if self._search_edit else ''

        for row, app in enumerate(apps):
            bg = _BG_UPDATE if app.has_update else (_BG_INSTALLED if app.installed else _BG_DEFAULT)
            self._catalog_table.setRowHeight(row, 36)

            # col 0: checkbox — UserRole stores AppEntry for sort-safe lookup
            chk = QTableWidgetItem()
            chk.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, app)
            chk.setBackground(bg)
            self._catalog_table.setItem(row, 0, chk)

            name_item = QTableWidgetItem(app.name)
            name_item.setFont(bold)
            name_item.setBackground(bg)
            name_item.setToolTip(app.winget_id)
            self._catalog_table.setItem(row, 1, name_item)

            pub = QTableWidgetItem(app.publisher)
            pub.setForeground(QColor(150, 150, 150))
            pub.setBackground(bg)
            self._catalog_table.setItem(row, 2, pub)

            desc = QTableWidgetItem(app.description)
            desc.setForeground(QColor(120, 120, 120))
            desc.setBackground(bg)
            self._catalog_table.setItem(row, 3, desc)

            st = self._catalog_status_item(app)
            st.setBackground(bg)
            self._catalog_table.setItem(row, 4, st)

            # Visibility. Always set it explicitly: setRowCount() does NOT reset
            # the hidden flag of rows that still exist, so a prior catalog filter
            # would otherwise leave search-result rows stuck hidden.
            if self._in_search_mode:
                self._catalog_table.setRowHidden(row, False)
            else:
                self._catalog_table.setRowHidden(
                    row, self._is_catalog_hidden(app, q)
                )

        self._catalog_table.blockSignals(False)
        self._catalog_table.setSortingEnabled(True)
        self._update_install_btn()

    def _catalog_status_item(self, app: AppEntry) -> QTableWidgetItem:
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(9)
        if app.has_update:
            text = tr('inst_update_lbl').format(
                cur=app.installed_version or '?', new=app.available_version)
            item = QTableWidgetItem(text)
            item.setForeground(_FG_UPDATE)
            item.setFont(bold)
        elif app.installed:
            ver  = app.installed_version
            text = tr('inst_version_lbl').format(ver=ver) if ver else tr('inst_status_installed')
            item = QTableWidgetItem(text)
            item.setForeground(_FG_INSTALLED)
            item.setFont(bold)
        else:
            item = QTableWidgetItem('—')
            item.setForeground(_FG_NONE)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _is_catalog_hidden(self, app: AppEntry, q: str) -> bool:
        """Row visibility logic for catalog mode (no-op in search mode)."""
        if q:
            return not (q in app.name.lower() or q in app.publisher.lower()
                        or q in app.description.lower() or q in app.winget_id.lower())
        if self._selected_cat and app.category != self._selected_cat:
            return True
        return False

    # ── Installed population ───────────────────────────────────────────────────

    def _populate_installed_table(self, entries: list[InstalledEntry]):
        if self._installed_table is None:
            return
        self._installed_table.setSortingEnabled(False)
        self._installed_table.blockSignals(True)
        self._installed_table.setRowCount(len(entries))

        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(9)

        q = self._inst_search.text().strip().lower() if self._inst_search else ''

        for row, entry in enumerate(entries):
            updatable = entry.has_update
            bg = _BG_UPDATE if updatable else _BG_DIM
            self._installed_table.setRowHeight(row, 36)

            # col 0: checkbox — only enabled when an update is available
            chk = QTableWidgetItem()
            if updatable:
                chk.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
            else:
                chk.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, entry)
            chk.setBackground(bg)
            self._installed_table.setItem(row, 0, chk)

            name_item = QTableWidgetItem(entry.name)
            name_item.setFont(bold)
            name_item.setForeground(QColor(210, 210, 210) if updatable else _FG_DIM)
            name_item.setBackground(bg)
            self._installed_table.setItem(row, 1, name_item)

            id_item = QTableWidgetItem(entry.winget_id)
            id_item.setForeground(QColor(120, 120, 120) if updatable else _FG_DIM)
            id_item.setBackground(bg)
            self._installed_table.setItem(row, 2, id_item)

            ver_item = QTableWidgetItem(entry.version)
            ver_item.setForeground(QColor(150, 150, 150) if updatable else _FG_DIM)
            ver_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            ver_item.setBackground(bg)
            self._installed_table.setItem(row, 3, ver_item)

            if updatable:
                avail = QTableWidgetItem(entry.available_version)
                avail.setForeground(_FG_UPDATE)
                avail.setFont(bold)
            else:
                avail = QTableWidgetItem('—')
                avail.setForeground(_FG_DIM)
            avail.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            avail.setBackground(bg)
            self._installed_table.setItem(row, 4, avail)

            src = QTableWidgetItem(entry.source)
            src.setForeground(_FG_DIM)
            src.setBackground(bg)
            self._installed_table.setItem(row, 5, src)

            self._installed_table.setRowHidden(row, self._is_installed_hidden(entry, q))

        self._installed_table.blockSignals(False)
        self._installed_table.setSortingEnabled(True)

        if self._tabs:
            self._tabs.setTabText(1, f'{tr("inst_tab_installed")} ({len(entries)})')

    @staticmethod
    def _is_installed_hidden(entry: InstalledEntry, q: str) -> bool:
        if not q:
            return False
        return not (q in entry.name.lower() or q in entry.winget_id.lower()
                    or q in entry.version.lower() or q in entry.source.lower())

    # ── Filtering ──────────────────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str):
        """Live filter for catalog mode; clears search mode when field is emptied."""
        if self._in_search_mode:
            if not text.strip():
                self._clear_search_mode()
            return
        self._apply_catalog_filter(text)

    def _apply_catalog_filter(self, text: str = ''):
        if self._catalog_table is None or self._in_search_mode:
            return
        q = text.strip().lower()
        visible = 0
        for row in range(self._catalog_table.rowCount()):
            item0 = self._catalog_table.item(row, 0)
            if not item0:
                continue
            app = item0.data(Qt.ItemDataRole.UserRole)
            if app:
                hidden = self._is_catalog_hidden(app, q)
                self._catalog_table.setRowHidden(row, hidden)
                if not hidden:
                    visible += 1
        # No catalog match for a typed query → hint that Enter searches winget
        if q and visible == 0:
            self._status_lbl.setText(tr('inst_press_enter').format(query=text.strip()))
        self._update_install_btn()

    def _apply_installed_filter(self, text: str = ''):
        if self._installed_table is None:
            return
        q = text.strip().lower()
        for row in range(self._installed_table.rowCount()):
            item0 = self._installed_table.item(row, 0)
            if not item0:
                continue
            entry = item0.data(Qt.ItemDataRole.UserRole)
            if entry:
                self._installed_table.setRowHidden(row, self._is_installed_hidden(entry, q))

    # ── Winget search (Enter key) ──────────────────────────────────────────────

    def _trigger_winget_search(self):
        if not self._search_edit:
            return
        query = self._search_edit.text().strip()
        if not query or len(query) < 2:
            return
        if self._search_worker and self._search_worker.isRunning():
            return
        if self._winget_ok is False:
            self._status_lbl.setText(tr('inst_status_no_winget'))
            return
        if self._winget_ok is None:
            self._winget_ok = is_winget_available()
        if not self._winget_ok:
            self._status_lbl.setText(tr('inst_status_no_winget'))
            return

        self._status_lbl.setText(tr('inst_searching').format(query=query))
        self._btn_check.setEnabled(False)

        installed_ids = {e.winget_id.lower() for e in self._all_installed}
        self._search_worker = _SearchWorker(query, installed_ids, self)
        self._search_worker.done.connect(lambda results: self._on_search_done(query, results))
        self._search_worker.start()

    def _on_search_done(self, query: str, results: list[AppEntry]):
        self._btn_check.setEnabled(True)
        self._in_search_mode = True
        self._search_results  = results

        if results:
            self._banner_lbl.setText(tr('inst_search_results').format(query=query))
            self._status_lbl.setText(
                tr('inst_search_results').format(query=query)
                + f'  —  {len(results)} result(s)'
            )
        else:
            self._banner_lbl.setText(tr('inst_no_results').format(query=query))
            self._status_lbl.setText(tr('inst_no_results').format(query=query))

        if self._search_banner:
            self._search_banner.setVisible(True)

        self._cat_list.setEnabled(False)
        if self._catalog_table:
            self._catalog_table.setHorizontalHeaderLabels([
                '', tr('inst_col_name'), tr('inst_col_id'),
                tr('inst_col_version'), tr('inst_col_status'),
            ])
        self._populate_catalog_table(self._search_results)

    def _clear_search_mode(self):
        self._in_search_mode = False
        self._search_results = []
        if self._search_banner:
            self._search_banner.setVisible(False)
        if self._search_edit:
            self._search_edit.blockSignals(True)
            self._search_edit.clear()
            self._search_edit.blockSignals(False)
        self._cat_list.setEnabled(True)
        if self._catalog_table:
            self._catalog_table.setHorizontalHeaderLabels([
                '', tr('inst_col_name'), tr('inst_col_publisher'),
                tr('inst_col_desc'), tr('inst_col_status'),
            ])
        self._populate_catalog_table()
        self._status_lbl.setText(
            tr('inst_status_ready').format(
                installed=sum(1 for a in self._apps if a.installed),
                updates=sum(1 for a in self._apps if a.has_update),
            ) if any(a.installed for a in self._apps) else tr('inst_status_idle')
        )

    # ── Category counts ────────────────────────────────────────────────────────

    def _update_category_counts(self):
        counts: dict[str, int] = {}
        for app in self._apps:
            if app.installed or app.has_update:
                counts[app.category] = counts.get(app.category, 0) + 1
        for i in range(1, self._cat_list.count()):
            item = self._cat_list.item(i)
            cat_id = item.data(Qt.ItemDataRole.UserRole)
            _, icon, tr_key = next(
                (c for c in CATEGORIES if c[0] == cat_id), (cat_id, '', cat_id)
            )
            n = counts.get(cat_id, 0)
            item.setText(f'{icon}  {tr(tr_key)}' + (f'  ({n})' if n else ''))

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_cat_changed(self, current: QListWidgetItem, _prev):
        if not current or self._in_search_mode:
            return
        self._selected_cat = current.data(Qt.ItemDataRole.UserRole)
        q = self._search_edit.text() if self._search_edit else ''
        self._apply_catalog_filter(q)

    def _on_tab_changed(self, _idx: int):
        self._update_install_btn()
        self._sync_install_btn_label()

    def _sync_install_btn_label(self):
        """Relabel the install/update button depending on which tab is visible."""
        if self._tabs and self._tabs.currentIndex() == 1:
            self._btn_install.setText(tr('inst_update_selected_btn'))
        else:
            self._btn_install.setText(tr('inst_install_btn'))

    def _on_catalog_check_changed(self, item: QTableWidgetItem):
        if item.column() == 0:
            self._update_install_btn()

    def _on_installed_check_changed(self, item: QTableWidgetItem):
        if item.column() == 0:
            self._update_install_btn()

    def _set_catalog_checks(self, state: bool):
        if self._catalog_table is None:
            return
        cs = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        self._catalog_table.blockSignals(True)
        for row in range(self._catalog_table.rowCount()):
            if not self._catalog_table.isRowHidden(row):
                item = self._catalog_table.item(row, 0)
                if item:
                    item.setCheckState(cs)
        self._catalog_table.blockSignals(False)
        self._update_install_btn()

    def _set_installed_checks(self, state: bool):
        if self._installed_table is None:
            return
        cs = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        self._installed_table.blockSignals(True)
        for row in range(self._installed_table.rowCount()):
            if not self._installed_table.isRowHidden(row):
                item = self._installed_table.item(row, 0)
                # Only toggle rows that are actually checkable (i.e. have an update)
                if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    item.setCheckState(cs)
        self._installed_table.blockSignals(False)
        self._update_install_btn()

    def _update_install_btn(self):
        if self._tabs is None or self._catalog_table is None or self._installed_table is None:
            return
        table = (self._catalog_table if self._tabs.currentIndex() == 0
                 else self._installed_table)
        checked = any(
            table.item(r, 0) and table.item(r, 0).checkState() == Qt.CheckState.Checked
            for r in range(table.rowCount())
        )
        if not (self._install_worker and self._install_worker.isRunning()):
            self._btn_install.setEnabled(checked)

    # ── Check status ───────────────────────────────────────────────────────────

    def _check_status(self, clear_log: bool = True):
        if (self._check_worker   and self._check_worker.isRunning()) or \
           (self._install_worker and self._install_worker.isRunning()):
            return

        if self._winget_ok is None:
            self._winget_ok = is_winget_available()
        if not self._winget_ok:
            self._status_lbl.setText(tr('inst_status_no_winget'))
            return

        self._set_busy(True, checking=True)
        self._status_lbl.setText(tr('inst_status_checking'))
        if clear_log:
            self._log.clear()

        self._check_worker = _CheckWorker(self._apps, self)
        self._check_worker.done.connect(self._on_check_done)
        self._check_worker.start()

    def _on_check_done(self, all_installed: list):
        self._all_installed = all_installed
        self._set_busy(False)

        n_installed = sum(1 for a in self._apps if a.installed)
        n_updates   = sum(1 for a in self._apps if a.has_update)
        self._status_lbl.setText(
            tr('inst_status_ready').format(installed=n_installed, updates=n_updates)
        )
        self._btn_update_all.setEnabled(n_updates > 0)
        self._update_category_counts()
        if self._in_search_mode:
            # Keep the search view; just refresh each result's installed flag
            installed_ids = {e.winget_id.lower() for e in all_installed}
            for app in self._search_results:
                app.installed = app.winget_id.lower() in installed_ids
            self._populate_catalog_table(self._search_results)
        else:
            self._populate_catalog_table()
        self._populate_installed_table(all_installed)

    # ── Install / update ───────────────────────────────────────────────────────

    def _install_selected(self):
        if self._install_worker and self._install_worker.isRunning():
            return

        queue: list[tuple[AppEntry, str]] = []

        if self._tabs and self._tabs.currentIndex() == 0:
            for row in range(self._catalog_table.rowCount()):
                chk = self._catalog_table.item(row, 0)
                if chk and chk.checkState() == Qt.CheckState.Checked:
                    app = chk.data(Qt.ItemDataRole.UserRole)
                    if app:
                        queue.append((app, 'upgrade' if app.has_update else 'install'))
        else:
            for row in range(self._installed_table.rowCount()):
                chk = self._installed_table.item(row, 0)
                if chk and chk.checkState() == Qt.CheckState.Checked:
                    entry = chk.data(Qt.ItemDataRole.UserRole)
                    if entry and entry.has_update:
                        proxy = AppEntry(
                            winget_id=entry.winget_id,
                            name=entry.name,
                            publisher='', category='', description='',
                        )
                        queue.append((proxy, 'upgrade'))

        if not queue:
            self._status_lbl.setText(tr('inst_none_selected'))
            return
        self._start_install_worker(queue)

    def _update_all(self):
        if self._install_worker and self._install_worker.isRunning():
            return
        queue = [(a, 'upgrade') for a in self._apps if a.has_update]
        if queue:
            self._start_install_worker(queue)

    def _start_install_worker(self, queue: list[tuple[AppEntry, str]]):
        self._set_busy(True, checking=False)
        self._log.clear()
        self._log.append(f'--- {tr("inst_log_title")} ---\n')

        self._install_worker = _InstallWorker(queue, self)
        self._install_worker.progress.connect(self._on_install_progress)
        self._install_worker.app_done.connect(self._on_app_done)
        self._install_worker.all_done.connect(self._on_all_done)
        self._install_worker.start()

    def _cancel_install(self):
        if self._install_worker and self._install_worker.isRunning():
            self._install_worker.cancel()

    def _on_install_progress(self, status: str, pct: int, _raw: str):
        self._status_lbl.setText(status)
        if pct >= 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(pct)
        else:
            self._progress.setRange(0, 0)

    def _on_app_done(self, name: str, ok: bool, err: str):
        self._log.append(f'{"✓" if ok else "✗"}  {name}' + (f'  —  {err}' if not ok else ''))

    def _on_all_done(self, ok: int, total: int):
        self._set_busy(False)
        msg = tr('inst_done').format(ok=ok, total=total)
        self._status_lbl.setText(msg)
        self._log.append(f'\n{msg}')
        # Defer the refresh: the install QThread is still finishing when this
        # slot fires, so an immediate _check_status() can hit the isRunning()
        # guard and silently skip. The delay also lets us preserve the log.
        QTimer.singleShot(600, lambda: self._check_status(clear_log=False))

    # ── Log panel ──────────────────────────────────────────────────────────────

    def _build_log_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName('instLogPanel')
        panel.setStyleSheet(
            'QFrame#instLogPanel {'
            '  background: #181818;'
            '  border-top: 1px solid rgba(255,255,255,0.08);'
            '}'
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        hdr = QLabel(tr('inst_log_title'))
        hdr.setStyleSheet(
            'color: rgba(255,255,255,0.28); font-size: 9px; font-weight: 700;'
            ' letter-spacing: 1.2px;'
        )
        lay.addWidget(hdr)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            'QTextEdit {'
            '  background: transparent; border: none;'
            '  color: rgba(255,255,255,0.68); font-size: 12px;'
            '  font-family: Consolas, "Courier New", monospace;'
            '}'
        )
        lay.addWidget(self._log, 1)
        return panel

    # ── Busy state ─────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool, checking: bool = False):
        self._btn_check.setEnabled(not busy)
        self._btn_install.setEnabled(not busy)
        if busy:
            self._btn_update_all.setEnabled(False)
        self._btn_cancel.setVisible(busy and not checking)
        self._progress.setVisible(busy)
        if busy:
            self._progress.setRange(0, 0)

    # ── Retranslation ──────────────────────────────────────────────────────────

    def retranslate(self):
        self._btn_check.setText(tr('inst_check_btn'))
        self._sync_install_btn_label()
        self._btn_update_all.setText(tr('inst_update_all_btn'))
        self._btn_cancel.setText(tr('inst_cancel_btn'))
        self._btn_select_all.setText(tr('inst_select_all'))
        self._btn_deselect_all.setText(tr('inst_deselect_all'))
        self._btn_inst_sel.setText(tr('inst_select_all'))
        self._btn_inst_desel.setText(tr('inst_deselect_all'))
        self._btn_back_catalog.setText(tr('inst_back_catalog'))

        if self._search_edit:
            self._search_edit.setPlaceholderText(tr('inst_search_placeholder'))
        if self._inst_search:
            self._inst_search.setPlaceholderText(tr('inst_search_placeholder'))

        if self._tabs:
            self._tabs.setTabText(0, tr('inst_tab_catalog'))
            n = self._installed_table.rowCount() if self._installed_table else 0
            self._tabs.setTabText(1, f'{tr("inst_tab_installed")} ({n})')

        if self._cat_list.count() > 0:
            self._cat_list.item(0).setText(f'◉  {tr("inst_all_apps")}')
        for i in range(1, self._cat_list.count()):
            item = self._cat_list.item(i)
            cat_id = item.data(Qt.ItemDataRole.UserRole)
            _, icon, tr_key = next(
                (c for c in CATEGORIES if c[0] == cat_id), (cat_id, '', cat_id)
            )
            item.setText(f'{icon}  {tr(tr_key)}')

        if self._catalog_table:
            self._catalog_table.setHorizontalHeaderLabels([
                '', tr('inst_col_name'), tr('inst_col_publisher'),
                tr('inst_col_desc'), tr('inst_col_status'),
            ])
        if self._installed_table:
            self._installed_table.setHorizontalHeaderLabels([
                '', tr('inst_col_name'), tr('inst_col_id'),
                tr('inst_col_version'), tr('inst_col_available'), tr('inst_col_source'),
            ])
