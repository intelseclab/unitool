import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QMenu, QMessageBox, QApplication,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QCursor

from ..startup import (
    StartupEntry, get_startup_entries,
    enable_entry, disable_entry, delete_entry, open_entry_location,
)
from ..translations import tr


# ── Colors ────────────────────────────────────────────────────────────────────

_GREEN  = QColor(77,  219, 138)   # enabled dot
_GRAY   = QColor(100, 100, 100)   # disabled dot


# ── Worker ────────────────────────────────────────────────────────────────────

class _LoadWorker(QThread):
    done = pyqtSignal(list)

    def run(self):
        self.done.emit(get_startup_entries())


# ── Widget ────────────────────────────────────────────────────────────────────

_COL_STATUS    = 0
_COL_NAME      = 1
_COL_PUBLISHER = 2
_COL_SOURCE    = 3
_COL_COMMAND   = 4

_SOURCE_LABELS = {
    'HKCU_Run':          'Registry',
    'HKLM_Run':          'Registry',
    'HKCU_RunOnce':      'Registry',
    'HKLM_RunOnce':      'Registry',
    'StartupFolder_User': 'Startup Folder',
    'StartupFolder_All':  'Startup Folder',
    'LaunchAgent':        'Launch Agent',
    'Autostart':          'Autostart',
}


class StartupWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[StartupEntry] = []
        self._worker: _LoadWorker | None = None
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_cmd_bar())
        layout.addWidget(self._build_table(), 1)

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._btn_refresh = QPushButton(tr('strt_refresh'))
        self._btn_refresh.setObjectName('cmdBtn')
        self._btn_refresh.clicked.connect(self._load)

        sep1 = _make_sep()

        self._btn_enable = QPushButton(tr('strt_enable'))
        self._btn_enable.setObjectName('cmdBtn')
        self._btn_enable.setEnabled(False)
        self._btn_enable.clicked.connect(self._on_enable)

        self._btn_disable = QPushButton(tr('strt_disable'))
        self._btn_disable.setObjectName('cmdBtn')
        self._btn_disable.setEnabled(False)
        self._btn_disable.clicked.connect(self._on_disable)

        sep2 = _make_sep()

        self._btn_delete = QPushButton(tr('strt_delete'))
        self._btn_delete.setObjectName('dangerCmd')
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._on_delete)

        self._status_lbl = QLabel('')
        self._status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.40); font-size: 12px; padding: 0 6px;'
        )

        lay.addWidget(self._btn_refresh)
        lay.addWidget(sep1)
        lay.addWidget(self._btn_enable)
        lay.addWidget(self._btn_disable)
        lay.addWidget(sep2)
        lay.addWidget(self._btn_delete)
        lay.addStretch()
        lay.addWidget(self._status_lbl)
        return bar

    def _build_table(self) -> QWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            tr('strt_col_status'),
            tr('strt_col_name'),
            tr('strt_col_publisher'),
            tr('strt_col_source'),
            tr('strt_col_command'),
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.currentCellChanged.connect(self._on_row_changed)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_STATUS,    QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(_COL_NAME,      QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_PUBLISHER, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_SOURCE,    QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_COMMAND,   QHeaderView.ResizeMode.Stretch)

        self._table.setColumnWidth(_COL_STATUS,    28)
        self._table.setColumnWidth(_COL_NAME,      200)
        self._table.setColumnWidth(_COL_PUBLISHER, 150)
        self._table.setColumnWidth(_COL_SOURCE,    120)

        return self._table

    # ── Load ──────────────────────────────────────────────────────────────────

    def _load(self):
        self._btn_refresh.setEnabled(False)
        self._status_lbl.setText(tr('strt_loading'))
        self._set_action_buttons(selected=False, enabled=False)

        if self._worker and self._worker.isRunning():
            self._worker.done.disconnect()
            self._worker.quit()
            self._worker.wait(300)

        self._worker = _LoadWorker(self)
        self._worker.done.connect(self._on_load_done)
        self._worker.start()

    def _on_load_done(self, entries: list):
        self._btn_refresh.setEnabled(True)
        self._entries = entries
        self._populate(entries)
        n = len(entries)
        n_enabled  = sum(1 for e in entries if e.enabled)
        n_disabled = n - n_enabled
        self._status_lbl.setText(
            tr('strt_status', n=n, enabled=n_enabled, disabled=n_disabled)
        )

    # ── Table population ──────────────────────────────────────────────────────

    def _populate(self, entries: list[StartupEntry]):
        vp = self._table.viewport()
        vp.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(entries))

        for r, e in enumerate(entries):
            # Col 0: status dot
            dot = '●' if e.enabled else '○'
            dot_item = QTableWidgetItem(dot)
            dot_item.setForeground(_GREEN if e.enabled else _GRAY)
            dot_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dot_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_STATUS, dot_item)

            # Col 1: name
            name_item = QTableWidgetItem(e.name)
            name_item.setToolTip(e.command)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_NAME, name_item)

            # Col 2: publisher
            pub_item = QTableWidgetItem(e.publisher or e.file_description or '')
            pub_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_PUBLISHER, pub_item)

            # Col 3: source label
            src_label = _SOURCE_LABELS.get(e.source, e.source)
            src_item = QTableWidgetItem(src_label)
            src_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_SOURCE, src_item)

            # Col 4: command
            cmd_item = QTableWidgetItem(e.command)
            cmd_item.setToolTip(e.command)
            cmd_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_COMMAND, cmd_item)

        self._table.setSortingEnabled(True)
        self._table.blockSignals(False)
        vp.setUpdatesEnabled(True)
        vp.update()

        self._set_action_buttons(selected=False, enabled=False)

    # ── Row selection / button state ──────────────────────────────────────────

    def _on_row_changed(self, row: int, _col, _prev_row, _prev_col):
        if row < 0 or row >= len(self._entries):
            self._set_action_buttons(selected=False, enabled=False)
            return
        entry = self._entries[self._logical_row(row)]
        self._set_action_buttons(selected=True, enabled=entry.enabled)

    def _set_action_buttons(self, *, selected: bool, enabled: bool):
        self._btn_enable.setEnabled(selected and not enabled)
        self._btn_disable.setEnabled(selected and enabled)
        self._btn_delete.setEnabled(selected)

    def _selected_entry(self) -> tuple[int, StartupEntry] | tuple[None, None]:
        """Returns (visual_row, StartupEntry) or (None, None)."""
        row = self._table.currentRow()
        if row < 0:
            return None, None
        logical = self._logical_row(row)
        if logical < 0 or logical >= len(self._entries):
            return None, None
        return row, self._entries[logical]

    def _logical_row(self, visual_row: int) -> int:
        """Map visual (possibly sorted) row to _entries index via column 0 data."""
        # The table uses internal sort proxying via QTableWidget's sort —
        # we store the original index in UserRole on the name item.
        name_item = self._table.item(visual_row, _COL_NAME)
        if name_item is None:
            return visual_row
        idx = name_item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return visual_row
        return int(idx)

    # Override _populate to store UserRole indices:
    def _populate(self, entries: list[StartupEntry]):  # noqa: F811
        vp = self._table.viewport()
        vp.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(entries))

        for r, e in enumerate(entries):
            # Col 0: status dot
            dot = '●' if e.enabled else '○'
            dot_item = QTableWidgetItem(dot)
            dot_item.setForeground(_GREEN if e.enabled else _GRAY)
            dot_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dot_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_STATUS, dot_item)

            # Col 1: name — store original index so we survive sorting
            name_item = QTableWidgetItem(e.name)
            name_item.setToolTip(e.command)
            name_item.setData(Qt.ItemDataRole.UserRole, r)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_NAME, name_item)

            # Col 2: publisher
            pub_item = QTableWidgetItem(e.publisher or e.file_description or '')
            pub_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_PUBLISHER, pub_item)

            # Col 3: source label
            src_label = _SOURCE_LABELS.get(e.source, e.source)
            src_item = QTableWidgetItem(src_label)
            src_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_SOURCE, src_item)

            # Col 4: command
            cmd_item = QTableWidgetItem(e.command)
            cmd_item.setToolTip(e.command)
            cmd_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, _COL_COMMAND, cmd_item)

        self._table.setSortingEnabled(True)
        self._table.blockSignals(False)
        vp.setUpdatesEnabled(True)
        vp.update()

        self._set_action_buttons(selected=False, enabled=False)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _warn_if_hklm(self, entry: StartupEntry) -> bool:
        """Show admin warning for HKLM entries.  Returns True to proceed."""
        if entry.source in ('HKLM_Run', 'HKLM_RunOnce'):
            QMessageBox.warning(
                self,
                tr('strt_admin_warn'),
                tr('strt_admin_warn_msg'),
            )
        return True  # still proceed; user has been informed

    def _on_enable(self):
        row, entry = self._selected_entry()
        if entry is None:
            return
        self._warn_if_hklm(entry)
        ok, err = enable_entry(entry)
        if ok:
            entry.enabled = True
            self._refresh_row(row, entry)
            self._set_action_buttons(selected=True, enabled=True)
        else:
            QMessageBox.warning(self, tr('strt_enable'), err)

    def _on_disable(self):
        row, entry = self._selected_entry()
        if entry is None:
            return
        self._warn_if_hklm(entry)
        ok, err = disable_entry(entry)
        if ok:
            entry.enabled = False
            self._refresh_row(row, entry)
            self._set_action_buttons(selected=True, enabled=False)
        else:
            QMessageBox.warning(self, tr('strt_disable'), err)

    def _on_delete(self):
        row, entry = self._selected_entry()
        if entry is None:
            return
        reply = QMessageBox.question(
            self,
            tr('strt_confirm_delete'),
            tr('strt_confirm_delete_msg', name=entry.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._warn_if_hklm(entry)
        ok, err = delete_entry(entry)
        if ok:
            logical = self._logical_row(row)
            self._entries.pop(logical)
            self._table.blockSignals(True)
            self._table.removeRow(row)
            # Re-index UserRole for all rows after the removed one
            for vr in range(self._table.rowCount()):
                ni = self._table.item(vr, _COL_NAME)
                if ni is not None:
                    old_idx = ni.data(Qt.ItemDataRole.UserRole)
                    if old_idx is not None and int(old_idx) > logical:
                        ni.setData(Qt.ItemDataRole.UserRole, int(old_idx) - 1)
            self._table.blockSignals(False)
            self._set_action_buttons(selected=False, enabled=False)
            # Update status counts
            n = len(self._entries)
            n_enabled  = sum(1 for e in self._entries if e.enabled)
            n_disabled = n - n_enabled
            self._status_lbl.setText(
                tr('strt_status', n=n, enabled=n_enabled, disabled=n_disabled)
            )
        else:
            QMessageBox.warning(self, tr('strt_delete'), err)

    # ── Row refresh helper ────────────────────────────────────────────────────

    def _refresh_row(self, visual_row: int, entry: StartupEntry):
        self._table.blockSignals(True)
        dot = '●' if entry.enabled else '○'
        dot_item = self._table.item(visual_row, _COL_STATUS)
        if dot_item:
            dot_item.setText(dot)
            dot_item.setForeground(_GREEN if entry.enabled else _GRAY)
        self._table.blockSignals(False)
        self._table.viewport().update()

    # ── Context menu ──────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        logical = self._logical_row(row)
        if logical < 0 or logical >= len(self._entries):
            return
        entry = self._entries[logical]

        menu = QMenu(self)

        act_enable  = menu.addAction(tr('strt_ctx_enable'))
        act_disable = menu.addAction(tr('strt_ctx_disable'))
        act_delete  = menu.addAction(tr('strt_ctx_delete'))
        act_enable.setEnabled(not entry.enabled)
        act_disable.setEnabled(entry.enabled)

        menu.addSeparator()

        act_open = menu.addAction(tr('strt_ctx_open'))
        act_open.setEnabled(bool(entry.exe_path or entry.folder_path))

        act_copy = menu.addAction(tr('strt_ctx_copy'))

        chosen = menu.exec(QCursor.pos())
        if chosen == act_enable:
            self._table.setCurrentCell(row, 0)
            self._on_enable()
        elif chosen == act_disable:
            self._table.setCurrentCell(row, 0)
            self._on_disable()
        elif chosen == act_delete:
            self._table.setCurrentCell(row, 0)
            self._on_delete()
        elif chosen == act_open:
            open_entry_location(entry)
        elif chosen == act_copy:
            QApplication.clipboard().setText(entry.command)

    # ── Retranslate ───────────────────────────────────────────────────────────

    def retranslate(self):
        self._btn_refresh.setText(tr('strt_refresh'))
        self._btn_enable.setText(tr('strt_enable'))
        self._btn_disable.setText(tr('strt_disable'))
        self._btn_delete.setText(tr('strt_delete'))
        self._table.setHorizontalHeaderLabels([
            tr('strt_col_status'),
            tr('strt_col_name'),
            tr('strt_col_publisher'),
            tr('strt_col_source'),
            tr('strt_col_command'),
        ])

    # ── Show event — lazy initial load ────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._entries and (self._worker is None or not self._worker.isRunning()):
            self._load()


# ── Small utility ─────────────────────────────────────────────────────────────

def _make_sep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setStyleSheet(
        'background: rgba(255,255,255,0.10); max-width: 1px; border: none;'
    )
    return sep
