"""
unitool/ui/dpi_widget.py
Internet Freedom tab — ProtonVPN-inspired UI for connection protection.
"""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QCheckBox, QScrollArea, QPlainTextEdit, QButtonGroup,
    QRadioButton, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QThread

from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QLinearGradient

from ..translations import tr
from ..dpi import (
    DpiEngine, DpiProfile, Backend, default_backend,
    goodbyedpi_available, download_goodbyedpi,
)

# ── Background workers ───────────────────────────────────────────────────────

class _StartWorker(QThread):
    done = pyqtSignal(bool, str)   # ok, error

    def __init__(self, engine: DpiEngine, profile: DpiProfile, parent=None):
        super().__init__(parent)
        self._engine  = engine
        self._profile = profile

    def run(self):
        ok, err = self._engine.start(self._profile)
        self.done.emit(ok, err)


class _StopWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, engine: DpiEngine, parent=None):
        super().__init__(parent)
        self._engine = engine

    def run(self):
        ok, err = self._engine.stop()
        self.done.emit(ok, err)


class _DownloadWorker(QThread):
    progress = pyqtSignal(int, str)   # pct (-1=indeterminate), message
    done     = pyqtSignal(bool, str)

    def run(self):
        def cb(pct, msg):
            self.progress.emit(pct, msg)
        ok, err = download_goodbyedpi(progress_cb=cb)
        self.done.emit(ok, err)


# ── ProtonVPN-inspired palette ────────────────────────────────────────────────
_BG_HERO   = '#16151F'   # deepest — left panel
_BG_CARD   = '#1E1D2A'   # card surfaces
_BG_INPUT  = '#252434'   # inputs / inner rows
_BG_HOVER  = '#2B2A3D'
_BG_ACT    = '#2E2C45'

_COL_GREEN = '#4ADE80'
_COL_RED   = '#F87171'
_COL_PURP  = '#7C6FE0'

_T1 = 'rgba(255,255,255,0.90)'
_T2 = 'rgba(255,255,255,0.45)'
_T3 = 'rgba(255,255,255,0.22)'
_DIV = 'rgba(255,255,255,0.06)'

_R  = '8px'
_R2 = '12px'

# ── Pill toggle ───────────────────────────────────────────────────────────────
_PILL = (
    'QCheckBox { spacing: 0; }'
    'QCheckBox::indicator {'
    '  width: 38px; height: 21px; border-radius: 10px;'
    f' background: {_BG_HOVER};'
    '}'
    f'QCheckBox::indicator:checked {{ background: {_COL_GREEN}; }}'
    f'QCheckBox::indicator:disabled {{ background: rgba(255,255,255,0.06); }}'
)

# ── Preset data ───────────────────────────────────────────────────────────────
_PRESETS: dict[str, set[str]] = {
    'dpi_mode_1': {'passive', 'frag_https'},
    'dpi_mode_2': {'passive', 'frag_https', 'frag_http', 'host_mixedcase'},
    'dpi_mode_3': {'passive', 'frag_https', 'frag_http', 'fake', 'host_mixedcase'},
    'dpi_mode_4': {'passive', 'frag_https', 'frag_http', 'fake',
                   'wrong_seq', 'host_mixedcase', 'host_removespace'},
    'dpi_mode_5': {'passive', 'frag_https', 'frag_http', 'fake', 'wrong_chksum',
                   'wrong_seq', 'native_frag', 'host_mixedcase', 'host_removespace'},
}
_PRESET_KEYS = ['dpi_mode_1','dpi_mode_2','dpi_mode_3','dpi_mode_4','dpi_mode_5','dpi_mode_custom']
_PRESET_NUM  = {'dpi_mode_1':'1','dpi_mode_2':'2','dpi_mode_3':'3',
                'dpi_mode_4':'4','dpi_mode_5':'5','dpi_mode_custom':'·'}

# ── Feature tile (ProtonVPN feature grid style) ───────────────────────────────
class _FeatureTile(QFrame):
    toggled = pyqtSignal(bool)

    def __init__(self, icon: str, title_key: str, parent=None):
        super().__init__(parent)
        self._tk = title_key
        self.setStyleSheet(
            f'QFrame {{ background: {_BG_INPUT}; border-radius: {_R}; }}'
            f'QFrame:hover {{ background: {_BG_HOVER}; }}'
        )
        self.setFixedHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(0)
        self._icon = QLabel(icon)
        self._icon.setStyleSheet(f'font-size: 15px; color: {_T2};')
        self._cb = QCheckBox()
        self._cb.setStyleSheet(_PILL)
        self._cb.toggled.connect(self._on_toggled)
        top.addWidget(self._icon)
        top.addStretch()
        top.addWidget(self._cb)

        self._name = QLabel(tr(title_key))
        self._name.setStyleSheet(
            f'font-size: 11px; font-weight: 500; color: {_T2};')
        self._name.setWordWrap(True)

        lay.addLayout(top)
        lay.addWidget(self._name)

    def _on_toggled(self, v: bool):
        self._name.setStyleSheet(
            f'font-size: 11px; font-weight: {"600" if v else "500"};'
            f' color: {_T1 if v else _T2};')
        self._icon.setStyleSheet(
            f'font-size: 15px; color: {_COL_GREEN if v else _T2};')
        self.toggled.emit(v)

    def is_checked(self) -> bool: return self._cb.isChecked()
    def set_checked(self, v: bool):
        self._cb.blockSignals(True)
        self._cb.setChecked(v)
        self._cb.blockSignals(False)
        self._on_toggled(v)

    def retranslate(self):
        self._name.setText(tr(self._tk))


# ── Preset tab (inside segmented bar) ─────────────────────────────────────────
class _PresetTab(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(1)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._n = QLabel(_PRESET_NUM[key])
        self._n.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._s = QLabel()
        self._s.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._n)
        lay.addWidget(self._s)
        self._refresh_label()
        self.set_active(False)

    def _refresh_label(self):
        full = tr(self._key)
        short = (full.split('—',1)[1].strip() if '—' in full
                 else full.split('·',1)[1].strip() if '·' in full
                 else full)
        self._s.setText(short)

    def set_active(self, v: bool):
        if v:
            self.setStyleSheet(
                f'QWidget {{ background: {_BG_ACT}; border-radius: {_R}; }}'
            )
            self._n.setStyleSheet(
                f'font-size: 13px; font-weight: 700; color: {_COL_GREEN};'
                ' background: transparent;')
            self._s.setStyleSheet(
                f'font-size: 9px; font-weight: 500; color: rgba(74,222,128,0.70);'
                ' background: transparent;')
        else:
            self.setStyleSheet(
                'QWidget { background: transparent; border-radius: 8px; }'
                f'QWidget:hover {{ background: {_BG_HOVER}; }}'
            )
            self._n.setStyleSheet(
                f'font-size: 13px; font-weight: 600; color: {_T3};'
                ' background: transparent;')
            self._s.setStyleSheet(
                f'font-size: 9px; color: rgba(255,255,255,0.18);'
                ' background: transparent;')

    def retranslate(self): self._refresh_label()
    def mousePressEvent(self, _): self.clicked.emit(self._key)


class _PresetBar(QFrame):
    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f'QFrame {{ background: {_BG_INPUT}; border-radius: {_R}; }}'
        )
        self.setFixedHeight(56)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 4, 4, 4)
        self._lay.setSpacing(2)
        self._tabs: dict[str, _PresetTab] = {}

    def add(self, key: str):
        t = _PresetTab(key)
        t.clicked.connect(self._click)
        self._tabs[key] = t
        self._lay.addWidget(t)

    def _click(self, key: str):
        self.select(key)
        self.changed.emit(key)

    def select(self, key: str):
        for k, t in self._tabs.items():
            t.set_active(k == key)
        self._active = key

    def active(self) -> str:
        return getattr(self, '_active', _PRESET_KEYS[2])

    def retranslate(self):
        for t in self._tabs.values():
            t.retranslate()


# ── Power button ──────────────────────────────────────────────────────────────
class _PowerBtn(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = False
        self.setFixedSize(136, 136)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_on(self, v: bool):
        self._on = v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = cy = 68

        if self._on:
            # outer glow
            g = QColor(74, 222, 128, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(cx-64, cy-64, 128, 128)
            # mid ring
            p.setPen(QPen(QColor(74,222,128,40), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx-52, cy-52, 104, 104)
            # main circle
            p.setPen(QPen(QColor(_COL_GREEN), 2.5))
            p.setBrush(QBrush(QColor(74,222,128,20)))
        else:
            p.setPen(QPen(QColor(255,255,255,45), 2))
            p.setBrush(QBrush(QColor(255,255,255,8)))

        p.drawEllipse(cx-40, cy-40, 80, 80)

        f = QFont()
        f.setPixelSize(26)
        p.setFont(f)
        p.setPen(QColor(_COL_GREEN) if self._on else QColor(255,255,255,90))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '⏻')
        p.end()

    def mousePressEvent(self, _): self.clicked.emit()
    def sizeHint(self): return QSize(136, 136)


# ── DNS row ───────────────────────────────────────────────────────────────────
class _DnsRow(QWidget):
    selected = pyqtSignal(str)

    def __init__(self, name: str, ip: str, parent=None):
        super().__init__(parent)
        self._ip = ip
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._dot = QLabel('●')
        self._dot.setFixedWidth(14)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name = QLabel(name)
        self._ip_lbl = QLabel(ip)
        self._ip_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._ip_lbl.setStyleSheet(
            f'font-size: 10px; font-family: Consolas,monospace; color: {_T3};')
        lay.addWidget(self._dot)
        lay.addWidget(self._name, 1)
        lay.addWidget(self._ip_lbl)
        self.set_active(False)

    def set_active(self, v: bool):
        self._dot.setStyleSheet(
            f'font-size: 7px; color: {_COL_GREEN if v else _T3};')
        self._name.setStyleSheet(
            f'font-size: 12px; font-weight: {"600" if v else "400"};'
            f' color: {_T1 if v else _T2};')

    def mousePressEvent(self, _): self.selected.emit(self._ip)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _div() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f'background: {_DIV}; border: none;')
    return f

def _sect(key: str) -> QLabel:
    l = QLabel(tr(key))
    l.setProperty('trkey', key)
    l.setStyleSheet(
        f'font-size: 10px; font-weight: 600; color: {_T3}; letter-spacing: 0.5px;')
    return l


# ── Main widget ───────────────────────────────────────────────────────────────
class DpiWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected       = False
        self._active_dns      = '1.1.1.1'
        self._suppress_custom = False
        self._tech: dict[str, _FeatureTile] = {}
        self._dns_rows: dict[str, _DnsRow]  = {}
        self._engine   = DpiEngine()
        self._worker:  QThread | None = None
        self._setup_ui()
        self._preset_bar.select('dpi_mode_3')
        self._apply_preset_techniques('dpi_mode_3')

    # ── Build ─────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        body = QWidget()
        body.setObjectName('pageArea')
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self._build_hero())
        bl.addWidget(self._build_panel(), 1)
        root.addWidget(body, 1)

    # ── Top bar ───────────────────────────────────────────────────────────────

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(10)

        self._title_lbl = QLabel(tr('tab_dpi'))
        self._title_lbl.setStyleSheet(
            f'font-size: 13px; font-weight: 700; color: {_T1};')

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet(f'background: {_DIV}; border: none;')

        self._sub_lbl = QLabel(tr('dpi_cmd_subtitle'))
        self._sub_lbl.setStyleSheet(f'font-size: 11px; color: {_T3};')

        lay.addWidget(self._title_lbl)
        lay.addWidget(sep)
        lay.addWidget(self._sub_lbl)
        lay.addStretch()

        self._chip = QLabel()
        self._chip.setFixedHeight(26)
        lay.addWidget(self._chip)
        self._refresh_chip()
        return bar

    # ── Hero ──────────────────────────────────────────────────────────────────

    def _build_hero(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName('dpiHero')
        panel.setFixedWidth(268)
        panel.setStyleSheet(
            f'QFrame#dpiHero {{ background: {_BG_HERO};'
            f' border-right: 1px solid {_DIV}; }}'
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(24, 0, 24, 24)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        lay.addStretch()

        self._power_btn = _PowerBtn()
        self._power_btn.clicked.connect(self._toggle)
        lay.addWidget(self._power_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        lay.addSpacing(14)

        self._state_lbl = QLabel()
        self._state_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._state_lbl)

        lay.addSpacing(5)

        self._state_sub = QLabel()
        self._state_sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._state_sub.setWordWrap(True)
        self._state_sub.setStyleSheet(f'font-size: 10px; color: {_T3};')
        lay.addWidget(self._state_sub)

        lay.addStretch()
        lay.addWidget(_div())
        lay.addSpacing(16)

        # ── Mode + DNS info ───────────────────────────────────────────────────
        info = QGridLayout()
        info.setHorizontalSpacing(8)
        info.setVerticalSpacing(10)

        self._sum_mode_k = QLabel(tr('dpi_sum_mode'))
        self._sum_dns_k  = QLabel(tr('dpi_sum_dns'))
        for w in (self._sum_mode_k, self._sum_dns_k):
            w.setStyleSheet(f'font-size: 10px; color: {_T3};')

        self._sum_mode_v = QLabel('—')
        self._sum_dns_v  = QLabel('—')
        for w in (self._sum_mode_v, self._sum_dns_v):
            w.setStyleSheet(
                f'font-size: 11px; font-weight: 600; color: {_T1};')
            w.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        info.addWidget(self._sum_mode_k, 0, 0)
        info.addWidget(self._sum_mode_v, 0, 1)
        info.addWidget(self._sum_dns_k,  1, 0)
        info.addWidget(self._sum_dns_v,  1, 1)
        info.setColumnStretch(1, 1)
        lay.addLayout(info)

        self._refresh_hero()
        return panel

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_panel(self) -> QWidget:
        wrap = QWidget()
        vl = QVBoxLayout(wrap)
        vl.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(24, 22, 24, 22)
        col.setSpacing(20)

        col.addWidget(self._build_presets())
        col.addWidget(self._build_features())
        col.addWidget(self._build_dns())
        col.addWidget(self._build_domains())
        col.addWidget(self._build_note())
        col.addStretch()

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(inner)
        vl.addWidget(sc)
        return wrap

    # ── Preset section ────────────────────────────────────────────────────────

    def _build_presets(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._sec_mode = _sect('dpi_sect_mode')
        lay.addWidget(self._sec_mode)
        self._preset_bar = _PresetBar()
        for k in _PRESET_KEYS:
            self._preset_bar.add(k)
        self._preset_bar.changed.connect(self._on_preset)
        lay.addWidget(self._preset_bar)
        return w

    # ── Feature tile grid ─────────────────────────────────────────────────────

    def _build_features(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._sec_tech = _sect('dpi_sect_tech')
        lay.addWidget(self._sec_tech)

        grid = QGridLayout()
        grid.setSpacing(8)

        tech_defs = [
            ('🛡', 'passive',          'dpi_t_passive'),
            ('✂', 'frag_https',       'dpi_t_frag_https'),
            ('📡', 'frag_http',        'dpi_t_frag_http'),
            ('👻', 'fake',             'dpi_t_fake'),
            ('⚡', 'wrong_chksum',     'dpi_t_wrong_chksum'),
            ('↯',  'wrong_seq',        'dpi_t_wrong_seq'),
            ('📦', 'native_frag',      'dpi_t_native_frag'),
            ('Aa', 'host_mixedcase',   'dpi_t_host_mixed'),
            ('·',  'host_removespace', 'dpi_t_host_space'),
        ]
        for i, (icon, attr, tk) in enumerate(tech_defs):
            tile = _FeatureTile(icon, tk)
            tile.toggled.connect(self._on_tech_toggled)
            self._tech[attr] = tile
            grid.addWidget(tile, i // 3, i % 3)

        lay.addLayout(grid)
        return w

    # ── DNS section ───────────────────────────────────────────────────────────

    def _build_dns(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._sec_dns = _sect('dpi_sect_dns')
        lay.addWidget(self._sec_dns)

        card = QFrame()
        card.setStyleSheet(
            f'QFrame {{ background: {_BG_CARD}; border-radius: {_R2}; }}'
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 6, 16, 6)
        cl.setSpacing(0)

        # redirect toggle row
        en_row = QHBoxLayout()
        en_txt = QVBoxLayout()
        en_txt.setSpacing(2)
        self._dns_en_lbl  = QLabel(tr('dpi_dns_enable'))
        self._dns_en_lbl.setStyleSheet(
            f'font-size: 12px; font-weight: 500; color: {_T1};')
        self._dns_en_desc = QLabel(tr('dpi_dns_enable_d'))
        self._dns_en_desc.setStyleSheet(f'font-size: 10px; color: {_T2};')
        en_txt.addWidget(self._dns_en_lbl)
        en_txt.addWidget(self._dns_en_desc)
        self._dns_en_cb = QCheckBox()
        self._dns_en_cb.setStyleSheet(_PILL)
        self._dns_en_cb.setChecked(True)
        self._dns_en_cb.toggled.connect(self._on_dns_toggle)
        en_row.addLayout(en_txt, 1)
        en_row.addWidget(self._dns_en_cb, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addSpacing(8)
        cl.addLayout(en_row)
        cl.addSpacing(8)
        cl.addWidget(_div())

        # DNS provider list
        providers = [
            ('Cloudflare', '1.1.1.1'),
            ('Google',     '8.8.8.8'),
            ('Quad9',      '9.9.9.9'),
            ('AdGuard',    '94.140.14.14'),
        ]
        for name, ip in providers:
            row = _DnsRow(name, ip)
            row.selected.connect(self._on_dns_select)
            self._dns_rows[ip] = row
            cl.addWidget(row)
        self._on_dns_select('1.1.1.1')
        cl.addWidget(_div())
        cl.addSpacing(8)

        # DoH toggle
        doh_row = QHBoxLayout()
        doh_txt = QVBoxLayout()
        doh_txt.setSpacing(2)
        self._doh_lbl  = QLabel(tr('dpi_doh'))
        self._doh_lbl.setStyleSheet(
            f'font-size: 12px; font-weight: 500; color: {_T1};')
        self._doh_desc = QLabel(tr('dpi_doh_d'))
        self._doh_desc.setStyleSheet(f'font-size: 10px; color: {_T2};')
        doh_txt.addWidget(self._doh_lbl)
        doh_txt.addWidget(self._doh_desc)
        self._doh_cb = QCheckBox()
        self._doh_cb.setStyleSheet(_PILL)
        self._doh_cb.setChecked(True)
        doh_row.addLayout(doh_txt, 1)
        doh_row.addWidget(self._doh_cb, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addLayout(doh_row)
        cl.addSpacing(8)

        lay.addWidget(card)
        return w

    # ── Domain filter ─────────────────────────────────────────────────────────

    def _build_domains(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._sec_dom = _sect('dpi_sect_domains')
        lay.addWidget(self._sec_dom)

        self._dom_grp = QButtonGroup(self)
        self._rb_all  = QRadioButton(tr('dpi_dom_all'))
        self._rb_list = QRadioButton(tr('dpi_dom_list'))
        self._rb_all.setChecked(True)
        for rb in (self._rb_all, self._rb_list):
            rb.setStyleSheet(f'font-size: 12px; color: {_T1};')
            self._dom_grp.addButton(rb)
        self._rb_list.toggled.connect(lambda v: self._dom_edit.setEnabled(v))

        rb_row = QHBoxLayout()
        rb_row.addWidget(self._rb_all)
        rb_row.addSpacing(20)
        rb_row.addWidget(self._rb_list)
        rb_row.addStretch()
        lay.addLayout(rb_row)

        self._dom_edit = QPlainTextEdit()
        self._dom_edit.setPlaceholderText(tr('dpi_dom_ph'))
        self._dom_edit.setFixedHeight(72)
        self._dom_edit.setEnabled(False)
        self._dom_edit.setStyleSheet(
            f'QPlainTextEdit {{'
            f'  background: {_BG_CARD}; border-radius: {_R};'
            f'  color: {_T1}; font-size: 11px;'
            '  font-family: Consolas, monospace; padding: 8px;'
            f'}}'
            f'QPlainTextEdit:disabled {{ color: {_T3}; }}'
        )
        lay.addWidget(self._dom_edit)
        return w

    def _build_note(self) -> QLabel:
        self._note = QLabel(tr('dpi_note'))
        self._note.setWordWrap(True)
        self._note.setStyleSheet(
            f'font-size: 10px; color: rgba(251,191,36,0.55);'
            f' background: rgba(251,191,36,0.06);'
            f' border-radius: {_R}; padding: 10px 12px;'
        )
        return self._note

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _collect_profile(self) -> DpiProfile:
        """Read all widget state into a DpiProfile."""
        techs = {attr for attr, tile in self._tech.items() if tile.is_checked()}
        domains: list[str] = []
        if hasattr(self, '_rb_list') and self._rb_list.isChecked():
            text = self._dom_edit.toPlainText().strip()
            domains = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return DpiProfile(
            preset=self._preset_bar.active(),
            techniques=techs,
            dns_enabled=self._dns_en_cb.isChecked(),
            dns_server=self._active_dns,
            doh=self._doh_cb.isChecked(),
            domains=domains,
            backend=default_backend(),
        )

    def _toggle(self):
        if self._worker and self._worker.isRunning():
            return  # operation in progress

        if not self._connected:
            self._start_engine()
        else:
            self._stop_engine()

    def _start_engine(self):
        # Windows: check binary available
        if sys.platform == 'win32' and not goodbyedpi_available():
            self._ask_download()
            return
        self._set_connecting(True)
        profile = self._collect_profile()
        w = _StartWorker(self._engine, profile, self)
        w.done.connect(self._on_start_done)
        self._worker = w
        w.start()

    def _stop_engine(self):
        self._set_connecting(True)
        w = _StopWorker(self._engine, self)
        w.done.connect(self._on_stop_done)
        self._worker = w
        w.start()

    def _on_start_done(self, ok: bool, err: str):
        self._set_connecting(False)
        if ok:
            self._connected = True
        else:
            self._connected = False
            QMessageBox.critical(self, tr('tab_dpi'), err)
        self._refresh_hero()
        self._refresh_chip()

    def _on_stop_done(self, ok: bool, err: str):
        self._set_connecting(False)
        self._connected = False
        if not ok:
            QMessageBox.warning(self, tr('tab_dpi'), err)
        self._refresh_hero()
        self._refresh_chip()

    def _set_connecting(self, busy: bool):
        """Gray-out the power button while a start/stop is in flight."""
        self._power_btn.setEnabled(not busy)
        self._state_lbl.setText(tr('dpi_connecting') if busy else
                                (tr('dpi_status_protected') if self._connected
                                 else tr('dpi_status_unprotected')))

    # ── Binary download ───────────────────────────────────────────────────────

    def _ask_download(self):
        from PyQt6.QtWidgets import QProgressDialog
        reply = QMessageBox.question(
            self, tr('tab_dpi'),
            tr('dpi_download_prompt'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        dlg = QProgressDialog(tr('dpi_downloading'), tr('inst_cancel_btn'), 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setValue(0)
        dlg.show()

        w = _DownloadWorker(self)

        def on_progress(pct, msg):
            dlg.setLabelText(msg)
            dlg.setValue(max(0, pct))

        def on_done(ok, err):
            dlg.close()
            if ok:
                QMessageBox.information(self, tr('tab_dpi'), tr('dpi_download_ok'))
            else:
                QMessageBox.critical(self, tr('tab_dpi'),
                                     tr('dpi_download_fail') + f'\n\n{err}')

        w.progress.connect(on_progress)
        w.done.connect(on_done)
        self._worker = w
        w.start()

    def _refresh_hero(self):
        on = self._connected
        self._power_btn.set_on(on)
        self._state_lbl.setText(
            tr('dpi_status_protected') if on else tr('dpi_status_unprotected'))
        self._state_lbl.setStyleSheet(
            f'font-size: 19px; font-weight: 700; color: '
            + (f'{_COL_GREEN};' if on else f'{_T1};'))
        self._state_sub.setText(
            tr('dpi_state_on_sub') if on else tr('dpi_state_off_sub'))
        self._refresh_summary()

    def _refresh_chip(self):
        on = self._connected
        self._chip.setText(tr('dpi_chip_on') if on else tr('dpi_chip_off'))
        self._chip.setStyleSheet(
            (f'background: rgba(74,222,128,0.12); color: {_COL_GREEN};'
             if on else
             f'background: rgba(255,255,255,0.05); color: {_T2};')
            + f' border-radius: 12px; font-size: 11px; font-weight: 600; padding: 0 14px;'
        )

    def _refresh_summary(self):
        if hasattr(self, '_preset_bar'):
            self._sum_mode_v.setText(tr(self._preset_bar.active()))
        if hasattr(self, '_dns_en_cb'):
            self._sum_dns_v.setText(
                self._active_dns if self._dns_en_cb.isChecked() else tr('dpi_off'))

    def _on_preset(self, key: str):
        if key != 'dpi_mode_custom':
            self._apply_preset_techniques(key)
        self._refresh_summary()

    def _apply_preset_techniques(self, key: str):
        wanted = _PRESETS.get(key, set())
        self._suppress_custom = True
        for attr, tile in self._tech.items():
            tile.set_checked(attr in wanted)
        self._suppress_custom = False

    def _on_tech_toggled(self, _v: bool):
        if self._suppress_custom:
            return
        self._preset_bar.select('dpi_mode_custom')
        self._refresh_summary()

    def _on_dns_select(self, ip: str):
        self._active_dns = ip
        for k, row in self._dns_rows.items():
            row.set_active(k == ip)
        self._refresh_summary()

    def _on_dns_toggle(self, v: bool):
        for row in self._dns_rows.values():
            row.setEnabled(v)
        self._doh_cb.setEnabled(v)
        self._refresh_summary()

    # ── Retranslate ───────────────────────────────────────────────────────────

    def retranslate(self):
        self._title_lbl.setText(tr('tab_dpi'))
        self._sub_lbl.setText(tr('dpi_cmd_subtitle'))
        self._dns_en_lbl.setText(tr('dpi_dns_enable'))
        self._dns_en_desc.setText(tr('dpi_dns_enable_d'))
        self._doh_lbl.setText(tr('dpi_doh'))
        self._doh_desc.setText(tr('dpi_doh_d'))
        self._rb_all.setText(tr('dpi_dom_all'))
        self._rb_list.setText(tr('dpi_dom_list'))
        self._dom_edit.setPlaceholderText(tr('dpi_dom_ph'))
        self._note.setText(tr('dpi_note'))
        self._sum_mode_k.setText(tr('dpi_sum_mode'))
        self._sum_dns_k.setText(tr('dpi_sum_dns'))
        for sec in (self._sec_mode, self._sec_tech, self._sec_dns, self._sec_dom):
            sec.setText(tr(sec.property('trkey')))
        for tile in self._tech.values():
            tile.retranslate()
        self._preset_bar.retranslate()
        self._refresh_hero()
        self._refresh_chip()
