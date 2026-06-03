"""
unitool/ui/privacy_ram_tab.py
RAM Cleaner sub-tab.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QProgressBar,
)
from PyQt6.QtCore import QThread, pyqtSignal

from ..privacy import get_ram_info, clean_ram, fmt_size
from ..translations import tr


class _RamCleanWorker(QThread):
    done = pyqtSignal(int, str)   # bytes_freed, error

    def run(self):
        freed, err = clean_ram()
        self.done.emit(freed, err)


class RamTab(QWidget):
    def __init__(self, platform_info: dict, parent=None):
        super().__init__(parent)
        self._platform_info = platform_info
        self._ram_clean_worker: _RamCleanWorker | None = None
        self._setup_ui()
        self._refresh_ram()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(0)

        self._title_lbl = QLabel(tr('prv_ram_title'))
        self._title_lbl.setStyleSheet(
            'font-size: 14px; font-weight: 600; color: rgba(255,255,255,0.85);'
        )
        lay.addWidget(self._title_lbl)
        lay.addSpacing(20)

        self._ram_bar = QProgressBar()
        self._ram_bar.setRange(0, 100)
        self._ram_bar.setFixedHeight(18)
        self._ram_bar.setTextVisible(True)
        self._ram_bar.setStyleSheet(
            'QProgressBar { border-radius: 4px; background: rgba(255,255,255,0.08); '
            'color: rgba(255,255,255,0.8); font-size: 11px; }'
            'QProgressBar::chunk { background: #4CC2FF; border-radius: 4px; }'
        )
        lay.addWidget(self._ram_bar)
        lay.addSpacing(14)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(6)

        lbl_style = 'color: rgba(255,255,255,0.40); font-size: 12px;'
        val_style = 'color: rgba(255,255,255,0.80); font-size: 12px; font-weight: 600;'

        self._ram_total_lbl = QLabel('—')
        self._ram_used_lbl  = QLabel('—')
        self._ram_free_lbl  = QLabel('—')
        self._lbl_total = QLabel(tr('prv_ram_total'))
        self._lbl_used  = QLabel(tr('prv_ram_used'))
        self._lbl_free  = QLabel(tr('prv_ram_free'))

        for row, (lbl_w, val_w) in enumerate([
            (self._lbl_total, self._ram_total_lbl),
            (self._lbl_used,  self._ram_used_lbl),
            (self._lbl_free,  self._ram_free_lbl),
        ]):
            lbl_w.setStyleSheet(lbl_style)
            val_w.setStyleSheet(val_style)
            grid.addWidget(lbl_w, row, 0)
            grid.addWidget(val_w, row, 1)

        lay.addLayout(grid)
        lay.addSpacing(24)

        btn_row = QHBoxLayout()
        self._btn_refresh = QPushButton(tr('prv_ram_refresh'))
        self._btn_refresh.setObjectName('cmdBtn')
        self._btn_refresh.clicked.connect(self._refresh_ram)

        self._btn_clean = QPushButton(tr('prv_ram_clean'))
        self._btn_clean.setObjectName('primaryCmd')
        self._btn_clean.clicked.connect(self._do_clean)

        btn_row.addWidget(self._btn_refresh)
        btn_row.addWidget(self._btn_clean)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addSpacing(12)

        self._freed_lbl = QLabel('')
        self._freed_lbl.setStyleSheet('color: #4DDB8A; font-size: 12px; font-weight: 600;')
        lay.addWidget(self._freed_lbl)

        if not self._platform_info.get('is_admin'):
            lay.addSpacing(12)
            note = QLabel(tr('prv_ram_admin_note'))
            note.setWordWrap(True)
            note.setStyleSheet('color: rgba(255,180,60,0.75); font-size: 11px;')
            lay.addWidget(note)

        lay.addStretch()

    def _refresh_ram(self):
        info = get_ram_info()
        t, u, a, pct = info['total'], info['used'], info['available'], info['percent']
        self._ram_bar.setValue(int(pct))
        self._ram_bar.setFormat(f'{pct}%  used')
        self._ram_total_lbl.setText(fmt_size(t))
        self._ram_used_lbl.setText(fmt_size(u))
        self._ram_free_lbl.setText(fmt_size(a))
        self._freed_lbl.setText('')

    def _do_clean(self):
        self._btn_clean.setEnabled(False)
        self._btn_refresh.setEnabled(False)
        self._freed_lbl.setText(tr('prv_ram_cleaning'))
        self._freed_lbl.setStyleSheet('color: rgba(255,255,255,0.45); font-size: 12px;')

        if self._ram_clean_worker and self._ram_clean_worker.isRunning():
            return
        self._ram_clean_worker = _RamCleanWorker(self)
        self._ram_clean_worker.done.connect(self._on_cleaned)
        self._ram_clean_worker.start()

    def _on_cleaned(self, freed: int, error: str):
        self._btn_clean.setEnabled(True)
        self._btn_refresh.setEnabled(True)
        self._refresh_ram()
        if error:
            self._freed_lbl.setText(f'Error: {error}')
            self._freed_lbl.setStyleSheet('color: #FF6060; font-size: 12px;')
        else:
            msg = tr('prv_ram_freed', size=fmt_size(freed)) if freed else tr('prv_ram_already_clean')
            self._freed_lbl.setText(msg)
            self._freed_lbl.setStyleSheet('color: #4DDB8A; font-size: 12px; font-weight: 600;')

    def retranslate(self):
        self._title_lbl.setText(tr('prv_ram_title'))
        self._lbl_total.setText(tr('prv_ram_total'))
        self._lbl_used.setText(tr('prv_ram_used'))
        self._lbl_free.setText(tr('prv_ram_free'))
        self._btn_refresh.setText(tr('prv_ram_refresh'))
        self._btn_clean.setText(tr('prv_ram_clean'))
