"""
unitool/ui/privacy_traces_tab.py
Traces sub-tab: scan / clean file-based privacy artifacts.
"""
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QProgressBar,
    QCheckBox, QMessageBox, QTextEdit, QStackedWidget, QApplication, QMenu,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QTextCursor

from ..privacy import (
    ArtifactItem, scan_all, clean_item,
    scan_shell_history, scan_usb_traces, scan_credentials,
    scan_cloud_traces, scan_network_traces, scan_clipboard,
    get_artifact_preview, fmt_size,
)
from ..translations import tr

_CATEGORIES = [
    ('shell_history', '🖥',  'prv_cat_shell'),
    ('usb_traces',    '💾',  'prv_cat_usb'),
    ('network',       '🌐',  'prv_cat_network'),
    ('credentials',   '🔑',  'prv_cat_creds'),
    ('cloud',         '☁',  'prv_cat_cloud'),
    ('clipboard',     '📋',  'prv_cat_clipboard'),
]

_CAT_SCAN = {
    'shell_history': scan_shell_history,
    'usb_traces':    scan_usb_traces,
    'network':       scan_network_traces,
    'credentials':   scan_credentials,
    'cloud':         scan_cloud_traces,
    'clipboard':     scan_clipboard,
}

_COLOR_FOUND   = QColor(255, 140, 60)
_COLOR_CLEANED = QColor(77,  219, 138)
_COLOR_MANUAL  = QColor(130, 130, 130)
_COLOR_MISSING = QColor(80,  80,  80)


# ── Worker threads ────────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    done = pyqtSignal(list)

    def run(self):
        self.done.emit(scan_all())


class _PreviewWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, item: ArtifactItem, parent=None):
        super().__init__(parent)
        self._item = item

    def run(self):
        self.done.emit(get_artifact_preview(self._item))


# ── Traces Tab ────────────────────────────────────────────────────────────────

class TracesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[ArtifactItem] = []
        self._shown_items: list[ArtifactItem] = []
        self._scan_worker: _ScanWorker | None = None
        self._preview_worker: _PreviewWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_cmd_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_category_panel())
        splitter.addWidget(self._build_content_area())
        splitter.setSizes([200, 1000])
        layout.addWidget(splitter, 1)

    # ── Command bar ───────────────────────────────────────────────────────────

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._btn_scan = QPushButton(tr('prv_scan_btn'))
        self._btn_scan.setObjectName('primaryCmd')
        self._btn_scan.clicked.connect(self._scan_all)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._cb_secure = QCheckBox(tr('prv_secure_lbl'))

        self._btn_clean = QPushButton(tr('prv_clean_btn'))
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
        lay.addWidget(self._cb_secure)
        lay.addSpacing(8)
        lay.addWidget(self._btn_clean)
        lay.addStretch()
        lay.addWidget(self._progress)
        return bar

    # ── Category panel ────────────────────────────────────────────────────────

    def _build_category_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('leftPanel')
        panel.setFixedWidth(200)
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

        self._status_lbl = QLabel(tr('prv_status_idle'))
        self._status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.40); font-size: 12px; padding: 2px 0;'
        )
        outer.addWidget(self._status_lbl)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self._build_artifact_table())
        vsplit.addWidget(self._build_preview_panel())
        vsplit.setSizes([340, 180])

        self._note_lbl = QLabel(tr('prv_ssd_note'))
        self._note_lbl.setWordWrap(True)
        self._note_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.25); font-size: 10px; '
            'padding: 4px 2px; border-top: 1px solid rgba(255,255,255,0.06);'
        )

        outer.addWidget(vsplit, 1)
        outer.addWidget(self._note_lbl)
        return panel

    def _build_artifact_table(self) -> QTableWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            '', tr('prv_col_item'), tr('prv_col_path'),
            tr('prv_col_size'), tr('prv_col_status'),
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

        self._preview_title = QLabel(tr('prv_preview_title'))
        self._preview_title.setStyleSheet(
            'color: rgba(255,255,255,0.35); font-size: 9px; '
            'font-weight: 700; letter-spacing: 0.8px;'
        )
        self._preview_loading = QLabel(tr('prv_preview_loading'))
        self._preview_loading.setStyleSheet('color: rgba(255,255,255,0.28); font-size: 10px;')
        self._preview_loading.setVisible(False)
        hdr_lay.addWidget(self._preview_title)
        hdr_lay.addStretch()
        hdr_lay.addWidget(self._preview_loading)

        self._preview_text = QTextEdit()
        self._preview_text.setReadOnly(True)
        self._preview_text.setStyleSheet(
            'QTextEdit { background: transparent; border: none;'
            '  font-family: "Consolas","Cascadia Code","JetBrains Mono",monospace;'
            '  font-size: 11px; color: rgba(255,255,255,0.75); padding: 4px 8px; }'
        )
        self._preview_text.setPlaceholderText(tr('prv_no_preview'))

        lay.addWidget(hdr)
        lay.addWidget(self._preview_text, 1)
        return panel

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _scan_all(self):
        self._btn_scan.setEnabled(False)
        self._progress.setVisible(True)
        self._status_lbl.setText(tr('prv_status_scanning'))

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
            tr('prv_status_found', n=len(existing), size=fmt_size(total_size))
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
        filtered = [i for i in self._items if i.category == cat_id]
        if not filtered:
            fn = _CAT_SCAN.get(cat_id)
            if fn:
                try:
                    filtered = fn()
                    known = {i.path for i in self._items}
                    for item in filtered:
                        if item.path not in known:
                            self._items.append(item)
                except Exception:
                    pass
        self._show_category(cat_id)

    def _show_category(self, cat_id: str):
        items = [i for i in self._items if i.category == cat_id]
        if cat_id == 'clipboard':
            live = self._get_clipboard_item()
            if live:
                items = [live] + items
        self._show_items(items)

    def _get_clipboard_item(self) -> ArtifactItem | None:
        mime = QApplication.clipboard().mimeData()
        if not mime:
            return None
        if mime.hasText():
            text = mime.text()
            if not text.strip():
                return None
            byte_len   = len(text.encode('utf-8'))
            line_count = len(text.splitlines())
            label      = tr('prv_cb_text_label', lines=line_count, size=fmt_size(byte_len))
            body = (f'{label}\n{"─"*60}\n'
                    + (text if len(text) <= 8192 else text[:8192] + '\n…[truncated]'))
            return ArtifactItem(category='clipboard', label=label,
                                path='<clipboard:text>', size=byte_len,
                                description=body, exists=True,
                                deletable=True, method='clipboard_clear')
        if mime.hasImage():
            img = mime.imageData()
            w, h = img.width(), img.height()
            label = tr('prv_cb_image_label', w=w, h=h)
            return ArtifactItem(category='clipboard', label=label,
                                path='<clipboard:image>', size=w * h * 4,
                                description=f'{label}\n{"─"*60}\n{tr("prv_cb_image_note")}',
                                exists=True, deletable=True, method='clipboard_clear')
        if mime.hasUrls():
            urls  = mime.urls()
            paths = [u.toLocalFile() or u.toString() for u in urls]
            label = tr('prv_cb_files_label', n=len(paths))
            body  = (f'{label}\n{"─"*60}\n' + '\n'.join(paths[:50])
                     + (f'\n…and {len(paths)-50} more' if len(paths) > 50 else ''))
            return ArtifactItem(category='clipboard', label=label,
                                path='<clipboard:files>', size=0,
                                description=body, exists=True,
                                deletable=True, method='clipboard_clear')
        formats = mime.formats()
        label = tr('prv_cb_other_label', fmt=formats[0] if formats else '?')
        return ArtifactItem(category='clipboard', label=label,
                            path='<clipboard:other>', size=0,
                            description=f'{label}\n\nFormats: {", ".join(formats[:10])}',
                            exists=True, deletable=True, method='clipboard_clear')

    # ── Table population ──────────────────────────────────────────────────────

    def _show_items(self, items: list[ArtifactItem]):
        self._shown_items = items
        vp = self._table.viewport()
        vp.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(items))

        for r, item in enumerate(items):
            cb = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled
            if item.deletable:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                cb.setCheckState(Qt.CheckState.Unchecked)
            cb.setFlags(flags)
            self._table.setItem(r, 0, cb)

            lbl_item = QTableWidgetItem(item.label)
            lbl_item.setToolTip(item.description)
            lbl_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 1, lbl_item)

            path_item = QTableWidgetItem(item.path)
            path_item.setToolTip(item.path)
            path_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 2, path_item)

            sz = QTableWidgetItem(fmt_size(item.size) if item.size else '—')
            sz.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 3, sz)

            if not item.exists:
                status, color = tr('prv_status_not_found'), _COLOR_MISSING
            elif item.deletable:
                status, color = tr('prv_status_found_item'), _COLOR_FOUND
            else:
                status, color = tr('prv_status_manual'), _COLOR_MANUAL
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
        self._preview_text.setPlaceholderText(tr('prv_no_preview'))

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_row_selected(self, row: int, _col, _prev_row, _prev_col):
        if row < 0 or row >= len(self._shown_items):
            return
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.done.disconnect()
            self._preview_worker.quit()
            self._preview_worker.wait(100)
        self._preview_loading.setVisible(True)
        self._preview_text.setPlaceholderText('')
        self._preview_worker = _PreviewWorker(self._shown_items[row], self)
        self._preview_worker.done.connect(self._on_preview_ready)
        self._preview_worker.start()

    def _on_preview_ready(self, text: str):
        self._preview_loading.setVisible(False)
        self._preview_text.setPlainText(text)
        cur = self._cat_list.currentItem()
        if cur and cur.data(Qt.ItemDataRole.UserRole) == 'shell_history':
            self._preview_text.moveCursor(QTextCursor.MoveOperation.End)
        else:
            self._preview_text.moveCursor(QTextCursor.MoveOperation.Start)

    # ── Context menu ──────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._shown_items):
            return
        item = self._shown_items[row]
        menu = QMenu(self)
        act_preview = menu.addAction(tr('prv_ctx_preview'))
        act_copy    = menu.addAction(tr('prv_ctx_copy_path'))
        act_open    = menu.addAction(tr('prv_ctx_open_folder'))
        act_open.setEnabled(os.path.exists(item.path))
        menu.addSeparator()
        act_clean = menu.addAction(tr('prv_ctx_clean'))
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

    def _clean_single(self, row: int, item: ArtifactItem):
        reply = QMessageBox.question(
            self, tr('prv_confirm_title'),
            tr('prv_confirm_msg', n=1, size=fmt_size(item.size)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if item.method == 'clipboard_clear':
            QApplication.clipboard().clear()
            ok, err = True, ''
        else:
            ok, err = clean_item(item, secure=self._cb_secure.isChecked())
        self._table.blockSignals(True)
        if ok:
            if st := self._table.item(row, 4):
                st.setText('Cleaned'); st.setForeground(_COLOR_CLEANED)
            if cb := self._table.item(row, 0):
                cb.setCheckState(Qt.CheckState.Unchecked)
                cb.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if sz := self._table.item(row, 3):
                sz.setText('—')
        else:
            QMessageBox.warning(self, 'Error', err)
        self._table.blockSignals(False)
        self._refresh_clean_btn()

    # ── Check / clean ─────────────────────────────────────────────────────────

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

    def _clean_selected(self):
        to_clean = [
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
            self, tr('prv_confirm_title'),
            tr('prv_confirm_msg', n=len(to_clean), size=fmt_size(total_size)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        secure = self._cb_secure.isChecked()
        cleaned, errors = 0, []
        self._table.blockSignals(True)
        for r, item in to_clean:
            if item.method == 'clipboard_clear':
                QApplication.clipboard().clear()
                ok, err = True, ''
            else:
                ok, err = clean_item(item, secure=secure)
            if ok:
                cleaned += 1
                if st := self._table.item(r, 4):
                    st.setText('Cleaned'); st.setForeground(_COLOR_CLEANED)
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
            QMessageBox.warning(self, 'Done (with errors)',
                                tr('prv_status_cleaned', n=cleaned) + '\n\n' + '\n'.join(errors[:8]))
        else:
            self._status_lbl.setText(tr('prv_status_cleaned', n=cleaned))

    # ── Retranslation ─────────────────────────────────────────────────────────

    def retranslate(self):
        self._btn_scan.setText(tr('prv_scan_btn'))
        self._btn_clean.setText(tr('prv_clean_btn'))
        self._cb_secure.setText(tr('prv_secure_lbl'))
        self._status_lbl.setText(tr('prv_status_idle'))
        self._note_lbl.setText(tr('prv_ssd_note'))
        self._preview_title.setText(tr('prv_preview_title'))
        self._preview_loading.setText(tr('prv_preview_loading'))
        self._preview_text.setPlaceholderText(tr('prv_no_preview'))
        self._table.setHorizontalHeaderLabels([
            '', tr('prv_col_item'), tr('prv_col_path'),
            tr('prv_col_size'), tr('prv_col_status'),
        ])
        for i in range(self._cat_list.count()):
            li  = self._cat_list.item(i)
            cid = li.data(Qt.ItemDataRole.UserRole)
            for cat_id, icon, tr_key in _CATEGORIES:
                if cat_id == cid:
                    li.setText(f'{icon}  {tr(tr_key)}')
                    break
