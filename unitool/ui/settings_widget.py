import os
import sqlite3

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QListWidget, QCheckBox, QFileDialog,
    QProgressBar, QMessageBox, QComboBox,
    QAbstractItemView, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ..translations import tr
from ..config import load as load_config, save as save_config
from ..indexer import Indexer, get_stats, init_db, DB_PATH
from ..scanner import fmt_size


class SettingsWidget(QWidget):
    index_started  = pyqtSignal()
    index_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._indexer: Indexer | None = None
        self._min_size_keys = ['any', 'gt1kb', 'gt10kb', 'gt100kb', 'gt1mb']
        self._min_size_bytes = {'any': 0, 'gt1kb': 1_024, 'gt10kb': 10_240,
                                'gt100kb': 102_400, 'gt1mb': 1_048_576}
        self._build_ui()
        self._load_settings()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        scroll.setWidget(body)

        # ── Index Locations ──
        self._grp_locations = QGroupBox()
        loc = QVBoxLayout(self._grp_locations)
        self._folder_list = QListWidget()
        self._folder_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._folder_list.setMaximumHeight(150)
        loc.addWidget(self._folder_list)
        btn_row = QHBoxLayout()
        self._btn_add    = QPushButton()
        self._btn_remove = QPushButton()
        self._btn_add.clicked.connect(self._add_folder)
        self._btn_remove.clicked.connect(self._remove_folder)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        btn_row.addStretch()
        loc.addLayout(btn_row)
        layout.addWidget(self._grp_locations)

        # ── Index Options ──
        self._grp_options = QGroupBox()
        opt = QVBoxLayout(self._grp_options)
        self._cb_hidden = QCheckBox()
        self._cb_system = QCheckBox()
        self._cb_hidden.setChecked(True)
        self._cb_system.setChecked(True)
        size_row = QHBoxLayout()
        self._lbl_minsize  = QLabel()
        self._cmb_minsize  = QComboBox()
        for k in self._min_size_keys:
            self._cmb_minsize.addItem('', k)
        size_row.addWidget(self._lbl_minsize)
        size_row.addWidget(self._cmb_minsize)
        size_row.addStretch()
        opt.addWidget(self._cb_hidden)
        opt.addWidget(self._cb_system)
        opt.addLayout(size_row)
        layout.addWidget(self._grp_options)

        # ── Index Statistics ──
        self._grp_stats = QGroupBox()
        stats = QVBoxLayout(self._grp_stats)
        self._lbl_files   = QLabel()
        self._lbl_locs    = QLabel()
        self._lbl_dbsize  = QLabel()
        self._lbl_last    = QLabel()
        for lbl in (self._lbl_files, self._lbl_locs,
                    self._lbl_dbsize, self._lbl_last):
            lbl.setStyleSheet('color: rgba(255,255,255,0.50); font-size: 12px;')
            stats.addWidget(lbl)
        layout.addWidget(self._grp_stats)

        # ── Action buttons ──
        act_row = QHBoxLayout()
        self._btn_index = QPushButton()
        self._btn_stop  = QPushButton()
        self._btn_clear = QPushButton()
        self._btn_stop.setEnabled(False)
        self._btn_index.setStyleSheet(
            'padding: 7px 20px; font-weight: 600; background: #4CC2FF; '
            'color: #000000; border: none; border-radius: 6px;')
        self._btn_clear.setStyleSheet(
            'padding: 6px 14px; color: #FF7070; '
            'border: 1px solid rgba(255,80,80,0.30); border-radius: 6px;')
        self._btn_index.clicked.connect(self.trigger_index)
        self._btn_stop.clicked.connect(self._stop_index)
        self._btn_clear.clicked.connect(self._clear_index)
        act_row.addWidget(self._btn_index)
        act_row.addWidget(self._btn_stop)
        act_row.addStretch()
        act_row.addWidget(self._btn_clear)
        layout.addLayout(act_row)

        # ── Progress + status ──
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(14)
        self._progress.setVisible(False)
        self._status_lbl = QLabel()
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._progress)
        layout.addWidget(self._status_lbl)

        layout.addStretch()
        self.retranslate()

    # ── Public ───────────────────────────────────────────────────────────────

    def retranslate(self):
        self._grp_locations.setTitle(tr('set_grp_locations'))
        self._grp_options.setTitle(tr('set_grp_options'))
        self._grp_stats.setTitle(tr('set_grp_stats'))
        self._btn_add.setText(tr('btn_add'))
        self._btn_remove.setText(tr('btn_remove'))
        self._cb_hidden.setText(tr('set_exclude_hidden'))
        self._cb_system.setText(tr('set_exclude_system'))
        self._lbl_minsize.setText(tr('set_min_size_lbl'))
        for i, k in enumerate(self._min_size_keys):
            self._cmb_minsize.setItemText(i, tr(f'set_minsize_{k}'))
        self._btn_index.setText(tr('set_index_now'))
        self._btn_stop.setText(tr('act_stop'))
        self._btn_clear.setText(tr('set_clear_index'))
        self.refresh_stats()

    def refresh_stats(self):
        stats = get_stats()
        self._lbl_files.setText(tr('set_stat_files', n=f"{stats['count']:,}"))
        self._lbl_locs.setText(tr('set_stat_folders', n=f"{stats['folders']:,}"))
        try:
            sz = os.path.getsize(DB_PATH)
            self._lbl_dbsize.setText(tr('set_stat_dbsize', size=fmt_size(sz)))
        except OSError:
            self._lbl_dbsize.setText(tr('set_stat_dbsize', size='—'))
        when = stats.get('last_indexed') or '—'
        self._lbl_last.setText(tr('set_stat_last', when=when))

    def get_options(self) -> dict:
        return {
            'exclude_hidden': self._cb_hidden.isChecked(),
            'exclude_system': self._cb_system.isChecked(),
            'min_size': self._min_size_bytes.get(
                self._cmb_minsize.currentData(), 0),
        }

    def trigger_index(self):
        folders = [self._folder_list.item(i).text()
                   for i in range(self._folder_list.count())]
        if not folders:
            self._status_lbl.setText(tr('srch_no_folders'))
            return
        if self._indexer and self._indexer.isRunning():
            return
        opts = self.get_options()
        self._indexer = Indexer(
            folders,
            exclude_hidden=opts['exclude_hidden'],
            exclude_system=opts['exclude_system'],
            min_size=opts['min_size'],
        )
        self._indexer.progress.connect(self._on_progress)
        self._indexer.finished.connect(self._on_finished)
        self._indexer.error_occurred.connect(self._on_error)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._btn_index.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self.index_started.emit()
        self._indexer.start()

    # ── Private ───────────────────────────────────────────────────────────────

    def _load_settings(self):
        cfg = load_config()
        for f in cfg.get('index_folders', []):
            if os.path.isdir(f):
                self._folder_list.addItem(f)
        self._cb_hidden.setChecked(cfg.get('index_exclude_hidden', True))
        self._cb_system.setChecked(cfg.get('index_exclude_system', True))
        key = cfg.get('index_min_size_key', 'any')
        idx = self._min_size_keys.index(key) if key in self._min_size_keys else 0
        self._cmb_minsize.setCurrentIndex(idx)

    def _save_settings(self):
        cfg = load_config()
        cfg['index_folders'] = [
            self._folder_list.item(i).text()
            for i in range(self._folder_list.count())]
        cfg['index_exclude_hidden'] = self._cb_hidden.isChecked()
        cfg['index_exclude_system'] = self._cb_system.isChecked()
        cfg['index_min_size_key']   = self._cmb_minsize.currentData()
        save_config(cfg)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr('set_grp_locations'), '')
        if not folder:
            return
        existing = {self._folder_list.item(i).text()
                    for i in range(self._folder_list.count())}
        if folder not in existing:
            self._folder_list.addItem(folder)
            self._save_settings()

    def _remove_folder(self):
        for item in self._folder_list.selectedItems():
            self._folder_list.takeItem(self._folder_list.row(item))
        self._save_settings()

    def _stop_index(self):
        if self._indexer:
            self._indexer.stop()
        self._progress.setVisible(False)
        self._btn_index.setEnabled(True)
        self._btn_stop.setEnabled(False)

    def _on_progress(self, pct: int, msg: str):
        self._progress.setValue(pct)
        self._status_lbl.setText(msg)

    def _on_finished(self, total: int, folders: int):
        self._progress.setVisible(False)
        self._btn_index.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status_lbl.setText(
            tr('srch_index_done', count=total, folders=folders))
        self.refresh_stats()
        self.index_finished.emit()

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._btn_index.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status_lbl.setText(f'Error: {msg}')

    def _clear_index(self):
        reply = QMessageBox.question(
            self, tr('set_clear_title'), tr('set_clear_msg'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            init_db(DB_PATH)
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM meta")
            conn.commit()
            conn.close()
        except Exception:
            pass
        self.refresh_stats()
        self._status_lbl.setText(tr('set_index_cleared'))
        self.index_finished.emit()
