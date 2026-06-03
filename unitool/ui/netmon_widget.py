"""
unitool/ui/netmon_widget.py
Network Monitor widget — live connection table, detail panel, firewall controls.
Uses QAbstractTableModel + QSortFilterProxyModel so only changed cells repaint.
"""

from __future__ import annotations

import csv
from datetime import datetime

from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
)
from PyQt6.QtGui import QColor, QBrush, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QLineEdit, QTableView,
    QHeaderView, QFrame, QMessageBox, QFileDialog,
    QAbstractItemView, QMenu, QApplication,
)

from ..netmon import Connection, GeoWorker, NetMonWorker, FirewallManager, _ip_cache
from ..translations import tr

# ── Column indices ────────────────────────────────────────────────────────────

_COL_PROTO  = 0
_COL_LOCAL  = 1
_COL_REMOTE = 2
_COL_STATE  = 3
_COL_PROC   = 4
_COL_GEO    = 5
_COL_ORG    = 6
_COL_TIME   = 7
_NCOLS      = 8

_GEO_CONCURRENCY = 6   # max simultaneous reverse-DNS + geo threads

# ── Styles ────────────────────────────────────────────────────────────────────

_VIEW_STYLE = """
QTableView {
    background: #111111;
    alternate-background-color: #161616;
    gridline-color: rgba(255,255,255,0.05);
    border: none;
    selection-background-color: rgba(76,194,255,0.15);
    color: rgba(255,255,255,0.85);
    font-size: 12px;
}
QTableView::item {
    padding: 0 6px;
    border: none;
}
QTableView::item:selected {
    background: rgba(76,194,255,0.18);
    color: rgba(255,255,255,0.95);
}
QHeaderView::section {
    background: #1a1a1a;
    color: rgba(255,255,255,0.45);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
    padding: 4px 6px;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    border-right: 1px solid rgba(255,255,255,0.05);
}
"""


# ── Data model ────────────────────────────────────────────────────────────────

class _ConnModel(QAbstractTableModel):
    """Stores Connection objects; emits targeted dataChanged on update."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[Connection] = []

    # ── Qt overrides ─────────────────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else _NCOLS

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            _hdrs = [
                tr('nm_col_proto'), tr('nm_col_local'),   tr('nm_col_remote'),
                tr('nm_col_state'), tr('nm_col_proc'),    tr('nm_col_country'),
                tr('nm_col_org'),   tr('nm_col_since'),
            ]
            return _hdrs[section] if section < len(_hdrs) else None
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        conn = self._rows[index.row()]
        col  = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return _cell_text(conn, col)
        if role == Qt.ItemDataRole.ForegroundRole and col == _COL_STATE:
            return QColor(conn.state_color)
        if role == Qt.ItemDataRole.UserRole:        # whole Connection on any col
            return conn
        return None

    # ── Data management ──────────────────────────────────────────────────────

    def set_connections(self, conns: list[Connection]):
        surviving = {c.conn_id for c in conns}
        old_index = {c.conn_id: i for i, c in enumerate(self._rows)}

        # Remove disappeared rows (highest first to keep indices stable)
        stale = sorted([i for i, c in enumerate(self._rows)
                        if c.conn_id not in surviving], reverse=True)
        for r in stale:
            self.beginRemoveRows(QModelIndex(), r, r)
            self._rows.pop(r)
            self.endRemoveRows()

        # Rebuild index after removals
        old_index = {c.conn_id: i for i, c in enumerate(self._rows)}

        for conn in conns:
            cid = conn.conn_id
            if cid in old_index:
                r = old_index[cid]
                self._rows[r] = conn
                self.dataChanged.emit(
                    self.index(r, 0), self.index(r, _NCOLS - 1),
                    [Qt.ItemDataRole.DisplayRole],
                )
            else:
                r = len(self._rows)
                self.beginInsertRows(QModelIndex(), r, r)
                self._rows.append(conn)
                self.endInsertRows()

    def update_geo(self, ip: str, info: dict):
        for i, conn in enumerate(self._rows):
            if conn.remote_addr == ip:
                conn.remote_host         = info.get('host', '')
                conn.remote_country      = info.get('country', '')
                conn.remote_country_code = info.get('countryCode', '')
                conn.remote_city         = info.get('city', '')
                conn.remote_org          = info.get('org') or info.get('as', '')
                self.dataChanged.emit(
                    self.index(i, _COL_GEO), self.index(i, _COL_ORG),
                    [Qt.ItemDataRole.DisplayRole],
                )

    def conn_at(self, source_row: int) -> Connection | None:
        if 0 <= source_row < len(self._rows):
            return self._rows[source_row]
        return None

    def all_conns(self) -> list[Connection]:
        return list(self._rows)


# ── Proxy model (filter + sort) ───────────────────────────────────────────────

class _ConnProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._flt = ''
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_filter(self, text: str):
        self._flt = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, _parent: QModelIndex) -> bool:
        if not self._flt:
            return True
        conn: Connection | None = self.sourceModel().conn_at(source_row)
        if not conn:
            return False
        f = self._flt
        return (f in conn.process_name.lower() or
                f in conn.remote_addr.lower()   or
                f in conn.remote_host.lower()   or
                f in str(conn.remote_port)       or
                f in conn.state.lower()          or
                f in conn.remote_country.lower())

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        src = self.sourceModel()
        lc = src.conn_at(left.row())
        rc = src.conn_at(right.row())
        if lc is None or rc is None:
            return False
        return _cell_text(lc, left.column()) < _cell_text(rc, right.column())


# ── Main widget ───────────────────────────────────────────────────────────────

class NetMonWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: NetMonWorker | None = None
        self._geo_active: dict[str, GeoWorker] = {}
        self._geo_queue:  list[str]            = []
        self._live = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_cmd_bar())
        layout.addWidget(self._build_main_area())

    # ── Command bar ──────────────────────────────────────────────────────────

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._btn_live = QPushButton(tr('nm_btn_live'))
        self._btn_live.setObjectName('primaryCmd')
        self._btn_live.clicked.connect(self._toggle_live)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(tr('nm_filter_ph'))
        self._filter_edit.setFixedWidth(220)
        self._filter_edit.setFixedHeight(28)
        self._filter_edit.textChanged.connect(self._on_filter)

        self._btn_block_proc = QPushButton(tr('nm_block_proc'))
        self._btn_block_proc.setObjectName('dangerCmd')
        self._btn_block_proc.setEnabled(False)
        self._btn_block_proc.clicked.connect(self._block_process)

        self._btn_block_ip = QPushButton(tr('nm_block_ip'))
        self._btn_block_ip.setObjectName('dangerCmd')
        self._btn_block_ip.setEnabled(False)
        self._btn_block_ip.clicked.connect(self._block_ip)

        self._btn_unblock = QPushButton(tr('nm_unblock_all'))
        self._btn_unblock.setObjectName('cmdBtn')
        self._btn_unblock.clicked.connect(self._unblock_all)

        self._btn_export = QPushButton(tr('nm_export_csv'))
        self._btn_export.setObjectName('cmdBtn')
        self._btn_export.clicked.connect(self._export_csv)

        self._lbl_conns     = _StatLabel(tr('nm_stat_conns'),     '0')
        self._lbl_procs     = _StatLabel(tr('nm_stat_procs'),     '0')
        self._lbl_countries = _StatLabel(tr('nm_stat_countries'), '0')

        lay.addWidget(self._btn_live)
        lay.addSpacing(4)
        lay.addWidget(_vsep())
        lay.addSpacing(4)
        lay.addWidget(self._filter_edit)
        lay.addSpacing(4)
        lay.addWidget(_vsep())
        lay.addSpacing(4)
        lay.addWidget(self._btn_block_proc)
        lay.addWidget(self._btn_block_ip)
        lay.addWidget(self._btn_unblock)
        lay.addSpacing(4)
        lay.addWidget(_vsep())
        lay.addSpacing(4)
        lay.addWidget(self._btn_export)
        lay.addStretch()
        lay.addWidget(self._lbl_conns)
        lay.addWidget(self._lbl_procs)
        lay.addWidget(self._lbl_countries)

        return bar

    # ── Main area ────────────────────────────────────────────────────────────

    def _build_main_area(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_table())
        splitter.addWidget(self._build_detail_panel())
        splitter.setSizes([900, 320])
        return splitter

    def _build_table(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName('pageArea')
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._model = _ConnModel(self)
        self._proxy = _ConnProxy(self)
        self._proxy.setSourceModel(self._model)

        self._view = QTableView()
        self._view.setStyleSheet(_VIEW_STYLE)
        self._view.setModel(self._proxy)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setSortingEnabled(True)
        self._view.verticalHeader().setVisible(False)
        self._view.verticalHeader().setDefaultSectionSize(32)
        self._view.setShowGrid(False)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        hdr = self._view.horizontalHeader()
        hdr.setSectionResizeMode(_COL_PROTO, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_LOCAL,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_REMOTE, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_STATE,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_PROC,   QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_GEO,    QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_ORG,    QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_TIME,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.resizeSection(_COL_REMOTE, 200)
        hdr.resizeSection(_COL_PROC,   160)
        hdr.resizeSection(_COL_GEO,    140)
        hdr.resizeSection(_COL_ORG,    160)

        self._view.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._ctx_menu)

        lay.addWidget(self._view)
        return wrapper

    def _build_detail_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('detailPanel')
        panel.setStyleSheet(
            'QWidget#detailPanel {'
            '  background: #111;'
            '  border-left: 1px solid rgba(255,255,255,0.06);'
            '}'
        )
        panel.setMinimumWidth(260)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        self._detail_title_lbl = QLabel(tr('nm_detail_title'))
        self._detail_title_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.85); font-size: 13px; font-weight: 600;'
        )
        lay.addWidget(self._detail_title_lbl)
        lay.addWidget(_hsep())

        self._sect_proc_lbl = _DetailSection(tr('nm_sect_process'))
        lay.addWidget(self._sect_proc_lbl)
        self._dp_proc_name = _DetailRow(tr('nm_lbl_name'), '—')
        self._dp_proc_pid  = _DetailRow(tr('nm_lbl_pid'),  '—')
        self._dp_proc_path = _DetailRow(tr('nm_lbl_path'), '—')
        for w in (self._dp_proc_name, self._dp_proc_pid, self._dp_proc_path):
            lay.addWidget(w)

        lay.addWidget(_hsep(dim=True))

        self._sect_net_lbl = _DetailSection(tr('nm_sect_network'))
        lay.addWidget(self._sect_net_lbl)
        self._dp_proto  = _DetailRow(tr('nm_lbl_proto'),  '—')
        self._dp_local  = _DetailRow(tr('nm_lbl_local'),  '—')
        self._dp_remote = _DetailRow(tr('nm_lbl_remote'), '—')
        self._dp_state  = _DetailRow(tr('nm_lbl_state'),  '—')
        for w in (self._dp_proto, self._dp_local, self._dp_remote, self._dp_state):
            lay.addWidget(w)

        lay.addWidget(_hsep(dim=True))

        self._sect_geo_lbl = _DetailSection(tr('nm_sect_geo'))
        lay.addWidget(self._sect_geo_lbl)
        self._dp_country = _DetailRow(tr('nm_lbl_country'),  '—')
        self._dp_city    = _DetailRow(tr('nm_lbl_city'),     '—')
        self._dp_host    = _DetailRow(tr('nm_lbl_hostname'), '—')
        self._dp_org     = _DetailRow(tr('nm_lbl_org'),      '—')
        for w in (self._dp_country, self._dp_city, self._dp_host, self._dp_org):
            lay.addWidget(w)

        lay.addStretch()
        lay.addWidget(_hsep())

        self._dp_btn_block_proc = QPushButton(tr('nm_block_proc'))
        self._dp_btn_block_proc.setObjectName('dangerCmd')
        self._dp_btn_block_proc.setEnabled(False)
        self._dp_btn_block_proc.clicked.connect(self._block_process)

        self._dp_btn_block_ip = QPushButton(tr('nm_block_ip'))
        self._dp_btn_block_ip.setObjectName('dangerCmd')
        self._dp_btn_block_ip.setEnabled(False)
        self._dp_btn_block_ip.clicked.connect(self._block_ip)

        lay.addWidget(self._dp_btn_block_proc)
        lay.addWidget(self._dp_btn_block_ip)

        return panel

    # ── Live toggle ──────────────────────────────────────────────────────────

    def _toggle_live(self):
        if self._live:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        self._live = True
        self._btn_live.setText(tr('nm_btn_stop'))
        self._btn_live.setObjectName('cmdBtn')
        self._btn_live.style().unpolish(self._btn_live)
        self._btn_live.style().polish(self._btn_live)

        self._worker = NetMonWorker(interval=2.0, parent=self)
        self._worker.updated.connect(self._on_conns_updated)
        self._worker.start()

    def _stop_live(self):
        self._live = False
        self._btn_live.setText(tr('nm_btn_live'))
        self._btn_live.setObjectName('primaryCmd')
        self._btn_live.style().unpolish(self._btn_live)
        self._btn_live.style().polish(self._btn_live)

        if self._worker:
            worker = self._worker
            self._worker = None
            worker.updated.disconnect()
            worker.stop()
            worker.finished.connect(worker.deleteLater)

    # ── Connection update ────────────────────────────────────────────────────

    def _on_conns_updated(self, conns: list):
        self._model.set_connections(conns)
        self._update_stats()
        self._kick_geo(conns)

    def _update_stats(self):
        conns = self._model.all_conns()
        self._lbl_conns.set_value(str(len(conns)))
        self._lbl_procs.set_value(str(len({c.process_name for c in conns})))
        self._lbl_countries.set_value(
            str(len({c.remote_country_code for c in conns if c.remote_country_code}))
        )

    # ── Geo worker pool ──────────────────────────────────────────────────────

    def _kick_geo(self, conns: list):
        for conn in conns:
            ip = conn.remote_addr
            if ip and _ip_cache.mark_pending(ip):
                self._geo_queue.append(ip)
        self._drain_geo_queue()

    def _drain_geo_queue(self):
        while self._geo_queue and len(self._geo_active) < _GEO_CONCURRENCY:
            ip = self._geo_queue.pop(0)
            w = GeoWorker(ip, self)
            w.resolved.connect(self._on_geo_resolved)
            w.finished.connect(lambda _ip=ip: self._on_geo_done(_ip))
            self._geo_active[ip] = w
            w.start()

    def _on_geo_done(self, ip: str):
        self._geo_active.pop(ip, None)
        self._drain_geo_queue()

    def _on_geo_resolved(self, ip: str, info: dict):
        self._model.update_geo(ip, info)
        conn = self._selected_conn()
        if conn and conn.remote_addr == ip:
            conn.remote_host         = info.get('host', '')
            conn.remote_country      = info.get('country', '')
            conn.remote_country_code = info.get('countryCode', '')
            conn.remote_city         = info.get('city', '')
            conn.remote_org          = info.get('org') or info.get('as', '')
            self._populate_detail(conn)

    # ── Filter ───────────────────────────────────────────────────────────────

    def _on_filter(self, text: str):
        self._proxy.set_filter(text)
        self._update_stats()

    # ── Detail panel ─────────────────────────────────────────────────────────

    def _on_row_changed(self, current: QModelIndex, _prev: QModelIndex):
        conn = self._selected_conn()
        has  = conn is not None
        self._btn_block_proc.setEnabled(has)
        self._btn_block_ip.setEnabled(has)
        self._dp_btn_block_proc.setEnabled(has)
        self._dp_btn_block_ip.setEnabled(has)
        if conn:
            self._populate_detail(conn)

    def _populate_detail(self, conn: Connection):
        self._dp_proc_name.set_value(conn.process_name)
        self._dp_proc_pid.set_value(str(conn.pid) if conn.pid else '—')
        self._dp_proc_path.set_value(conn.process_path or '—')
        self._dp_proto.set_value(conn.protocol)
        self._dp_local.set_value(conn.local_display)
        self._dp_remote.set_value(conn.remote_display)
        self._dp_state.set_value(conn.state)
        self._dp_country.set_value(
            f'{conn.flag}  {conn.remote_country}'.strip() if conn.remote_country else '—'
        )
        self._dp_city.set_value(conn.remote_city or '—')
        self._dp_host.set_value(conn.remote_host or '—')
        self._dp_org.set_value(conn.org_display or '—')

    # ── Firewall actions ─────────────────────────────────────────────────────

    def _selected_conn(self) -> Connection | None:
        idxs = self._view.selectionModel().selectedRows()
        if not idxs:
            return None
        src = self._proxy.mapToSource(idxs[0])
        return self._model.conn_at(src.row())

    def _block_process(self):
        conn = self._selected_conn()
        if not conn:
            return
        if not conn.process_path:
            QMessageBox.warning(self, tr('nm_dlg_block_proc'), tr('nm_err_no_path'))
            return
        reply = QMessageBox.question(
            self, tr('nm_dlg_block_proc'),
            tr('nm_dlg_block_proc_msg', name=conn.process_name, path=conn.process_path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, err = FirewallManager.block_process(conn.process_path, conn.process_name)
        if ok:
            QMessageBox.information(self, tr('nm_dlg_blocked'),
                                    tr('nm_dlg_blocked_proc', name=conn.process_name))
        elif err:
            QMessageBox.critical(self, tr('nm_dlg_error'), err)

    def _block_ip(self):
        conn = self._selected_conn()
        if not conn or not conn.remote_addr:
            return
        reply = QMessageBox.question(
            self, tr('nm_dlg_block_ip'),
            tr('nm_dlg_block_ip_msg', addr=conn.remote_addr),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, err = FirewallManager.block_ip(conn.remote_addr)
        if ok:
            QMessageBox.information(self, tr('nm_dlg_blocked'),
                                    tr('nm_dlg_blocked_ip', addr=conn.remote_addr))
        elif err:
            QMessageBox.critical(self, tr('nm_dlg_error'), err)

    def _unblock_process(self, conn: Connection):
        ok, err = FirewallManager.unblock_process(conn.process_name, conn.process_path or '')
        if ok:
            QMessageBox.information(self, tr('nm_dlg_unblocked'),
                                    tr('nm_dlg_unblocked_proc', name=conn.process_name))
        elif err:
            QMessageBox.critical(self, tr('nm_dlg_error'), err)

    def _unblock_ip(self, conn: Connection):
        ok, err = FirewallManager.unblock_ip(conn.remote_addr)
        if ok:
            QMessageBox.information(self, tr('nm_dlg_unblocked'),
                                    tr('nm_dlg_unblocked_ip', addr=conn.remote_addr))
        elif err:
            QMessageBox.critical(self, tr('nm_dlg_error'), err)

    def _unblock_all(self):
        rules = FirewallManager.list_rules()
        if not rules:
            QMessageBox.information(self, tr('nm_dlg_unblock_all'), tr('nm_dlg_no_rules'))
            return
        reply = QMessageBox.question(
            self, tr('nm_dlg_unblock_all'),
            tr('nm_dlg_remove_rules', n=len(rules)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = sum(1 for r in rules if FirewallManager.remove_rule(r))
        QMessageBox.information(self, tr('nm_dlg_unblock_all'),
                                tr('nm_dlg_removed', n=removed))

    # ── Export ───────────────────────────────────────────────────────────────

    def _export_csv(self):
        conns = self._model.all_conns()
        if not conns:
            QMessageBox.information(self, tr('nm_dlg_export'), tr('nm_dlg_no_conns'))
            return
        default = f'connections_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        path, _ = QFileDialog.getSaveFileName(
            self, tr('nm_dlg_export_title'), default, 'CSV Files (*.csv)'
        )
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Protocol', 'Local', 'Remote', 'State',
                    'PID', 'Process', 'Process Path',
                    'Country', 'City', 'Hostname', 'Org', 'First Seen',
                ])
                for c in conns:
                    writer.writerow([
                        c.protocol, c.local_display, c.remote_display, c.state,
                        c.pid, c.process_name, c.process_path,
                        c.remote_country, c.remote_city, c.remote_host,
                        c.remote_org, c.first_seen,
                    ])
            QMessageBox.information(self, tr('nm_dlg_exported'),
                                    tr('nm_dlg_saved', path=path))
        except Exception as exc:
            QMessageBox.critical(self, tr('nm_dlg_error'), str(exc))

    # ── Context menu ─────────────────────────────────────────────────────────

    def _ctx_menu(self, pos):
        idx = self._view.indexAt(pos)
        if not idx.isValid():
            return
        src = self._proxy.mapToSource(idx)
        conn = self._model.conn_at(src.row())
        if not conn:
            return
        self._view.selectRow(idx.row())

        proc_blocked = FirewallManager.is_process_blocked(conn.process_name, conn.process_path or '')
        ip_blocked   = FirewallManager.is_ip_blocked(conn.remote_addr)

        menu = QMenu(self)

        if proc_blocked:
            act_proc = menu.addAction(tr('nm_ctx_unblock_proc', name=conn.process_name))
        else:
            act_proc = menu.addAction(tr('nm_ctx_block_proc',   name=conn.process_name))

        if ip_blocked:
            act_ip = menu.addAction(tr('nm_ctx_unblock_ip', addr=conn.remote_addr))
        else:
            act_ip = menu.addAction(tr('nm_ctx_block_ip',   addr=conn.remote_addr))

        menu.addSeparator()
        act_copy_ip   = menu.addAction(tr('nm_ctx_copy_ip',   addr=conn.remote_addr))
        act_copy_proc = menu.addAction(tr('nm_ctx_copy_path'))
        choice = menu.exec(QCursor.pos())

        if choice == act_proc:
            if proc_blocked:
                self._unblock_process(conn)
            else:
                self._block_process()
        elif choice == act_ip:
            if ip_blocked:
                self._unblock_ip(conn)
            else:
                self._block_ip()
        elif choice == act_copy_ip:
            QApplication.clipboard().setText(conn.remote_addr)
        elif choice == act_copy_proc:
            QApplication.clipboard().setText(conn.process_path)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def retranslate(self):
        # Command bar
        self._btn_live.setText(tr('nm_btn_stop') if self._live else tr('nm_btn_live'))
        self._filter_edit.setPlaceholderText(tr('nm_filter_ph'))
        self._btn_block_proc.setText(tr('nm_block_proc'))
        self._btn_block_ip.setText(tr('nm_block_ip'))
        self._btn_unblock.setText(tr('nm_unblock_all'))
        self._btn_export.setText(tr('nm_export_csv'))
        self._lbl_conns.set_label(tr('nm_stat_conns'))
        self._lbl_procs.set_label(tr('nm_stat_procs'))
        self._lbl_countries.set_label(tr('nm_stat_countries'))

        # Detail panel
        self._detail_title_lbl.setText(tr('nm_detail_title'))
        self._sect_proc_lbl.setText(tr('nm_sect_process'))
        self._sect_net_lbl.setText(tr('nm_sect_network'))
        self._sect_geo_lbl.setText(tr('nm_sect_geo'))
        self._dp_proc_name.set_label(tr('nm_lbl_name'))
        self._dp_proc_pid.set_label(tr('nm_lbl_pid'))
        self._dp_proc_path.set_label(tr('nm_lbl_path'))
        self._dp_proto.set_label(tr('nm_lbl_proto'))
        self._dp_local.set_label(tr('nm_lbl_local'))
        self._dp_remote.set_label(tr('nm_lbl_remote'))
        self._dp_state.set_label(tr('nm_lbl_state'))
        self._dp_country.set_label(tr('nm_lbl_country'))
        self._dp_city.set_label(tr('nm_lbl_city'))
        self._dp_host.set_label(tr('nm_lbl_hostname'))
        self._dp_org.set_label(tr('nm_lbl_org'))
        self._dp_btn_block_proc.setText(tr('nm_block_proc'))
        self._dp_btn_block_ip.setText(tr('nm_block_ip'))

        # Table column headers
        self._model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, _NCOLS - 1)

    def stop(self):
        self._stop_live()


# ── Helper widgets ────────────────────────────────────────────────────────────

class _StatLabel(QWidget):
    def __init__(self, label: str, value: str = '0', parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(1)
        self._lbl_lbl = QLabel(label.upper())
        self._lbl_lbl.setStyleSheet(
            'font-size: 9px; font-weight: 700; color: rgba(255,255,255,0.30);'
            ' letter-spacing: 0.6px;'
        )
        self._val = QLabel(value)
        self._val.setStyleSheet(
            'font-size: 15px; font-weight: 600; color: rgba(255,255,255,0.82);'
        )
        self._lbl_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._lbl_lbl)
        lay.addWidget(self._val)

    def set_value(self, v: str):
        self._val.setText(v)

    def set_label(self, text: str):
        self._lbl_lbl.setText(text.upper())


class _DetailSection(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(
            'font-size: 9px; font-weight: 700; color: rgba(255,255,255,0.28);'
            ' letter-spacing: 0.8px;'
        )


class _DetailRow(QWidget):
    def __init__(self, label: str, value: str = '—', parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(8)
        self._lbl_lbl = QLabel(label)
        self._lbl_lbl.setFixedWidth(72)
        self._lbl_lbl.setStyleSheet('color: rgba(255,255,255,0.35); font-size: 11px;')
        self._lbl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val = QLabel(value)
        self._val.setStyleSheet('color: rgba(255,255,255,0.78); font-size: 11px;')
        self._val.setWordWrap(True)
        self._val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._lbl_lbl)
        lay.addWidget(self._val, 1)

    def set_value(self, v: str):
        self._val.setText(v or '—')

    def set_label(self, text: str):
        self._lbl_lbl.setText(text)


# ── Module helpers ────────────────────────────────────────────────────────────

def _cell_text(conn: Connection, col: int) -> str:
    if col == _COL_PROTO:  return conn.protocol
    if col == _COL_LOCAL:  return conn.local_display
    if col == _COL_REMOTE: return conn.remote_display
    if col == _COL_STATE:  return f'  ●  {conn.state}'
    if col == _COL_PROC:   return conn.process_name
    if col == _COL_GEO:
        return (f'{conn.flag}  {conn.geo_display}' if conn.geo_display else conn.flag).strip()
    if col == _COL_ORG:    return conn.org_display
    if col == _COL_TIME:   return conn.first_seen
    return ''


def _vsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')
    return f


def _hsep(dim: bool = False) -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    alpha = '0.05' if dim else '0.08'
    f.setStyleSheet(f'background: rgba(255,255,255,{alpha}); max-height: 1px; border: none;')
    return f
