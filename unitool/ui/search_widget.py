"""
unitool/ui/search_widget.py
File Search — powered by fast_search.py (platform-native backends).

macOS   → Spotlight (mdfind)  — real-time, no indexing
Linux   → locate/plocate      — real-time, no indexing
Windows → thread-pool scan + SQLite FTS5 — index once, search instantly
"""
from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QFrame,
    QSplitter, QCheckBox, QDateEdit, QProgressBar, QSizePolicy,
    QFileDialog, QButtonGroup,
)
from PyQt6.QtCore import Qt, QTimer, QDate
from PyQt6.QtGui import QCursor, QPixmap, QColor

from ..translations import tr
from ..scanner import FILE_TYPE_EXTENSIONS, fmt_size
from ..platform_utils import open_path, open_folder
from ..fast_search import SearchEngine, SearchWorker, IndexWorker, FileRecord, list_drives

_IMAGE_EXTS    = {'.jpg','.jpeg','.png','.gif','.bmp','.webp','.ico','.tiff','.tif'}
_UNIT_BYTES    = {'KB': 1_024, 'MB': 1_048_576, 'GB': 1_073_741_824}
_ROW_NUM_COLOR = QColor(100, 100, 100)

_TYPE_ICONS = {'Images':'🖼','Videos':'🎬','Audio':'🎵','Documents':'📄','Archives':'📦'}
_EXT_ICON: dict[str,str] = {}
for _tn, _exts in FILE_TYPE_EXTENSIONS.items():
    if _tn != 'All Files':
        _ic = _TYPE_ICONS.get(_tn,'📄')
        for _e in _exts:
            _EXT_ICON[_e] = _ic

def _file_icon(name:str) -> str:
    return _EXT_ICON.get(os.path.splitext(name)[1].lower(),'📄')


class _SortItem(QTableWidgetItem):
    def __init__(self, display:str, sort_key):
        super().__init__(display)
        self._sort_key = sort_key
    def __lt__(self, other):
        if isinstance(other, _SortItem):
            try: return self._sort_key < other._sort_key
            except TypeError: pass
        return super().__lt__(other)


class _Div(QLabel):
    def __init__(self, parent=None):
        super().__init__('|', parent)
        self.setStyleSheet('color:rgba(255,255,255,0.12);padding:0 10px;font-size:12px;')


# ── Widget ────────────────────────────────────────────────────────────────────

class SearchWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine       = SearchEngine()
        self._search_worker: SearchWorker | None = None
        self._index_worker:  IndexWorker  | None = None
        self._scope: str | None = None          # None = all drives
        self._streaming   = False
        self._stream_size = 0
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._run_search)
        self._type_keys = list(FILE_TYPE_EXTENSIONS.keys())
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split)
        split.addWidget(self._build_filter_panel())
        split.addWidget(self._build_results_panel())
        split.setSizes([220, 900])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        self.retranslate()

    # ── Filter panel ──────────────────────────────────────────────────────────

    def _build_filter_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('leftPanel')
        panel.setFixedWidth(220)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14,14,14,14)
        lay.setSpacing(0)

        def _slbl(txt='') -> QLabel:
            l = QLabel(txt)
            l.setStyleSheet('font-size:9px;font-weight:700;letter-spacing:0.8px;'
                            'color:rgba(255,255,255,0.32);background:transparent;')
            return l

        # ── Location ──
        self._lbl_location = _slbl(tr('srch_grp_location'))
        lay.addWidget(self._lbl_location)
        lay.addSpacing(6)

        self._loc_chips_w = QWidget()
        chips_lay = QVBoxLayout(self._loc_chips_w)
        chips_lay.setContentsMargins(0, 0, 0, 0)
        chips_lay.setSpacing(4)

        # "All" chip
        all_row = QHBoxLayout()
        all_row.setSpacing(4)
        self._btn_loc_all = QPushButton(tr('srch_loc_all'))
        self._btn_loc_all.setCheckable(True)
        self._btn_loc_all.setChecked(True)
        self._btn_loc_all.setObjectName('locChip')
        self._btn_loc_all.clicked.connect(lambda: self._set_scope(None))
        all_row.addWidget(self._btn_loc_all)
        all_row.addStretch()
        chips_lay.addLayout(all_row)

        # One chip per drive / mount point
        self._drive_btns: dict[str, QPushButton] = {}
        for drive in list_drives():
            btn = QPushButton(drive.rstrip('/\\').rstrip(':') + ':')
            btn.setCheckable(True)
            btn.setObjectName('locChip')
            btn.clicked.connect(lambda _checked, d=drive: self._set_scope(d))
            self._drive_btns[drive] = btn

        drives_row = QHBoxLayout()
        drives_row.setSpacing(4)
        for btn in self._drive_btns.values():
            drives_row.addWidget(btn)
        drives_row.addStretch()
        chips_lay.addLayout(drives_row)

        # Browse button
        self._btn_browse = QPushButton('📁  ' + tr('srch_loc_browse'))
        self._btn_browse.setObjectName('cmdBtn')
        self._btn_browse.setFixedHeight(26)
        self._btn_browse.clicked.connect(self._browse_location)
        chips_lay.addWidget(self._btn_browse)

        # Current path label (shown when custom folder selected)
        self._lbl_scope = QLabel()
        self._lbl_scope.setWordWrap(True)
        self._lbl_scope.setVisible(False)
        self._lbl_scope.setStyleSheet(
            'font-size: 10px; color: rgba(255,255,255,0.45);'
            ' background: transparent; padding: 2px 0;')
        chips_lay.addWidget(self._lbl_scope)

        lay.addWidget(self._loc_chips_w)
        lay.addSpacing(14)

        # chip style (injected globally)
        self._loc_chips_w.setStyleSheet(
            'QPushButton#locChip {'
            '  background: rgba(255,255,255,0.05);'
            '  color: rgba(255,255,255,0.55);'
            '  border-radius: 4px; font-size: 11px;'
            '  padding: 3px 10px; border: none;'
            '}'
            'QPushButton#locChip:checked {'
            '  background: rgba(76,194,255,0.18);'
            '  color: #4CC2FF; font-weight: 600;'
            '}'
            'QPushButton#locChip:hover:!checked {'
            '  background: rgba(255,255,255,0.09);'
            '}'
        )

        # Search
        self._lbl_search = _slbl('SEARCH')
        self._search_edit = QLineEdit()
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumHeight(34)
        self._search_edit.setPlaceholderText('…')
        self._search_edit.returnPressed.connect(self._run_search)
        lay.addWidget(self._lbl_search)
        lay.addSpacing(5)
        lay.addWidget(self._search_edit)
        lay.addSpacing(14)

        # File type
        self._lbl_type = _slbl()
        self._type_combo = QComboBox()
        for k in self._type_keys:
            self._type_combo.addItem('', k)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        lay.addWidget(self._lbl_type)
        lay.addSpacing(5)
        lay.addWidget(self._type_combo)
        lay.addSpacing(6)

        # Custom extension input  e.g. ".pdf, .md, .txt"
        self._ext_edit = QLineEdit()
        self._ext_edit.setPlaceholderText(tr('srch_ext_ph'))
        self._ext_edit.setFixedHeight(28)
        self._ext_edit.returnPressed.connect(self._run_search)
        self._ext_edit.textChanged.connect(self._on_ext_changed)
        lay.addWidget(self._ext_edit)
        lay.addSpacing(14)

        # Date modified
        self._lbl_date_grp = _slbl()
        self._cb_date = QCheckBox()
        self._cb_date.setStyleSheet('QCheckBox{font-size:12px;color:rgba(255,255,255,0.60);}')
        self._cb_date.toggled.connect(self._on_date_toggle)
        lay.addWidget(self._lbl_date_grp)
        lay.addSpacing(5)
        lay.addWidget(self._cb_date)
        lay.addSpacing(6)

        self._date_grid = QWidget()
        dg = QVBoxLayout(self._date_grid)
        dg.setContentsMargins(0,0,0,0)
        dg.setSpacing(5)

        self._date_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self._date_from.setCalendarPopup(True)
        self._date_from.setDisplayFormat('yyyy-MM-dd')
        self._date_from.dateChanged.connect(lambda: self._debounce.start())

        self._date_to = QDateEdit(QDate.currentDate())
        self._date_to.setCalendarPopup(True)
        self._date_to.setDisplayFormat('yyyy-MM-dd')
        self._date_to.dateChanged.connect(lambda: self._debounce.start())

        for attr, de, tr_key in [('_lbl_from', self._date_from, 'srch_from'),
                                  ('_lbl_to',   self._date_to,   'srch_to')]:
            row = QHBoxLayout(); row.setSpacing(6)
            lbl = QLabel(); lbl.setFixedWidth(50)
            lbl.setStyleSheet('color:rgba(255,255,255,0.40);font-size:11px;background:transparent;')
            setattr(self, attr, lbl)
            row.addWidget(lbl); row.addWidget(de)
            dg.addLayout(row)

        self._date_grid.setEnabled(False)
        lay.addWidget(self._date_grid)
        lay.addSpacing(14)

        # File size
        self._lbl_size_grp = _slbl()
        self._cb_size = QCheckBox()
        self._cb_size.setStyleSheet('QCheckBox{font-size:12px;color:rgba(255,255,255,0.60);}')
        self._cb_size.toggled.connect(self._on_size_toggle)
        lay.addWidget(self._lbl_size_grp)
        lay.addSpacing(5)
        lay.addWidget(self._cb_size)
        lay.addSpacing(6)

        self._size_grid = QWidget()
        sg = QVBoxLayout(self._size_grid)
        sg.setContentsMargins(0,0,0,0)
        sg.setSpacing(5)

        for l_attr, e_attr, u_attr, dflt, unit in [
            ('_lbl_min','_size_min_edit','_size_min_unit','0',  'KB'),
            ('_lbl_max','_size_max_edit','_size_max_unit','100','MB'),
        ]:
            row = QHBoxLayout(); row.setSpacing(5)
            lbl = QLabel(); lbl.setFixedWidth(32)
            lbl.setStyleSheet('color:rgba(255,255,255,0.40);font-size:11px;background:transparent;')
            setattr(self, l_attr, lbl)
            edit = QLineEdit(dflt); edit.setFixedWidth(52)
            setattr(self, e_attr, edit)
            combo = QComboBox()
            for u in ['KB','MB','GB']: combo.addItem(u)
            combo.setCurrentText(unit)
            setattr(self, u_attr, combo)
            row.addWidget(lbl); row.addWidget(edit); row.addWidget(combo)
            sg.addLayout(row)

        self._size_grid.setEnabled(False)
        lay.addWidget(self._size_grid)
        lay.addStretch()

        # Buttons
        self._btn_search = QPushButton()
        self._btn_search.setObjectName('primaryCmd')
        self._btn_search.setFixedHeight(34)
        self._btn_search.clicked.connect(self._run_search)
        lay.addWidget(self._btn_search)

        self._btn_stop = QPushButton(tr('act_stop'))
        self._btn_stop.setObjectName('dangerCmd')
        self._btn_stop.setFixedHeight(28)
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self._stop_search)
        lay.addSpacing(4)
        lay.addWidget(self._btn_stop)

        # Index button — only shown for IndexedBackend
        self._btn_index = QPushButton()
        self._btn_index.setObjectName('cmdBtn')
        self._btn_index.setFixedHeight(28)
        self._btn_index.clicked.connect(self._start_index)
        self._btn_index.setVisible(not self._engine.is_realtime)
        lay.addSpacing(4)
        lay.addWidget(self._btn_index)

        return panel

    # ── Results panel ─────────────────────────────────────────────────────────

    def _build_results_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('pageArea')
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(0)

        # Progress bar (hidden normally, shown during indexing)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(3)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            'QProgressBar{background:transparent;border:none;}'
            'QProgressBar::chunk{background:#4CC2FF;}')
        lay.addWidget(self._progress)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(True)
        lay.addWidget(vsplit, 1)

        # Table
        table_wrap = QWidget()
        table_wrap.setObjectName('pageArea')
        tw = QVBoxLayout(table_wrap)
        tw.setContentsMargins(0,0,0,0)
        tw.setSpacing(0)

        self._table = QTableWidget(0, 5)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._ctx_menu)
        self._table.doubleClicked.connect(self._open_file)
        self._table.itemSelectionChanged.connect(self._on_sel_changed)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 40)
        hdr.setDefaultSectionSize(200)
        self._table.verticalHeader().setVisible(False)
        tw.addWidget(self._table)

        # Status strip
        strip = QFrame()
        strip.setFixedHeight(28)
        strip.setStyleSheet(
            'QFrame{background:rgba(255,255,255,0.025);'
            'border-top:1px solid rgba(255,255,255,0.07);}')
        sf = QHBoxLayout(strip)
        sf.setContentsMargins(14,0,14,0)
        sf.setSpacing(0)
        _s = 'color:rgba(255,255,255,0.48);font-size:12px;background:transparent;'
        self._stat_found   = QLabel(); self._stat_found.setStyleSheet(_s)
        self._stat_time    = QLabel(); self._stat_time.setStyleSheet(_s)
        self._stat_size    = QLabel(); self._stat_size.setStyleSheet(_s)
        self._stat_sel     = QLabel(); self._stat_sel.setStyleSheet(_s)
        self._stat_backend = QLabel()
        self._stat_backend.setStyleSheet(
            'font-size:10px;font-weight:600;color:rgba(74,222,128,0.65);'
            'background:transparent;')
        sf.addWidget(self._stat_found)
        sf.addWidget(_Div())
        sf.addWidget(self._stat_time)
        sf.addWidget(_Div())
        sf.addWidget(self._stat_size)
        sf.addStretch()
        sf.addWidget(self._stat_sel)
        sf.addWidget(_Div())
        sf.addWidget(self._stat_backend)
        tw.addWidget(strip)

        vsplit.addWidget(table_wrap)
        vsplit.addWidget(self._build_preview())
        vsplit.setSizes([600, 200])

        return panel

    # ── Preview ───────────────────────────────────────────────────────────────

    def _build_preview(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName('resultsCard')
        panel.setStyleSheet(
            'QFrame#resultsCard{background:#1e1e1e;'
            'border-top:1px solid rgba(255,255,255,0.07);border-radius:0;}')
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(0)

        hdr = QFrame()
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(
            'QFrame{background:rgba(255,255,255,0.03);'
            'border-bottom:1px solid rgba(255,255,255,0.07);}')
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14,0,8,0)
        self._lbl_preview_title = QLabel()
        self._lbl_preview_title.setStyleSheet(
            'font-size:9px;font-weight:700;letter-spacing:0.8px;'
            'color:rgba(255,255,255,0.32);background:transparent;')
        hl.addWidget(self._lbl_preview_title)
        hl.addStretch()
        lay.addWidget(hdr)

        content = QWidget()
        content.setStyleSheet('background:transparent;')
        cl = QHBoxLayout(content)
        cl.setContentsMargins(14,10,14,10)
        cl.setSpacing(16)

        self._preview_img = QLabel()
        self._preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_img.setFixedSize(160,120)
        self._preview_img.setStyleSheet(
            'background:rgba(255,255,255,0.04);'
            'border:1px solid rgba(255,255,255,0.08);'
            'border-radius:4px;color:rgba(255,255,255,0.20);font-size:11px;')
        cl.addWidget(self._preview_img)

        meta = QWidget(); meta.setStyleSheet('background:transparent;')
        ml = QVBoxLayout(meta); ml.setContentsMargins(0,0,0,0); ml.setSpacing(3)

        def _row():
            k,v = QLabel(),QLabel()
            k.setStyleSheet(
                'font-size:10px;font-weight:700;color:rgba(255,255,255,0.30);'
                'background:transparent;min-width:72px;')
            v.setStyleSheet(
                'font-size:12px;color:rgba(255,255,255,0.72);background:transparent;')
            v.setWordWrap(True)
            r = QHBoxLayout(); r.setSpacing(8)
            r.addWidget(k); r.addWidget(v,1)
            ml.addLayout(r)
            return k,v

        self._mk_name,self._mv_name = _row()
        self._mk_size,self._mv_size = _row()
        self._mk_date,self._mv_date = _row()
        self._mk_type,self._mv_type = _row()
        self._mk_path,self._mv_path = _row()
        ml.addStretch()
        cl.addWidget(meta,1)
        lay.addWidget(content)
        self._clear_preview()
        return panel

    # ── Retranslate ───────────────────────────────────────────────────────────

    def retranslate(self):
        self._search_edit.setPlaceholderText(tr('srch_placeholder'))
        self._lbl_type.setText(tr('srch_grp_type'))
        self._lbl_date_grp.setText(tr('srch_grp_date'))
        self._lbl_size_grp.setText(tr('srch_grp_size'))
        self._cb_date.setText(tr('srch_date_enable'))
        self._cb_size.setText(tr('srch_size_enable'))
        self._lbl_from.setText(tr('srch_from'))
        self._lbl_to.setText(tr('srch_to'))
        self._lbl_min.setText(tr('srch_min'))
        self._lbl_max.setText(tr('srch_max'))
        self._btn_search.setText(tr('srch_search_btn'))
        self._lbl_location.setText(tr('srch_grp_location'))
        self._btn_loc_all.setText(tr('srch_loc_all'))
        self._btn_browse.setText('📁  ' + tr('srch_loc_browse'))
        self._btn_stop.setText(tr('act_stop'))
        self._btn_index.setText(
            tr('srch_reindex') if self._engine.is_indexed() else tr('srch_index_btn'))
        for i, k in enumerate(self._type_keys):
            self._type_combo.setItemText(i, tr(f'ft_{k}'))
        self._ext_edit.setPlaceholderText(tr('srch_ext_ph'))
        self._table.setHorizontalHeaderLabels([
            tr('srch_col_num'), tr('srch_col_name'),
            tr('srch_col_size'), tr('srch_col_date'), tr('srch_col_folder'),
        ])
        self._lbl_preview_title.setText(tr('srch_preview_title'))
        self._mk_name.setText('NAME'); self._mk_size.setText('SIZE')
        self._mk_date.setText('MODIFIED'); self._mk_type.setText('TYPE')
        self._mk_path.setText('PATH')
        self._refresh_idle_status()

    # ── Location ─────────────────────────────────────────────────────────────

    def _on_type_changed(self):
        # Clear custom ext when category changes so they don't conflict
        self._ext_edit.blockSignals(True)
        self._ext_edit.clear()
        self._ext_edit.blockSignals(False)
        self._debounce.start()

    def _on_ext_changed(self, text: str):
        # When user types a custom ext, reset combo to "All Files" silently
        if text.strip():
            self._type_combo.blockSignals(True)
            self._type_combo.setCurrentIndex(0)
            self._type_combo.blockSignals(False)

    def _parse_ext_input(self) -> list[str] | None:
        """Parse '.pdf, .md, txt' → ['.pdf', '.md', '.txt']. None = no filter."""
        raw = self._ext_edit.text().strip()
        if not raw:
            return None
        exts = []
        for part in raw.replace(',', ' ').split():
            e = part.lower()
            if not e.startswith('.'):
                e = '.' + e
            if e != '.':
                exts.append(e)
        return exts if exts else None

    def _set_scope(self, path: str | None):
        self._scope = path
        # Update chip states
        self._btn_loc_all.setChecked(path is None)
        for drive, btn in self._drive_btns.items():
            btn.setChecked(path == drive)
        # Show custom path label
        if path and path not in self._drive_btns:
            self._lbl_scope.setText(path)
            self._lbl_scope.setVisible(True)
        else:
            self._lbl_scope.setVisible(False)
        # For scoped searches (direct scan), only auto-trigger if text is non-empty
        # to avoid scanning an entire drive on every chip click.
        if path is None or self._search_edit.text().strip():
            self._debounce.start()

    def _browse_location(self):
        folder = QFileDialog.getExistingDirectory(
            self, tr('srch_loc_browse'), os.path.expanduser('~'))
        if folder:
            self._set_scope(folder)

    # ── Filters ───────────────────────────────────────────────────────────────

    def _on_date_toggle(self, v:bool):
        self._date_grid.setEnabled(v)
        if v: self._debounce.start()

    def _on_size_toggle(self, v:bool):
        self._size_grid.setEnabled(v)

    def _parse_size(self, edit:QLineEdit, unit:QComboBox) -> int | None:
        try:
            return int(float(edit.text()) * _UNIT_BYTES.get(unit.currentText(), 1))
        except ValueError:
            return None

    def _collect_kwargs(self) -> dict:
        kw: dict = {}
        if self._scope is not None:
            kw['scope'] = self._scope
        # Custom extension input takes priority over category combo
        custom = self._parse_ext_input()
        if custom:
            kw['ext_filter'] = custom
        else:
            type_key = self._type_combo.currentData()
            if type_key and type_key != 'All Files':
                kw['ext_filter'] = FILE_TYPE_EXTENSIONS.get(type_key)
        if self._cb_size.isChecked():
            kw['size_min'] = self._parse_size(self._size_min_edit, self._size_min_unit)
            kw['size_max'] = self._parse_size(self._size_max_edit, self._size_max_unit)
        if self._cb_date.isChecked():
            d_from = self._date_from.date()
            d_to   = self._date_to.date()
            kw['date_from'] = float(QDate(d_from.year(), d_from.month(), d_from.day()
                                         ).startOfDay().toSecsSinceEpoch())
            kw['date_to']   = float(QDate(d_to.year(), d_to.month(), d_to.day()
                                         ).endOfDay().toSecsSinceEpoch())
        return kw

    # ── Search ────────────────────────────────────────────────────────────────

    def _run_search(self):
        # A scoped search (a specific drive/folder) is a live filesystem scan
        # and works regardless of the index state. Only an "all locations"
        # search on a non-real-time backend actually needs a prior index.
        if (self._scope is None
                and not self._engine.is_realtime
                and not self._engine.is_indexed()):
            self._stat_found.setText(tr('srch_not_indexed'))
            return

        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.stop()
            self._search_worker.done.disconnect()
            self._search_worker.quit()
            self._search_worker.wait(200)

        self._btn_search.setEnabled(False)
        self._btn_stop.setVisible(bool(self._scope))  # stop only for slow scoped scans
        if self._scope:
            self._stat_found.setText(tr('srch_status_scanning') + f'  {self._scope}')
        else:
            self._stat_found.setText(tr('srch_status_scanning'))

        kw = self._collect_kwargs()
        scope = kw.pop('scope', None)

        # Reset table + streaming accumulators for the new search
        self._streaming   = scope is not None
        self._stream_size = 0
        self._begin_results()

        self._search_worker = SearchWorker(
            self._engine,
            self._search_edit.text().strip(),
            scope=scope,
            **kw,
        )
        self._search_worker.partial.connect(self._on_partial)
        self._search_worker.done.connect(self._on_results)
        self._search_worker.start()

    def _on_partial(self, batch: list, total_so_far: int):
        """A new batch of results arrived during a scoped (drive/folder) scan.
        Append it — never rebuild the whole table (avoids O(n²) re-render)."""
        self._append_records(batch)
        self._stream_size += sum(r.size for r in batch)
        self._stat_found.setText(
            tr('srch_stat_found', n=self._table.rowCount()) + f'  ({total_so_far} …)')
        self._stat_size.setText(tr('srch_stat_size', size=fmt_size(self._stream_size)))

    def _stop_search(self):
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.stop()
        self._btn_stop.setVisible(False)
        self._btn_search.setEnabled(True)
        self._finalize_table()

    def _on_results(self, records: list, total: int, elapsed_ms: int):
        self._btn_search.setEnabled(True)
        self._btn_stop.setVisible(False)

        if self._streaming:
            # Rows were already appended via _on_partial — just finalize.
            n = self._table.rowCount()
            self._finalize_table()
            self._stat_found.setText(tr('srch_stat_found', n=n))
            self._stat_size.setText(tr('srch_stat_size', size=fmt_size(self._stream_size)))
        else:
            self._begin_results()
            self._append_records(records)
            self._finalize_table()
            found = tr('srch_stat_found', n=len(records))
            if total > len(records):
                found += f'  (+{total - len(records):,})'
            self._stat_found.setText(found)
            self._stat_size.setText(
                tr('srch_stat_size', size=fmt_size(sum(r.size for r in records))))

        self._stat_time.setText(tr('srch_stat_time', ms=elapsed_ms))
        self._stat_sel.setText('')

    # ── Table building (incremental) ───────────────────────────────────────────

    def _begin_results(self):
        """Clear the table and disable sorting for fast row appends."""
        self._table.setSortingEnabled(False)
        self._table.clearContents()
        self._table.setRowCount(0)

    def _append_records(self, records: list[FileRecord]):
        """Append rows to the end of the table without touching existing ones."""
        if not records:
            return
        vp = self._table.viewport()
        vp.setUpdatesEnabled(False)
        start = self._table.rowCount()
        self._table.setRowCount(start + len(records))

        for i, rec in enumerate(records):
            r = start + i
            num = _SortItem(str(r + 1), r)
            num.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            num.setForeground(_ROW_NUM_COLOR)

            name_item = QTableWidgetItem(f'{_file_icon(rec.name)}  {rec.name}')
            name_item.setData(Qt.ItemDataRole.UserRole, rec.path)

            sz = _SortItem(fmt_size(rec.size), rec.size)
            sz.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            try:
                ds = datetime.fromtimestamp(rec.mtime).strftime('%Y-%m-%d  %H:%M')
            except (OSError, ValueError, OverflowError):
                ds = '—'
            dt = _SortItem(ds, rec.mtime)

            folder = QTableWidgetItem(os.path.dirname(rec.path))

            for item in (num, name_item, sz, dt, folder):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self._table.setItem(r, 0, num)
            self._table.setItem(r, 1, name_item)
            self._table.setItem(r, 2, sz)
            self._table.setItem(r, 3, dt)
            self._table.setItem(r, 4, folder)

        vp.setUpdatesEnabled(True)
        vp.update()

    def _finalize_table(self):
        """Re-enable sorting once after all rows are in place."""
        self._table.setSortingEnabled(True)

    # ── Index ─────────────────────────────────────────────────────────────────

    def _start_index(self):
        if self._index_worker and self._index_worker.isRunning():
            return
        # Collect roots from settings (use all local drives as fallback)
        roots = self._get_index_roots()
        self._btn_index.setEnabled(False)
        self._btn_search.setEnabled(False)
        self._progress.setVisible(True)
        self._stat_found.setText(tr('srch_status_scanning'))

        self._index_worker = IndexWorker(self._engine, roots)
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.done.connect(self._on_index_done)
        self._index_worker.start()

    def _on_index_progress(self, n: int, path: str):
        self._stat_found.setText(tr('srch_indexing_progress',
                                    folder=os.path.basename(path) or path,
                                    count=n))

    def _on_index_done(self, total: int):
        self._progress.setVisible(False)
        self._btn_index.setEnabled(True)
        self._btn_search.setEnabled(True)
        self._btn_index.setText(tr('srch_reindex'))
        self._stat_found.setText(tr('srch_index_done', count=total, folders=1))
        self._stat_backend.setText(f'⚡ {self._engine.backend_name}')

    def _get_index_roots(self) -> list[str]:
        # If a scope is selected, index just that; otherwise index every mounted
        # drive/volume (list_drives() is cross-platform: drive letters on
        # Windows, '/' + /Volumes on macOS, mount points on Linux).
        if self._scope:
            return [self._scope]
        roots = list_drives()
        return roots or [os.path.expanduser('~')]

    # ── Status ────────────────────────────────────────────────────────────────

    def _refresh_idle_status(self):
        name = self._engine.backend_name
        if self._engine.is_realtime:
            self._stat_found.setText(tr('srch_status_empty'))
            self._stat_backend.setText(f'⚡ {name}')
        elif self._engine.is_indexed():
            n = self._engine.file_count()
            self._stat_found.setText(tr('srch_status_ready', n=n))
            self._stat_backend.setText(f'⚡ {name}')
        else:
            self._stat_found.setText(tr('srch_not_indexed'))
            self._stat_backend.setText('')
        self._stat_time.setText('')
        self._stat_size.setText('')
        self._stat_sel.setText('')

    # ── Preview ───────────────────────────────────────────────────────────────

    def _clear_preview(self):
        self._preview_img.setText(tr('srch_no_preview'))
        self._preview_img.setPixmap(QPixmap())
        for v in (self._mv_name,self._mv_size,self._mv_date,self._mv_type,self._mv_path):
            v.setText('—')

    def _show_preview(self, path:str):
        name = os.path.basename(path)
        ext  = os.path.splitext(name)[1].lower()
        self._mv_name.setText(name)
        try:
            st = os.stat(path)
            self._mv_size.setText(fmt_size(st.st_size))
            self._mv_date.setText(datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d  %H:%M'))
        except OSError:
            self._mv_size.setText('—'); self._mv_date.setText('—')
        self._mv_type.setText(ext.upper().lstrip('.') or '—')
        self._mv_path.setText(os.path.dirname(path))
        if ext in _IMAGE_EXTS:
            pix = QPixmap(path)
            if not pix.isNull():
                pix = pix.scaled(self._preview_img.width(), self._preview_img.height(),
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                self._preview_img.setPixmap(pix)
                self._preview_img.setText('')
                return
        self._preview_img.setPixmap(QPixmap())
        self._preview_img.setText(_file_icon(name))

    def _on_sel_changed(self):
        sel = {i.row() for i in self._table.selectedIndexes()}
        self._stat_sel.setText(tr('srch_stat_sel', n=len(sel)))
        if len(sel) == 1:
            path = self._selected_path()
            if path: self._show_preview(path)
        else:
            self._clear_preview()

    # ── Interaction ───────────────────────────────────────────────────────────

    def _selected_path(self) -> str | None:
        row = self._table.currentRow()
        if row < 0: return None
        item = self._table.item(row, 1)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _open_file(self):
        path = self._selected_path()
        if path and os.path.exists(path): open_path(path)

    def _ctx_menu(self, _pos):
        path = self._selected_path()
        if not path: return
        menu = QMenu(self)
        av = menu.addAction(tr('ctx_view'))
        ao = menu.addAction(tr('ctx_open'))
        choice = menu.exec(QCursor.pos())
        if choice == av and os.path.exists(path): open_path(path)
        elif choice == ao: open_folder(path)
