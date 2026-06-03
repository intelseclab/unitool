import os
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QProgressBar,
    QMessageBox, QTextEdit, QApplication,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QCursor, QTextCursor

from ..junk import (
    JunkItem, scan_all_junk, clean_junk_item, get_junk_preview, fmt_size,
)
from ..translations import tr

_CATEGORIES = [
    ('temp',          '🗑',  'jnk_cat_temp'),
    ('windows',       '🪟',  'jnk_cat_windows'),
    ('browser_cache', '🌐',  'jnk_cat_browser'),
    ('crash',         '💥',  'jnk_cat_crash'),
    ('logs',          '📋',  'jnk_cat_logs'),
]

_COLOR_FOUND   = QColor(255, 140, 60)
_COLOR_CLEANED = QColor(77,  219, 138)
_COLOR_MISSING = QColor(80,   80,  80)


# ── Worker threads ────────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    done = pyqtSignal(list)

    def run(self):
        self.done.emit(scan_all_junk())


class _PreviewWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, item: JunkItem, parent=None):
        super().__init__(parent)
        self._item = item

    def run(self):
        self.done.emit(get_junk_preview(self._item))


# ── Main widget ───────────────────────────────────────────────────────────────

class JunkWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[JunkItem] = []
        self._shown_items: list[JunkItem] = []
        self._scan_worker: _ScanWorker | None = None
        self._preview_worker: _PreviewWorker | None = None
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_cmd_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_category_panel())
        splitter.addWidget(self._build_content_area())
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

        self._btn_scan = QPushButton(tr('jnk_scan_btn'))
        self._btn_scan.setObjectName('primaryCmd')
        self._btn_scan.clicked.connect(self._scan_all)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._btn_clean = QPushButton(tr('jnk_clean_btn'))
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

    # ── Category panel ────────────────────────────────────────────────────────

    def _build_category_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('leftPanel')
        panel.setFixedWidth(220)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 14, 8, 8)
        lay.setSpacing(4)

        hdr = QLabel('CATEGORIES')
        hdr.setStyleSheet(
            'color: rgba(255,255,255,0.32); font-size: 9px; font-weight: 700; '
            'letter-spacing: 0.8px; padding-left: 8px;'
        )
        lay.addWidget(hdr)
        lay.addSpacing(4)

        self._cat_list = QListWidget()
        self._cat_list.setSpacing(2)
        for cat_id, icon, tr_key in _CATEGORIES:
            item = QListWidgetItem(f'{icon}  {tr(tr_key)}')
            item.setData(Qt.ItemDataRole.UserRole, cat_id)
            self._cat_list.addItem(item)

        self._cat_list.currentItemChanged.connect(self._on_cat_changed)
        lay.addWidget(self._cat_list, 1)
        return panel

    # ── Content area ──────────────────────────────────────────────────────────

    def _build_content_area(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(8, 10, 8, 8)
        outer.setSpacing(6)

        self._status_lbl = QLabel(tr('jnk_status_idle'))
        self._status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.40); font-size: 12px; padding: 2px 0;'
        )
        outer.addWidget(self._status_lbl)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self._build_junk_table())
        vsplit.addWidget(self._build_preview_panel())
        vsplit.setSizes([340, 200])

        self._note_lbl = QLabel(tr('jnk_ssd_note'))
        self._note_lbl.setWordWrap(True)
        self._note_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.25); font-size: 10px; '
            'padding: 4px 2px; border-top: 1px solid rgba(255,255,255,0.06);'
        )

        outer.addWidget(vsplit, 1)
        outer.addWidget(self._note_lbl)
        return panel

    # ── Junk table ────────────────────────────────────────────────────────────

    def _build_junk_table(self) -> QTableWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            '', tr('jnk_col_item'), tr('jnk_col_path'),
            tr('jnk_col_size'), tr('jnk_col_status'),
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
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(0, 28)
        self._table.setColumnWidth(1, 220)
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

        self._preview_title = QLabel(tr('jnk_preview_title'))
        self._preview_title.setStyleSheet(
            'color: rgba(255,255,255,0.35); font-size: 9px; '
            'font-weight: 700; letter-spacing: 0.8px;'
        )

        self._preview_loading = QLabel(tr('jnk_preview_loading'))
        self._preview_loading.setStyleSheet('color: rgba(255,255,255,0.28); font-size: 10px;')
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
        self._preview_text.setPlaceholderText(tr('jnk_no_preview'))

        lay.addWidget(hdr)
        lay.addWidget(self._preview_text, 1)
        return panel

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _scan_all(self):
        self._btn_scan.setEnabled(False)
        self._progress.setVisible(True)
        self._status_lbl.setText(tr('jnk_status_scanning'))

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
        self._items = items

        existing   = [i for i in items if i.exists]
        total_size = sum(i.size for i in existing)
        self._status_lbl.setText(
            tr('jnk_status_found', n=len(existing), size=fmt_size(total_size))
        )

        cur = self._cat_list.currentItem()
        if cur:
            self._show_category(cur.data(Qt.ItemDataRole.UserRole))
        else:
            self._show_items(items)

    # ── Category selection ────────────────────────────────────────────────────

    def _on_cat_changed(self, cur, _prev):
        if cur is None:
            return
        cat_id = cur.data(Qt.ItemDataRole.UserRole)
        self._show_category(cat_id)

    def _show_category(self, cat_id: str):
        items = [i for i in self._items if i.category == cat_id]
        self._show_items(items)

    # ── Table population ──────────────────────────────────────────────────────

    def _show_items(self, items: list[JunkItem]):
        self._shown_items = items

        vp = self._table.viewport()
        vp.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(items))

        for r, item in enumerate(items):
            # Checkbox column
            cb = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled
            if item.deletable:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                cb.setCheckState(Qt.CheckState.Unchecked)
            cb.setFlags(flags)
            self._table.setItem(r, 0, cb)

            # Item label
            lbl_item = QTableWidgetItem(item.label)
            lbl_item.setToolTip(item.description)
            lbl_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 1, lbl_item)

            # Path
            path_item = QTableWidgetItem(item.path)
            path_item.setToolTip(item.path)
            path_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 2, path_item)

            # Size
            sz = QTableWidgetItem(fmt_size(item.size) if item.size else '—')
            sz.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 3, sz)

            # Status
            if not item.exists:
                status, color = tr('jnk_status_not_found'), _COLOR_MISSING
            else:
                status, color = tr('jnk_status_found_item'), _COLOR_FOUND
            st = QTableWidgetItem(status)
            st.setForeground(color)
            st.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 4, st)

        self._table.setSortingEnabled(True)
        self._table.blockSignals(False)
        vp.setUpdatesEnabled(True)
        vp.update()

        self._refresh_clean_btn()
        self._preview_text.clear()
        self._preview_text.setPlaceholderText(tr('jnk_no_preview'))

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

    # ── Right-click context menu ──────────────────────────────────────────────

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._shown_items):
            return
        item = self._shown_items[row]

        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)

        act_preview = menu.addAction(tr('jnk_ctx_preview'))
        act_copy    = menu.addAction(tr('jnk_ctx_copy'))

        is_path_accessible = os.path.exists(item.path)
        act_open = menu.addAction(tr('jnk_ctx_open'))
        if not is_path_accessible:
            act_open.setEnabled(False)

        menu.addSeparator()
        act_clean = menu.addAction(tr('jnk_ctx_clean'))
        act_clean.setEnabled(item.deletable)

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

    # ── Clean single item ─────────────────────────────────────────────────────

    def _clean_single(self, row: int, item: JunkItem):
        reply = QMessageBox.question(
            self,
            tr('jnk_confirm_title'),
            tr('jnk_confirm_msg', n=1, size=fmt_size(item.size)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, err = clean_junk_item(item)

        self._table.blockSignals(True)
        if ok:
            st = self._table.item(row, 4)
            if st:
                st.setText('Cleaned')
                st.setForeground(_COLOR_CLEANED)
            cb = self._table.item(row, 0)
            if cb:
                cb.setCheckState(Qt.CheckState.Unchecked)
                cb.setFlags(Qt.ItemFlag.ItemIsEnabled)
            sz = self._table.item(row, 3)
            if sz:
                sz.setText('—')
        else:
            QMessageBox.warning(self, 'Error', err)
        self._table.blockSignals(False)
        self._refresh_clean_btn()

    # ── Checkbox / clean button ───────────────────────────────────────────────

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
        to_clean: list[tuple[int, JunkItem]] = [
            (r, self._shown_items[r])
            for r in range(self._table.rowCount())
            if (cb := self._table.item(r, 0)) is not None
            and bool(cb.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            and cb.checkState() == Qt.CheckState.Checked
            and r < len(self._shown_items)
        ]
        if not to_clean:
            return

        total_size = sum(i.size for _, i in to_clean)
        reply = QMessageBox.question(
            self,
            tr('jnk_confirm_title'),
            tr('jnk_confirm_msg', n=len(to_clean), size=fmt_size(total_size)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        cleaned, errors = 0, []

        self._table.blockSignals(True)
        for r, item in to_clean:
            ok, err = clean_junk_item(item)
            if ok:
                cleaned += 1
                if st := self._table.item(r, 4):
                    st.setText('Cleaned')
                    st.setForeground(_COLOR_CLEANED)
                if cb := self._table.item(r, 0):
                    cb.setCheckState(Qt.CheckState.Unchecked)
                    cb.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if sz := self._table.item(r, 3):
                    sz.setText('—')
            else:
                errors.append(f'{item.label}: {err}')
        self._table.blockSignals(False)
        self._btn_clean.setEnabled(False)

        if errors:
            QMessageBox.warning(
                self, 'Done (with errors)',
                tr('jnk_status_cleaned', n=cleaned) + '\n\n' + '\n'.join(errors[:8]),
            )
        else:
            self._status_lbl.setText(tr('jnk_status_cleaned', n=cleaned))

    # ── Retranslation ─────────────────────────────────────────────────────────

    def retranslate(self):
        self._btn_scan.setText(tr('jnk_scan_btn'))
        self._btn_clean.setText(tr('jnk_clean_btn'))
        self._status_lbl.setText(tr('jnk_status_idle'))
        self._note_lbl.setText(tr('jnk_ssd_note'))
        self._preview_title.setText(tr('jnk_preview_title'))
        self._preview_loading.setText(tr('jnk_preview_loading'))
        self._preview_text.setPlaceholderText(tr('jnk_no_preview'))
        self._table.setHorizontalHeaderLabels([
            '', tr('jnk_col_item'), tr('jnk_col_path'),
            tr('jnk_col_size'), tr('jnk_col_status'),
        ])
        for i in range(self._cat_list.count()):
            li  = self._cat_list.item(i)
            cid = li.data(Qt.ItemDataRole.UserRole)
            for cat_id, icon, tr_key in _CATEGORIES:
                if cat_id == cid:
                    li.setText(f'{icon}  {tr(tr_key)}')
                    break
