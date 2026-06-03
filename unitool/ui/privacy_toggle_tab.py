"""
unitool/ui/privacy_toggle_tab.py
Reusable toggle tab — Check State / Apply / Revert for a category of ToggleSettings.
"""
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QFrame, QCheckBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from ..privacy_toggles import (
    ToggleSetting, TOGGLE_SETTINGS, scan_toggle_states, apply_toggles,
    os_matches, is_win11,
)
from ..translations import tr

_STATE_STYLE = {
    True:  ('color: #4CDE9A; font-size: 10px; font-weight: 600;',  lambda: tr('prv_state_applied')),
    False: ('color: rgba(255,255,255,0.30); font-size: 10px;',     lambda: tr('prv_state_not_applied')),
    None:  ('color: rgba(255,160,60,0.70); font-size: 10px;',      lambda: tr('prv_state_unknown')),
}

_VER_BADGE = {
    'win10': ('[Win 10]', 'color: #4CC2FF; font-size: 9px; font-weight: 700;'),
    'win11': ('[Win 11]', 'color: #9B8BFF; font-size: 9px; font-weight: 700;'),
}

_WIN11 = is_win11()   # evaluated once at import time


# ── Worker threads ────────────────────────────────────────────────────────────

class _ScanStateWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, settings: list[ToggleSetting], parent=None):
        super().__init__(parent)
        self._settings = settings

    def run(self):
        self.done.emit(scan_toggle_states(self._settings))


class _ApplyWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, settings: list[ToggleSetting], revert: bool, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._revert   = revert

    def run(self):
        ok, err = apply_toggles(self._settings, self._revert)
        self.done.emit(ok, err)


# ── Toggle row widget ─────────────────────────────────────────────────────────

class _ToggleRow(QFrame):
    def __init__(self, setting: ToggleSetting, parent=None):
        super().__init__(parent)
        self._setting = setting
        self.setObjectName('toggleRow')
        self.setFixedHeight(58)
        self.setStyleSheet(
            'QFrame#toggleRow { border-bottom: 1px solid rgba(255,255,255,0.05); }'
            'QFrame#toggleRow:hover { background: rgba(255,255,255,0.025); }'
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        self._cb = QCheckBox()
        self._cb.setFixedWidth(18)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        # Determine if this setting is compatible with the current Windows version
        ver = setting.win_ver
        compatible = (ver == 'both' or
                      (ver == 'win11' and _WIN11) or
                      (ver == 'win10' and not _WIN11))
        dim = 'rgba(255,255,255,0.45)' if compatible else 'rgba(255,255,255,0.22)'

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.setContentsMargins(0, 0, 0, 0)
        self._name_lbl = QLabel(setting.label)
        self._name_lbl.setStyleSheet(
            f'font-size: 12px; font-weight: 600; color: {dim};'
        )
        name_row.addWidget(self._name_lbl)

        if ver in _VER_BADGE:
            badge_text, badge_style = _VER_BADGE[ver]
            badge = QLabel(badge_text)
            badge.setStyleSheet(
                badge_style if compatible
                else 'color: rgba(150,150,150,0.45); font-size: 9px; font-weight: 700;'
            )
            name_row.addWidget(badge)
        name_row.addStretch()

        self._desc_lbl = QLabel(setting.description)
        self._desc_lbl.setStyleSheet(
            f'font-size: 10px; color: {"rgba(255,255,255,0.38)" if compatible else "rgba(255,255,255,0.18)"};'
        )
        self._desc_lbl.setWordWrap(False)
        text_col.addLayout(name_row)
        text_col.addWidget(self._desc_lbl)

        if not compatible:
            self._cb.setEnabled(False)

        self._state_lbl = QLabel()
        self._state_lbl.setFixedWidth(80)
        self._state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.refresh_state()

        lay.addWidget(self._cb)
        lay.addLayout(text_col, 1)
        lay.addWidget(self._state_lbl)

    def refresh_state(self):
        style, label_fn = _STATE_STYLE[self._setting.state]
        self._state_lbl.setStyleSheet(style)
        self._state_lbl.setText(label_fn())

    @property
    def checked(self) -> bool:
        return self._cb.isChecked()

    @property
    def setting(self) -> ToggleSetting:
        return self._setting


# ── Toggle tab ────────────────────────────────────────────────────────────────

class ToggleTab(QWidget):
    def __init__(self, category: str, parent=None):
        super().__init__(parent)
        self._category = category
        self._settings = [s for s in TOGGLE_SETTINGS
                          if s.category == category and os_matches(s.os_filter)]
        self._rows: list[_ToggleRow] = []
        self._scan_worker:  _ScanStateWorker | None = None
        self._apply_worker: _ApplyWorker     | None = None
        self._first_show = True
        self._setup_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show and self._settings:
            self._first_show = False
            self._check_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_cmd_bar())

        if not self._settings:
            layout.addWidget(self._build_no_settings_notice())
            return

        layout.addWidget(self._build_scroll_area(), 1)

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._btn_check = QPushButton(tr('prv_check_state'))
        self._btn_check.setObjectName('cmdBtn')
        self._btn_check.clicked.connect(self._check_state)

        self._btn_apply = QPushButton(tr('prv_apply_sel'))
        self._btn_apply.setObjectName('primaryCmd')
        self._btn_apply.clicked.connect(self._apply_selected)

        self._btn_revert = QPushButton(tr('prv_revert_sel'))
        self._btn_revert.setObjectName('dangerCmd')
        self._btn_revert.clicked.connect(self._revert_selected)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._btn_sel_all = QPushButton(tr('prv_select_all'))
        self._btn_sel_all.setObjectName('cmdBtn')
        self._btn_sel_all.clicked.connect(lambda: self._set_all(True))

        self._btn_desel_all = QPushButton(tr('prv_deselect_all'))
        self._btn_desel_all.setObjectName('cmdBtn')
        self._btn_desel_all.clicked.connect(lambda: self._set_all(False))

        self._status_lbl = QLabel('')
        self._status_lbl.setStyleSheet(
            'color: rgba(255,255,255,0.38); font-size: 11px; padding-left: 4px;'
        )

        lay.addWidget(self._btn_check)
        lay.addWidget(self._btn_apply)
        lay.addWidget(self._btn_revert)
        lay.addSpacing(4)
        lay.addWidget(sep)
        lay.addSpacing(4)
        lay.addWidget(self._btn_sel_all)
        lay.addWidget(self._btn_desel_all)
        lay.addSpacing(8)
        lay.addWidget(self._status_lbl)
        lay.addStretch()
        return bar

    def _build_scroll_area(self) -> QScrollArea:
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        for s in self._settings:
            row = _ToggleRow(s)
            self._rows.append(row)
            col.addWidget(row)

        col.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _build_no_settings_notice(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(tr('prv_no_settings'))
        lbl.setStyleSheet('color: rgba(255,255,255,0.35); font-size: 13px;')
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)
        return w

    # ── Actions ───────────────────────────────────────────────────────────────

    def _check_state(self):
        self._set_busy(True)
        self._status_lbl.setText(tr('prv_toggle_checking'))

        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.done.disconnect()

        self._scan_worker = _ScanStateWorker(self._settings, self)
        self._scan_worker.done.connect(self._on_state_checked)
        self._scan_worker.start()

    def _on_state_checked(self, settings: list):
        self._settings = settings
        for row in self._rows:
            row.refresh_state()
        applied = sum(1 for s in self._settings if s.state is True)
        self._status_lbl.setText(
            tr('prv_toggle_n_applied', n=applied, total=len(self._settings))
        )
        self._set_busy(False)

    def _apply_selected(self):
        sel = self._selected_settings()
        if not sel:
            QMessageBox.information(self, tr('prv_apply_title'), tr('prv_toggle_none_sel'))
            return
        reply = QMessageBox.question(
            self, tr('prv_apply_title'),
            tr('prv_apply_msg', n=len(sel)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_apply(sel, revert=False)

    def _revert_selected(self):
        sel = self._selected_settings()
        if not sel:
            QMessageBox.information(self, tr('prv_revert_title'), tr('prv_toggle_none_sel'))
            return
        reply = QMessageBox.question(
            self, tr('prv_revert_title'),
            tr('prv_revert_msg', n=len(sel)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_apply(sel, revert=True)

    def _run_apply(self, settings: list[ToggleSetting], revert: bool):
        self._set_busy(True)
        self._status_lbl.setText(
            tr('prv_toggle_reverting') if revert else tr('prv_toggle_applying')
        )
        self._apply_worker = _ApplyWorker(settings, revert, self)
        self._apply_worker.done.connect(lambda ok, err: self._on_applied(ok, err, revert))
        self._apply_worker.start()

    def _on_applied(self, ok: bool, err: str, reverted: bool):
        self._set_busy(False)
        if ok:
            for row in self._rows:
                row.refresh_state()
            self._status_lbl.setText(tr('prv_toggle_done'))
        else:
            QMessageBox.critical(self, 'Error', tr('prv_toggle_error', err=err))
            self._status_lbl.setText('')

    def _set_all(self, checked: bool):
        for row in self._rows:
            row._cb.setChecked(checked)

    def _selected_settings(self) -> list[ToggleSetting]:
        return [row.setting for row in self._rows if row.checked]

    def _set_busy(self, busy: bool):
        for btn in (self._btn_check, self._btn_apply, self._btn_revert,
                    self._btn_sel_all, self._btn_desel_all):
            btn.setEnabled(not busy)

    # ── Retranslation ─────────────────────────────────────────────────────────

    def retranslate(self):
        if sys.platform != 'win32':
            return
        self._btn_check.setText(tr('prv_check_state'))
        self._btn_apply.setText(tr('prv_apply_sel'))
        self._btn_revert.setText(tr('prv_revert_sel'))
        self._btn_sel_all.setText(tr('prv_select_all'))
        self._btn_desel_all.setText(tr('prv_deselect_all'))
        for row in self._rows:
            row.refresh_state()
