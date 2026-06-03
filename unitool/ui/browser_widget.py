import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QProgressBar,
    QComboBox, QMessageBox, QTextEdit, QApplication, QMenu,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QTextCursor

from ..browser import (
    BrowserDataItem, BrowserProfile,
    detect_browsers, get_profiles, scan_browser_data,
    is_browser_running, clean_browser_item, get_browser_icon,
    get_browser_item_preview, fmt_size,
)
from ..translations import tr


# ── Colours ───────────────────────────────────────────────────────────────────

_COLOR_FOUND   = QColor(255, 140,  60)
_COLOR_CLEANED = QColor( 77, 219, 138)
_COLOR_DANGER  = QColor(255,  80,  80)
_COLOR_MISSING = QColor( 80,  80,  80)


# ── Worker threads ────────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    """Scans all detected browsers in a background thread."""
    done = pyqtSignal(list)   # list[BrowserDataItem]

    def run(self):
        self.done.emit(scan_browser_data())


class _PreviewWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, item: BrowserDataItem, parent=None):
        super().__init__(parent)
        self._item = item

    def run(self):
        self.done.emit(get_browser_item_preview(self._item))


# ── Main widget ───────────────────────────────────────────────────────────────

class BrowserWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # All items fetched so far, keyed by (browser, profile_name)
        self._all_items: dict[tuple[str, str], list[BrowserDataItem]] = {}
        # Items currently displayed in the table
        self._shown_items: list[BrowserDataItem] = []
        self._scan_worker: _ScanWorker | None = None
        self._preview_worker: _PreviewWorker | None = None
        self._detected_browsers: list[str] = []
        self._setup_ui()
        self._populate_browser_list()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_cmd_bar())
        layout.addWidget(self._build_warning_banner())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([220, 1000])
        layout.addWidget(splitter, 1)

    # ── Command bar ───────────────────────────────────────────────────────────

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._btn_scan = QPushButton(tr('bsr_scan_btn'))
        self._btn_scan.setObjectName('primaryCmd')
        self._btn_scan.clicked.connect(self._scan_all)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._btn_clean = QPushButton(tr('bsr_clean_btn'))
        self._btn_clean.setObjectName('dangerCmd')
        self._btn_clean.setEnabled(False)
        self._btn_clean.clicked.connect(self._clean_selected)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedWidth(100)
        self._progress.setFixedHeight(6)
        self._progress.setVisible(False)

        lay.addWidget(self._btn_scan)
        lay.addSpacing(4)
        lay.addWidget(sep)
        lay.addSpacing(4)
        lay.addWidget(self._btn_clean)
        lay.addStretch()
        lay.addWidget(self._progress)
        return bar

    # ── Warning banner ────────────────────────────────────────────────────────

    def _build_warning_banner(self) -> QFrame:
        self._warning_banner = QFrame()
        self._warning_banner.setStyleSheet(
            'QFrame {'
            '  background: rgba(255,160,0,0.12);'
            '  border-bottom: 1px solid rgba(255,160,0,0.35);'
            '  border-radius: 0px;'
            '}'
        )
        self._warning_banner.setFixedHeight(36)
        lay = QHBoxLayout(self._warning_banner)
        lay.setContentsMargins(14, 0, 14, 0)

        self._warning_lbl = QLabel()
        self._warning_lbl.setStyleSheet(
            'color: rgba(255,200,80,0.92); font-size: 12px; background: transparent; border: none;'
        )
        lay.addWidget(self._warning_lbl)
        lay.addStretch()

        self._warning_banner.setVisible(False)
        return self._warning_banner

    # ── Left panel (browser list) ─────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('leftPanel')
        panel.setFixedWidth(220)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 14, 8, 8)
        lay.setSpacing(4)

        hdr = QLabel('BROWSERS')
        hdr.setStyleSheet(
            'color: rgba(255,255,255,0.32); font-size: 9px; font-weight: 700; '
            'letter-spacing: 0.8px; padding-left: 8px;'
        )
        lay.addWidget(hdr)
        lay.addSpacing(4)

        self._browser_list = QListWidget()
        self._browser_list.setSpacing(2)
        self._browser_list.currentItemChanged.connect(self._on_browser_changed)
        lay.addWidget(self._browser_list, 1)
        return panel

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(8, 10, 8, 8)
        outer.setSpacing(6)

        # Profile row
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        self._profile_lbl = QLabel(tr('bsr_profile_lbl'))
        self._profile_lbl.setStyleSheet('color: rgba(255,255,255,0.55); font-size: 12px;')
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(180)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self._profile_lbl)
        profile_row.addWidget(self._profile_combo)
        profile_row.addStretch()
        outer.addLayout(profile_row)

        # Status label
        self._status_lbl = QLabel(tr('bsr_status_idle'))
        self._status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.40); font-size: 12px; padding: 2px 0;'
        )
        outer.addWidget(self._status_lbl)

        # Vertical splitter: table + preview
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self._build_table())
        vsplit.addWidget(self._build_preview_panel())
        vsplit.setSizes([340, 180])
        outer.addWidget(vsplit, 1)

        return panel

    # ── Table ─────────────────────────────────────────────────────────────────

    def _build_table(self) -> QWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            '',
            tr('bsr_col_type'),
            tr('bsr_col_size'),
            tr('bsr_col_status'),
            tr('bsr_col_path'),
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemChanged.connect(self._on_check_changed)
        self._table.currentCellChanged.connect(self._on_row_selected)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 28)
        self._table.setColumnWidth(1, 150)
        return self._table

    # ── Preview panel ─────────────────────────────────────────────────────────

    def _build_preview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet(
            'QFrame { background: rgba(0,0,0,0.18); '
            'border-top: 1px solid rgba(255,255,255,0.07); }'
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hdr = QWidget()
        hdr.setFixedHeight(26)
        hdr.setStyleSheet('background: rgba(255,255,255,0.04);')
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(10, 0, 10, 0)

        self._preview_title = QLabel(tr('bsr_preview_title'))
        self._preview_title.setStyleSheet(
            'color: rgba(255,255,255,0.35); font-size: 9px; '
            'font-weight: 700; letter-spacing: 0.8px;'
        )

        self._preview_loading = QLabel(tr('bsr_preview_loading'))
        self._preview_loading.setStyleSheet(
            'color: rgba(255,255,255,0.28); font-size: 10px;'
        )
        self._preview_loading.setVisible(False)

        hdr_lay.addWidget(self._preview_title)
        hdr_lay.addStretch()
        hdr_lay.addWidget(self._preview_loading)

        self._preview_text = QTextEdit()
        self._preview_text.setReadOnly(True)
        self._preview_text.setStyleSheet(
            'QTextEdit {'
            '  background: transparent; border: none;'
            '  font-family: "Consolas", "Cascadia Code", "JetBrains Mono", monospace;'
            '  font-size: 11px; color: rgba(255,255,255,0.75);'
            '  padding: 4px 8px;'
            '}'
        )
        self._preview_text.setPlaceholderText(tr('bsr_no_preview'))

        lay.addWidget(hdr)
        lay.addWidget(self._preview_text, 1)
        return panel

    # ── Browser list population ───────────────────────────────────────────────

    def _populate_browser_list(self):
        self._browser_list.blockSignals(True)
        self._browser_list.clear()
        self._detected_browsers = detect_browsers()

        if not self._detected_browsers:
            item = QListWidgetItem(tr('bsr_no_browsers'))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(QColor(100, 100, 100))
            self._browser_list.addItem(item)
        else:
            for bname in self._detected_browsers:
                icon = get_browser_icon(bname)
                running = is_browser_running(bname)
                text = f'{icon}  {bname}'
                li = QListWidgetItem(text)
                li.setData(Qt.ItemDataRole.UserRole, bname)
                if running:
                    li.setToolTip(tr('bsr_running_warn', browser=bname))
                self._browser_list.addItem(li)

                if running:
                    badge = QListWidgetItem('    ● Running')
                    badge.setForeground(QColor(255, 160, 0))
                    badge.setFlags(Qt.ItemFlag.NoItemFlags)
                    f = badge.font()
                    f.setPointSize(9)
                    badge.setFont(f)
                    self._browser_list.addItem(badge)

        self._browser_list.blockSignals(False)
        # Select first actual browser
        for i in range(self._browser_list.count()):
            li = self._browser_list.item(i)
            if li and li.data(Qt.ItemDataRole.UserRole):
                self._browser_list.setCurrentItem(li)
                break

    # ── Browser selection ─────────────────────────────────────────────────────

    def _on_browser_changed(self, current: QListWidgetItem | None, _prev):
        if current is None:
            return
        bname = current.data(Qt.ItemDataRole.UserRole)
        if not bname:
            return
        self._load_profiles_for_browser(bname)

    def _load_profiles_for_browser(self, browser: str):
        """Populate the profile combo for the selected browser."""
        profiles = get_profiles(browser)
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        for p in profiles:
            self._profile_combo.addItem(p.profile_name, userData=p)
        self._profile_combo.blockSignals(False)

        # Update running warning banner
        running = is_browser_running(browser)
        if running:
            self._warning_lbl.setText(
                f'⚠  {tr("bsr_running_warn", browser=browser)}'
            )
            self._warning_banner.setVisible(True)
        else:
            self._warning_banner.setVisible(False)

        # Load data for the first profile
        if profiles:
            self._load_profile_data(profiles[0])
        else:
            self._show_items([])
            self._status_lbl.setText(tr('bsr_status_idle'))

    def _on_profile_changed(self, index: int):
        if index < 0:
            return
        profile: BrowserProfile | None = self._profile_combo.itemData(index)
        if profile is None:
            return
        self._load_profile_data(profile)

    def _load_profile_data(self, profile: BrowserProfile):
        """Load (or retrieve from cache) data items for the given profile."""
        key = (profile.browser, profile.profile_name)
        if key in self._all_items:
            self._show_items(self._all_items[key])
            self._update_status_from_items(self._all_items[key])
            return

        # Quick synchronous per-profile scan (individual profiles are fast)
        self._status_lbl.setText(tr('bsr_status_scanning'))
        try:
            from ..browser import _chromium_items, _firefox_items
            if profile.browser == 'Firefox':
                items = _firefox_items(profile)
            else:
                items = _chromium_items(profile)
        except Exception:
            items = []

        self._all_items[key] = items
        self._show_items(items)
        self._update_status_from_items(items)

    def _update_status_from_items(self, items: list[BrowserDataItem]):
        if not items:
            self._status_lbl.setText(tr('bsr_status_not_found'))
            return
        total_size = sum(i.size for i in items)
        self._status_lbl.setText(
            tr('bsr_status_found', n=len(items), size=fmt_size(total_size))
        )

    # ── Full background scan ──────────────────────────────────────────────────

    def _scan_all(self):
        self._btn_scan.setEnabled(False)
        self._progress.setVisible(True)
        self._status_lbl.setText(tr('bsr_status_scanning'))
        self._all_items.clear()

        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.done.disconnect()
            self._scan_worker.quit()
            self._scan_worker.wait(300)

        self._scan_worker = _ScanWorker(self)
        self._scan_worker.done.connect(self._on_scan_done)
        self._scan_worker.start()

    def _on_scan_done(self, items: list):
        self._btn_scan.setEnabled(True)
        self._progress.setVisible(False)

        # Group items by (browser, profile)
        for item in items:
            key = (item.browser, item.profile)
            self._all_items.setdefault(key, []).append(item)

        # Refresh the browser list (running states may have changed)
        self._populate_browser_list()

        total = len(items)
        total_size = sum(i.size for i in items)
        self._status_lbl.setText(
            tr('bsr_status_found', n=total, size=fmt_size(total_size))
        )

    # ── Table population ──────────────────────────────────────────────────────

    def _show_items(self, items: list[BrowserDataItem]):
        self._shown_items = items

        vp = self._table.viewport()
        vp.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(items))

        for r, item in enumerate(items):
            is_password = (item.data_type == 'passwords')

            # Checkbox column
            cb = QTableWidgetItem()
            if item.deletable and not is_password:
                cb.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                )
                cb.setCheckState(Qt.CheckState.Unchecked)
            else:
                cb.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if is_password and item.deletable:
                    cb.setToolTip(tr('bsr_password_warn_msg'))
            self._table.setItem(r, 0, cb)

            # Type column
            type_lbl = item.data_type.replace('_', ' ').title()
            type_item = QTableWidgetItem(type_lbl)
            type_item.setToolTip(item.description)
            type_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if is_password:
                type_item.setForeground(_COLOR_DANGER)
            self._table.setItem(r, 1, type_item)

            # Size column
            sz = QTableWidgetItem(fmt_size(item.size) if item.size else '—')
            sz.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if is_password:
                sz.setForeground(_COLOR_DANGER)
            self._table.setItem(r, 2, sz)

            # Status column
            if not item.exists:
                status, color = tr('bsr_status_not_found'), _COLOR_MISSING
            else:
                status, color = tr('bsr_status_found_item'), _COLOR_FOUND
            st = QTableWidgetItem(status)
            st.setForeground(color)
            st.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 3, st)

            # Path column
            path_item = QTableWidgetItem(item.path)
            path_item.setToolTip(item.path)
            path_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if is_password:
                path_item.setForeground(_COLOR_DANGER)
            self._table.setItem(r, 4, path_item)

        self._table.setSortingEnabled(True)
        self._table.blockSignals(False)
        vp.setUpdatesEnabled(True)
        vp.update()

        self._refresh_clean_btn()
        self._preview_text.clear()
        self._preview_text.setPlaceholderText(tr('bsr_no_preview'))

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_row_selected(self, row: int, _col, _prev_row, _prev_col):
        if row < 0 or row >= len(self._shown_items):
            return
        item = self._shown_items[row]

        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.done.disconnect()
            self._preview_worker.quit()
            self._preview_worker.wait(100)

        self._preview_loading.setVisible(True)
        self._preview_text.setPlaceholderText('')

        self._preview_worker = _PreviewWorker(item, self)
        self._preview_worker.done.connect(self._on_preview_ready)
        self._preview_worker.start()

    def _on_preview_ready(self, text: str):
        self._preview_loading.setVisible(False)
        self._preview_text.setPlainText(text)
        self._preview_text.moveCursor(QTextCursor.MoveOperation.Start)

    # ── Context menu ──────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._shown_items):
            return
        item = self._shown_items[row]

        menu = QMenu(self)

        act_preview = menu.addAction(tr('bsr_ctx_preview'))
        act_copy    = menu.addAction(tr('bsr_ctx_copy'))

        is_accessible = os.path.exists(item.path)
        act_open = menu.addAction(tr('bsr_ctx_open'))
        if not is_accessible:
            act_open.setEnabled(False)

        menu.addSeparator()
        act_clean = menu.addAction(tr('bsr_ctx_clean'))
        act_clean.setEnabled(item.deletable and item.data_type != 'passwords')

        chosen = menu.exec(QCursor.pos())
        if chosen == act_preview:
            self._table.setCurrentCell(row, 1)
        elif chosen == act_copy:
            QApplication.clipboard().setText(item.path)
        elif chosen == act_open:
            from ..platform_utils import open_folder
            open_folder(item.path)
        elif chosen == act_clean:
            self._clean_single(row, item)

    # ── Single-item clean ─────────────────────────────────────────────────────

    def _clean_single(self, row: int, item: BrowserDataItem):
        if not item.safe_to_delete:
            reply = QMessageBox.warning(
                self,
                tr('bsr_password_warn_title'),
                tr('bsr_password_warn_msg'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        reply = QMessageBox.question(
            self,
            tr('bsr_confirm_title'),
            tr('bsr_confirm_msg', n=1, size=fmt_size(item.size)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, err = clean_browser_item(item)
        self._table.blockSignals(True)
        if ok:
            if st := self._table.item(row, 3):
                st.setText(tr('bsr_status_cleaned', n=1))
                st.setForeground(_COLOR_CLEANED)
            if cb := self._table.item(row, 0):
                cb.setCheckState(Qt.CheckState.Unchecked)
                cb.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if sz := self._table.item(row, 2):
                sz.setText('—')
        else:
            QMessageBox.warning(self, 'Error', err)
        self._table.blockSignals(False)

        # Invalidate cache for this profile
        self._invalidate_cache_for_item(item)
        self._refresh_clean_btn()

    # ── Checkbox / clean-button state ─────────────────────────────────────────

    def _on_check_changed(self, item: QTableWidgetItem):
        if item.column() == 0:
            self._refresh_clean_btn()

    def _refresh_clean_btn(self):
        checked = sum(
            1 for r in range(self._table.rowCount())
            if (cb := self._table.item(r, 0)) is not None
            and bool(cb.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            and cb.checkState() == Qt.CheckState.Checked
        )
        self._btn_clean.setEnabled(checked > 0)

    # ── Clean selected ────────────────────────────────────────────────────────

    def _clean_selected(self):
        to_clean: list[tuple[int, BrowserDataItem]] = [
            (r, self._shown_items[r])
            for r in range(self._table.rowCount())
            if (cb := self._table.item(r, 0)) is not None
            and bool(cb.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            and cb.checkState() == Qt.CheckState.Checked
            and r < len(self._shown_items)
        ]
        if not to_clean:
            return

        # Check if any password items are selected (should not happen since
        # password rows have checkbox disabled, but guard defensively)
        has_passwords = any(i.data_type == 'passwords' for _, i in to_clean)
        if has_passwords:
            QMessageBox.warning(
                self,
                tr('bsr_password_warn_title'),
                tr('bsr_password_warn_msg'),
            )
            return

        total_size = sum(i.size for _, i in to_clean)
        reply = QMessageBox.question(
            self,
            tr('bsr_confirm_title'),
            tr('bsr_confirm_msg', n=len(to_clean), size=fmt_size(total_size)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        cleaned, errors = 0, []
        self._table.blockSignals(True)

        for r, item in to_clean:
            ok, err = clean_browser_item(item)
            if ok:
                cleaned += 1
                if st := self._table.item(r, 3):
                    st.setText(tr('bsr_status_cleaned', n=1))
                    st.setForeground(_COLOR_CLEANED)
                if cb := self._table.item(r, 0):
                    cb.setCheckState(Qt.CheckState.Unchecked)
                    cb.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if sz := self._table.item(r, 2):
                    sz.setText('—')
                self._invalidate_cache_for_item(item)
            else:
                errors.append(f'{item.data_type}: {err}')

        self._table.blockSignals(False)
        self._btn_clean.setEnabled(False)
        self._status_lbl.setText(tr('bsr_status_cleaned', n=cleaned))

        if errors:
            QMessageBox.warning(
                self,
                'Done (with errors)',
                tr('bsr_status_cleaned', n=cleaned) + '\n\n' + '\n'.join(errors[:8]),
            )

    # ── Cache invalidation ────────────────────────────────────────────────────

    def _invalidate_cache_for_item(self, item: BrowserDataItem):
        key = (item.browser, item.profile)
        self._all_items.pop(key, None)

    # ── Retranslation ─────────────────────────────────────────────────────────

    def retranslate(self):
        self._btn_scan.setText(tr('bsr_scan_btn'))
        self._btn_clean.setText(tr('bsr_clean_btn'))
        self._profile_lbl.setText(tr('bsr_profile_lbl'))
        self._status_lbl.setText(tr('bsr_status_idle'))
        self._preview_title.setText(tr('bsr_preview_title'))
        self._preview_loading.setText(tr('bsr_preview_loading'))
        self._preview_text.setPlaceholderText(tr('bsr_no_preview'))
        self._table.setHorizontalHeaderLabels([
            '',
            tr('bsr_col_type'),
            tr('bsr_col_size'),
            tr('bsr_col_status'),
            tr('bsr_col_path'),
        ])

        # Re-check running warning for currently selected browser
        cur = self._browser_list.currentItem()
        if cur:
            bname = cur.data(Qt.ItemDataRole.UserRole)
            if bname and is_browser_running(bname):
                self._warning_lbl.setText(
                    f'⚠  {tr("bsr_running_warn", browser=bname)}'
                )
