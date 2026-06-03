import os
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QCheckBox, QFrame, QMessageBox, QMenu, QApplication,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QCursor

from ..uninstaller import (
    InstalledApp,
    get_installed_apps,
    uninstall_app,
    find_leftovers,
    clean_leftovers,
    fmt_size,
)
from ..translations import tr
from .uninstall_wizard import UninstallWizard


# ── Numeric-aware sort item ───────────────────────────────────────────────────

class _SortItem(QTableWidgetItem):
    def __init__(self, display: str, sort_key):
        super().__init__(display)
        self._sort_key = sort_key

    def __lt__(self, other):
        if isinstance(other, _SortItem):
            try:
                return self._sort_key < other._sort_key
            except TypeError:
                pass
        return super().__lt__(other)


# ── Worker: load apps in background ──────────────────────────────────────────

class _LoadWorker(QThread):
    done = pyqtSignal(list)   # list[InstalledApp]

    def run(self):
        self.done.emit(get_installed_apps())


# ── Worker: find leftovers in background ─────────────────────────────────────

class _LeftoversWorker(QThread):
    done = pyqtSignal(list)   # list[tuple[str, int]]

    def __init__(self, app: InstalledApp, parent=None):
        super().__init__(parent)
        self._app = app

    def run(self):
        self.done.emit(find_leftovers(self._app))


# ── Main widget ───────────────────────────────────────────────────────────────

class UninstallerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._all_apps: list[InstalledApp] = []
        self._filtered_apps: list[InstalledApp] = []
        self._current_app: InstalledApp | None = None

        self._load_worker: _LoadWorker | None = None
        self._leftovers_worker: _LeftoversWorker | None = None

        # Debounce timer for search
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_filter)

        # Refresh-offer timer (shown after uninstall launch)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self._offer_refresh)

        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_cmd_bar())
        layout.addWidget(self._build_main_table(), 1)
        layout.addWidget(self._build_bottom_bar())
        layout.addWidget(self._build_leftovers_panel())

    # ── Command bar ───────────────────────────────────────────────────────────

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr('unst_search_placeholder'))
        self._search_edit.setFixedWidth(280)
        self._search_edit.textChanged.connect(self._on_search_changed)

        sep = self._make_vsep()

        self._show_system_cb = QCheckBox(tr('unst_show_system'))
        self._show_system_cb.setChecked(False)
        self._show_system_cb.toggled.connect(self._apply_filter)

        self._status_lbl = QLabel('')
        self._status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.45); font-size: 12px;'
        )

        self._refresh_btn = QPushButton(tr('unst_refresh'))
        self._refresh_btn.setObjectName('cmdBtn')
        self._refresh_btn.clicked.connect(self._start_load)

        lay.addWidget(self._search_edit)
        lay.addWidget(sep)
        lay.addWidget(self._show_system_cb)
        lay.addStretch()
        lay.addWidget(self._status_lbl)
        lay.addSpacing(8)
        lay.addWidget(self._refresh_btn)
        return bar

    # ── Main table ────────────────────────────────────────────────────────────

    def _build_main_table(self) -> QTableWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            tr('unst_col_name'),
            tr('unst_col_publisher'),
            tr('unst_col_version'),
            tr('unst_col_size'),
            tr('unst_col_date'),
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 300)
        self._table.setColumnWidth(1, 180)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 100)

        # Default sort: size descending (col 3)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(3, Qt.SortOrder.DescendingOrder)

        return self._table

    # ── Bottom bar ────────────────────────────────────────────────────────────

    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(52)
        bar.setStyleSheet(
            'QFrame {'
            '  background: rgba(255,255,255,0.02);'
            '  border-top: 1px solid rgba(255,255,255,0.07);'
            '}'
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._uninstall_btn = QPushButton(tr('unst_uninstall_btn'))
        self._uninstall_btn.setObjectName('dangerCmd')
        self._uninstall_btn.setEnabled(False)
        self._uninstall_btn.clicked.connect(self._do_uninstall)

        sep = self._make_vsep()

        self._advanced_btn = QPushButton(tr('unst_advanced_btn'))
        self._advanced_btn.setObjectName('cmdBtn')
        self._advanced_btn.setEnabled(False)
        self._advanced_btn.clicked.connect(self._toggle_leftovers_panel)

        self._selected_lbl = QLabel('')
        self._selected_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.40); font-size: 12px; padding-left: 8px;'
        )

        lay.addWidget(self._uninstall_btn)
        lay.addWidget(sep)
        lay.addWidget(self._advanced_btn)
        lay.addWidget(self._selected_lbl)
        lay.addStretch()
        return bar

    # ── Leftovers panel ───────────────────────────────────────────────────────

    def _build_leftovers_panel(self) -> QFrame:
        panel = QFrame()
        panel.setVisible(False)
        panel.setStyleSheet(
            'QFrame {'
            '  background: rgba(255,255,255,0.02);'
            '  border-top: 1px solid rgba(255,80,80,0.20);'
            '}'
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        # Header row
        hdr_lay = QHBoxLayout()
        self._leftovers_title_lbl = QLabel(tr('unst_leftovers_title'))
        self._leftovers_title_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.32); font-size: 9px; font-weight: 700; '
            'letter-spacing: 0.8px;'
        )
        self._leftovers_close_btn = QPushButton(tr('unst_leftovers_close'))
        self._leftovers_close_btn.setObjectName('cmdBtn')
        self._leftovers_close_btn.setFixedHeight(24)
        self._leftovers_close_btn.clicked.connect(self._hide_leftovers_panel)

        hdr_lay.addWidget(self._leftovers_title_lbl)
        hdr_lay.addStretch()
        hdr_lay.addWidget(self._leftovers_close_btn)
        lay.addLayout(hdr_lay)

        # Leftovers table (fixed height ~ 6 rows)
        self._leftovers_table = QTableWidget()
        self._leftovers_table.setColumnCount(3)
        self._leftovers_table.setHorizontalHeaderLabels([
            '', tr('unst_leftovers_col_path'), tr('unst_leftovers_col_size'),
        ])
        self._leftovers_table.setAlternatingRowColors(True)
        self._leftovers_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._leftovers_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._leftovers_table.setShowGrid(False)
        self._leftovers_table.verticalHeader().setVisible(False)
        self._leftovers_table.setFixedHeight(160)

        lft_hdr = self._leftovers_table.horizontalHeader()
        lft_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._leftovers_table.setColumnWidth(0, 28)
        lft_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lft_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self._leftovers_table)

        # Status label inside panel
        self._leftovers_status_lbl = QLabel('')
        self._leftovers_status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.38); font-size: 11px;'
        )
        lay.addWidget(self._leftovers_status_lbl)

        # Clean button row
        clean_row = QHBoxLayout()
        self._clean_leftovers_btn = QPushButton(tr('unst_leftovers_clean'))
        self._clean_leftovers_btn.setObjectName('dangerCmd')
        self._clean_leftovers_btn.setEnabled(False)
        self._clean_leftovers_btn.clicked.connect(self._do_clean_leftovers)
        clean_row.addWidget(self._clean_leftovers_btn)
        clean_row.addStretch()
        lay.addLayout(clean_row)

        self._leftovers_panel = panel
        return panel

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_vsep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(
            'background: rgba(255,255,255,0.10); max-width: 1px; border: none;'
        )
        return sep

    # ── Loading ───────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        # Auto-load on first show
        if not self._all_apps and not (self._load_worker and self._load_worker.isRunning()):
            self._start_load()

    def _start_load(self):
        self._table.setRowCount(0)
        self._status_lbl.setText(tr('unst_loading'))
        self._refresh_btn.setEnabled(False)
        self._uninstall_btn.setEnabled(False)
        self._advanced_btn.setEnabled(False)
        self._current_app = None

        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.done.disconnect()
            self._load_worker.quit()
            self._load_worker.wait(500)

        self._load_worker = _LoadWorker(self)
        self._load_worker.done.connect(self._on_load_done)
        self._load_worker.start()

    def _on_load_done(self, apps: list):
        self._all_apps = apps
        self._refresh_btn.setEnabled(True)
        self._apply_filter()

    # ── Filter / populate ─────────────────────────────────────────────────────

    def _on_search_changed(self, _text: str):
        self._search_timer.start()

    def _apply_filter(self):
        query = self._search_edit.text().strip().lower()
        show_system = self._show_system_cb.isChecked()

        filtered: list[InstalledApp] = []
        for app in self._all_apps:
            if not show_system and app.is_system:
                continue
            if query:
                haystack = (app.name + ' ' + app.publisher).lower()
                if query not in haystack:
                    continue
            filtered.append(app)

        self._filtered_apps = filtered
        self._populate_table(filtered)

        n = len(filtered)
        self._status_lbl.setText(tr('unst_status', n=n))

    def _populate_table(self, apps: list[InstalledApp]):
        vp = self._table.viewport()
        vp.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(apps))

        for r, app in enumerate(apps):
            # Name
            name_item = QTableWidgetItem(app.name)
            name_item.setData(Qt.ItemDataRole.UserRole, r)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 0, name_item)

            # Publisher
            pub_item = QTableWidgetItem(app.publisher)
            pub_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 1, pub_item)

            # Version
            ver_item = QTableWidgetItem(app.version)
            ver_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 2, ver_item)

            # Size — numeric sort
            size_bytes = app.size_kb * 1024
            size_display = fmt_size(size_bytes) if app.size_kb else '—'
            size_item = _SortItem(size_display, app.size_kb)
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            size_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 3, size_item)

            # Date
            date_item = QTableWidgetItem(app.install_date or '—')
            date_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 4, date_item)

        self._table.setSortingEnabled(True)
        self._table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self._table.blockSignals(False)
        vp.setUpdatesEnabled(True)
        vp.update()

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._current_app = None
            self._uninstall_btn.setEnabled(False)
            self._advanced_btn.setEnabled(False)
            self._selected_lbl.setText('')
            return

        row = rows[0].row()
        name_item = self._table.item(row, 0)
        if name_item is None:
            return

        # Map back to filtered_apps by original index stored in UserRole,
        # but since we repopulate sorted, we match by name instead.
        app_name = name_item.text()
        found = next((a for a in self._filtered_apps if a.name == app_name), None)
        if found is None:
            # Fallback: use position in filtered list (pre-sort index may be stale)
            idx = name_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(idx, int) and 0 <= idx < len(self._filtered_apps):
                found = self._filtered_apps[idx]

        self._current_app = found
        enabled = found is not None
        self._uninstall_btn.setEnabled(enabled)
        self._advanced_btn.setEnabled(enabled)
        if found:
            self._selected_lbl.setText(f'1  {tr("unst_status", n=1).split()[0]}  {found.name[:40]}')
        else:
            self._selected_lbl.setText('')

    # ── Uninstall ─────────────────────────────────────────────────────────────

    def _do_uninstall(self):
        app = self._current_app
        if app is None:
            return

        wizard = UninstallWizard(app, self)
        wizard.exec()

        if wizard.uninstall_succeeded:
            # Suggest a refresh after the external uninstaller has had time to run
            self._refresh_timer.start()
            # If the user opted to find leftovers, open the panel immediately
            if wizard.find_leftovers_checked:
                self._show_leftovers_panel()

    def _offer_refresh(self):
        self._status_lbl.setText(
            f'"{self._current_app.name if self._current_app else "App"}"'
            ' uninstaller launched — click Refresh when done.'
        )
        self._refresh_btn.setEnabled(True)

    # ── Leftovers panel visibility ────────────────────────────────────────────

    def _toggle_leftovers_panel(self):
        if self._leftovers_panel.isVisible():
            self._hide_leftovers_panel()
        else:
            self._show_leftovers_panel()

    def _show_leftovers_panel(self):
        app = self._current_app
        if app is None:
            return
        self._leftovers_panel.setVisible(True)
        self._leftovers_table.setRowCount(0)
        self._leftovers_status_lbl.setText(tr('unst_leftovers_loading'))
        self._clean_leftovers_btn.setEnabled(False)
        self._start_leftovers_scan(app)

    def _hide_leftovers_panel(self):
        self._leftovers_panel.setVisible(False)
        if self._leftovers_worker and self._leftovers_worker.isRunning():
            self._leftovers_worker.done.disconnect()
            self._leftovers_worker.quit()
            self._leftovers_worker.wait(300)

    # ── Leftover scanning ─────────────────────────────────────────────────────

    def _start_leftovers_scan(self, app: InstalledApp):
        if self._leftovers_worker and self._leftovers_worker.isRunning():
            self._leftovers_worker.done.disconnect()
            self._leftovers_worker.quit()
            self._leftovers_worker.wait(500)

        self._leftovers_worker = _LeftoversWorker(app, self)
        self._leftovers_worker.done.connect(self._on_leftovers_done)
        self._leftovers_worker.start()

    def _on_leftovers_done(self, results: list):
        """Populate leftovers table. results is list[tuple[str, int]]."""
        self._leftovers_table.blockSignals(True)
        self._leftovers_table.setSortingEnabled(False)
        self._leftovers_table.setRowCount(len(results))

        if not results:
            self._leftovers_status_lbl.setText(tr('unst_leftovers_none'))
            self._leftovers_table.setRowCount(0)
            self._clean_leftovers_btn.setEnabled(False)
            self._leftovers_table.blockSignals(False)
            return

        self._leftovers_status_lbl.setText('')

        for r, (path, size) in enumerate(results):
            # Checkbox
            cb = QTableWidgetItem()
            cb.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            cb.setCheckState(Qt.CheckState.Checked)
            self._leftovers_table.setItem(r, 0, cb)

            # Path
            path_item = QTableWidgetItem(path)
            path_item.setToolTip(path)
            path_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._leftovers_table.setItem(r, 1, path_item)

            # Size
            size_item = _SortItem(fmt_size(size), size)
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            size_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._leftovers_table.setItem(r, 2, size_item)

        self._leftovers_table.setSortingEnabled(True)
        self._leftovers_table.blockSignals(False)
        self._clean_leftovers_btn.setEnabled(True)

    # ── Clean leftovers ───────────────────────────────────────────────────────

    def _do_clean_leftovers(self):
        paths: list[str] = []
        for r in range(self._leftovers_table.rowCount()):
            cb = self._leftovers_table.item(r, 0)
            path_item = self._leftovers_table.item(r, 1)
            if cb and path_item:
                if cb.checkState() == Qt.CheckState.Checked:
                    paths.append(path_item.text())

        if not paths:
            return

        reply = QMessageBox.question(
            self,
            tr('unst_leftovers_clean'),
            f'Delete {len(paths)} leftover location(s)? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        cleaned, errors = clean_leftovers(paths)

        # Remove cleaned rows from table
        rows_to_remove = []
        for r in range(self._leftovers_table.rowCount()):
            cb = self._leftovers_table.item(r, 0)
            path_item = self._leftovers_table.item(r, 1)
            if cb and path_item and path_item.text() in paths:
                if path_item.text() not in errors:
                    rows_to_remove.append(r)

        for r in reversed(rows_to_remove):
            self._leftovers_table.removeRow(r)

        if self._leftovers_table.rowCount() == 0:
            self._leftovers_status_lbl.setText(tr('unst_leftovers_none'))
            self._clean_leftovers_btn.setEnabled(False)

        msg = f'Cleaned {cleaned} location(s).'
        if errors:
            msg += '\n\nErrors:\n' + '\n'.join(errors[:8])
            QMessageBox.warning(self, tr('unst_leftovers_clean'), msg)
        else:
            QMessageBox.information(self, tr('unst_leftovers_clean'), msg)

    # ── Context menu ──────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            row = self._table.rowAt(pos.y())
            if row < 0:
                return
            self._table.selectRow(row)
            rows = self._table.selectionModel().selectedRows()

        if not rows:
            return

        app = self._current_app

        menu = QMenu(self)
        act_uninstall   = menu.addAction(tr('unst_ctx_uninstall'))
        act_uninstall.setEnabled(app is not None)
        menu.addSeparator()
        act_leftovers   = menu.addAction(tr('unst_ctx_leftovers'))
        act_leftovers.setEnabled(app is not None)
        menu.addSeparator()
        act_copy_name   = menu.addAction(tr('unst_ctx_copy_name'))
        act_copy_name.setEnabled(app is not None)
        act_copy_pub    = menu.addAction(tr('unst_ctx_copy_pub'))
        act_copy_pub.setEnabled(app is not None and bool(app.publisher))

        chosen = menu.exec(QCursor.pos())
        if chosen == act_uninstall:
            self._do_uninstall()
        elif chosen == act_leftovers:
            if not self._leftovers_panel.isVisible():
                self._show_leftovers_panel()
        elif chosen == act_copy_name and app:
            QApplication.clipboard().setText(app.name)
        elif chosen == act_copy_pub and app:
            QApplication.clipboard().setText(app.publisher)

    # ── Retranslation ─────────────────────────────────────────────────────────

    def retranslate(self):
        self._search_edit.setPlaceholderText(tr('unst_search_placeholder'))
        self._show_system_cb.setText(tr('unst_show_system'))
        self._refresh_btn.setText(tr('unst_refresh'))
        self._uninstall_btn.setText(tr('unst_uninstall_btn'))
        self._advanced_btn.setText(tr('unst_advanced_btn'))

        self._table.setHorizontalHeaderLabels([
            tr('unst_col_name'),
            tr('unst_col_publisher'),
            tr('unst_col_version'),
            tr('unst_col_size'),
            tr('unst_col_date'),
        ])

        self._leftovers_title_lbl.setText(tr('unst_leftovers_title'))
        self._leftovers_close_btn.setText(tr('unst_leftovers_close'))
        self._clean_leftovers_btn.setText(tr('unst_leftovers_clean'))
        self._leftovers_table.setHorizontalHeaderLabels([
            '',
            tr('unst_leftovers_col_path'),
            tr('unst_leftovers_col_size'),
        ])

        # Refresh status label with current count
        n = len(self._filtered_apps)
        if n or self._all_apps:
            self._status_lbl.setText(tr('unst_status', n=n))
