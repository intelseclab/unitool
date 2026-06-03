import glob
import os
import re
import sys

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QFrame, QStackedWidget,
    QFileIconProvider,
)
from PyQt6.QtCore import Qt, QFileInfo, QSize
from PyQt6.QtGui import QPixmap

from ..uninstaller import InstalledApp, uninstall_app, fmt_size


# ── palette tokens ─────────────────────────────────────────────────────────────
_BLUE    = '#4CC2FF'
_GREEN   = '#4DDB8A'
_RED     = '#e03535'
_WARN    = '#FFA040'

_SOURCE_BADGE = {
    'Store':    '🏪  Microsoft Store',
    'HKCU':     'User Install',
    'HKLM':     'System Install',
    'HKLM_WOW': 'System (32-bit)',
    'macOS_App':'macOS App',
    'deb':      'Debian Package',
    'rpm':      'RPM Package',
}
_SOURCE_FULL = {
    'Store':    'Microsoft Store',
    'HKCU':     'Win32 — User',
    'HKLM':     'Win32 — System',
    'HKLM_WOW': 'Win32 — System (32-bit)',
    'macOS_App':'macOS Application',
    'deb':      'Debian Package',
    'rpm':      'RPM Package',
}

# Dialog-level QSS — covers only non-button elements.
# Buttons get their styles applied directly via setStyleSheet() on each
# QPushButton instance so there is no Qt cascade ambiguity.
_DIALOG_QSS = f"""
QDialog {{
    background: #12141b;
}}
QLabel {{
    background: transparent;
    color: rgba(255,255,255,0.85);
}}
QCheckBox {{
    color: rgba(255,255,255,0.82);
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid rgba(255,255,255,0.28);
    border-radius: 3px;
    background: transparent;
}}
QCheckBox::indicator:checked {{
    background: {_BLUE};
    border: none;
    border-radius: 3px;
}}
QCheckBox::indicator:unchecked:hover {{
    border-color: rgba(76,194,255,0.70);
}}
QCheckBox:disabled {{
    color: rgba(255,255,255,0.22);
}}
"""

# Per-button stylesheets applied directly — immune to cascade issues.
_BTN_PRIMARY = """
    QPushButton {
        background: #4CC2FF;
        color: #000000;
        font-size: 13px;
        font-weight: 700;
        border: none;
        border-radius: 6px;
        padding: 0px 20px;
        min-height: 32px;
        min-width: 100px;
    }
    QPushButton:hover   { background: #6bceff; }
    QPushButton:pressed { background: #38aee8; }
    QPushButton:disabled { background: rgba(76,194,255,0.25); color: rgba(0,0,0,0.35); }
"""

_BTN_GHOST = """
    QPushButton {
        background: rgba(255,255,255,0.08);
        color: rgba(255,255,255,0.82);
        font-size: 13px;
        border: 1px solid rgba(255,255,255,0.28);
        border-radius: 6px;
        padding: 0px 16px;
        min-height: 32px;
        min-width: 80px;
    }
    QPushButton:hover {
        background: rgba(255,255,255,0.14);
        color: #ffffff;
        border-color: rgba(255,255,255,0.45);
    }
    QPushButton:pressed  { background: rgba(255,255,255,0.05); }
    QPushButton:disabled { color: rgba(255,255,255,0.22); border-color: rgba(255,255,255,0.10); }
"""

_BTN_DANGER = """
    QPushButton {
        background: #e03535;
        color: #ffffff;
        font-size: 13px;
        font-weight: 700;
        border: none;
        border-radius: 6px;
        padding: 0px 20px;
        min-height: 32px;
        min-width: 120px;
    }
    QPushButton:hover   { background: #e84f4f; }
    QPushButton:pressed { background: #c42c2c; }
    QPushButton:disabled { background: rgba(224,53,53,0.30); color: rgba(255,255,255,0.35); }
"""


# ── helpers ────────────────────────────────────────────────────────────────────

def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet('background: rgba(255,255,255,0.07); max-height: 1px; border: none;')
    return f


def _exe_from_uninstall_str(s: str) -> str:
    """Extract an executable path from an uninstall string. Returns '' on failure."""
    if not s:
        return ''
    s = s.strip()
    if s.lower().startswith('msiexec'):
        return ''
    # Quoted path
    m = re.match(r'"([^"]+\.exe)"', s, re.IGNORECASE)
    if m and os.path.isfile(m.group(1)):
        return m.group(1)
    # Unquoted — try splitting on spaces until we find a real file
    parts = s.split()
    for i in range(1, min(6, len(parts) + 1)):
        candidate = ' '.join(parts[:i])
        if os.path.isfile(candidate):
            return candidate
    return ''


def _app_icon_pixmap(app: InstalledApp) -> QPixmap | None:
    """Return a 52×52 QPixmap for the app, or None if unavailable."""
    provider = QFileIconProvider()

    if app.source == 'Store':
        # Try loading a PNG from the package's Assets folder
        loc = app.install_location or ''
        if loc and os.path.isdir(loc):
            for pattern in [
                'Assets/Square44x44Logo.scale-200.png',
                'Assets/Square44x44Logo.scale-150.png',
                'Assets/Square44x44Logo.targetsize-48.png',
                'Assets/Square44x44Logo*.png',
                'Assets/Square150x150Logo*.png',
                'Assets/*.png',
            ]:
                try:
                    matches = glob.glob(os.path.join(loc, pattern))
                    for path in matches:
                        px = QPixmap(path)
                        if not px.isNull():
                            return px.scaled(
                                52, 52,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                except OSError:
                    pass
    else:
        # Win32 / macOS / Linux — try the uninstaller exe first
        exe = _exe_from_uninstall_str(app.uninstall_string)
        if exe:
            icon = provider.icon(QFileInfo(exe))
            px = icon.pixmap(QSize(52, 52))
            if not px.isNull():
                return px

        # Fallback: first .exe found in install_location
        loc = app.install_location or ''
        if loc and os.path.isdir(loc):
            try:
                for entry in os.scandir(loc):
                    if entry.name.lower().endswith('.exe'):
                        icon = provider.icon(QFileInfo(entry.path))
                        px = icon.pixmap(QSize(52, 52))
                        if not px.isNull():
                            return px
                        break
            except OSError:
                pass

    return None


def _app_avatar(name: str) -> QLabel:
    """Colored initials avatar — fallback when no real icon is available."""
    initials = ''.join(w[0].upper() for w in name.split()[:2]) or '?'
    palette = [_BLUE, _GREEN, _WARN, '#FF6060', '#C07FFF', '#FFD740', '#FF80AB']
    color = palette[sum(ord(c) for c in name) % len(palette)]
    lbl = QLabel(initials)
    lbl.setFixedSize(52, 52)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    # Inline stylesheet wins over the dialog-level QLabel rule
    lbl.setStyleSheet(
        f'background: {color}; color: #0a0a0a;'
        f' font-size: 20px; font-weight: 700; border-radius: 12px;'
    )
    return lbl


def _icon_label(app: InstalledApp) -> QLabel:
    """Return a 52×52 QLabel showing the real icon or the initials avatar."""
    px = _app_icon_pixmap(app)
    if px is not None:
        lbl = QLabel()
        lbl.setFixedSize(52, 52)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setPixmap(
            px.scaled(52, 52,
                      Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        )
        lbl.setStyleSheet('background: transparent; border-radius: 10px;')
        return lbl
    return _app_avatar(app.name)


# ── step bar ───────────────────────────────────────────────────────────────────

class _StepBar(QWidget):
    _LABELS = ('Overview', 'Options', 'Review')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self._dots: list[QFrame] = []
        self._lbls: list[QLabel] = []

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch()

        for i, name in enumerate(self._LABELS):
            col = QVBoxLayout()
            col.setSpacing(5)
            col.setContentsMargins(0, 8, 0, 0)
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            dot = QFrame()
            dot.setFixedSize(10, 10)
            self._dots.append(dot)
            col.addWidget(dot, alignment=Qt.AlignmentFlag.AlignHCenter)

            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._lbls.append(lbl)
            col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

            outer.addLayout(col)

            if i < len(self._LABELS) - 1:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setFixedWidth(96)
                line.setStyleSheet(
                    'background: rgba(255,255,255,0.12);'
                    ' max-height: 1px; border: none; margin-bottom: 16px;'
                )
                outer.addWidget(line)

        outer.addStretch()
        self.go(0)

    def go(self, step: int):
        for i in range(len(self._LABELS)):
            dot, lbl = self._dots[i], self._lbls[i]
            if i < step:
                dot.setStyleSheet(
                    f'background:{_GREEN};border-radius:5px;border:none;'
                )
                lbl.setStyleSheet(
                    f'background:transparent;font-size:10px;'
                    f'font-weight:600;color:{_GREEN};'
                )
            elif i == step:
                dot.setStyleSheet(
                    f'background:{_BLUE};border-radius:5px;border:none;'
                )
                lbl.setStyleSheet(
                    f'background:transparent;font-size:10px;'
                    f'font-weight:700;color:{_BLUE};'
                )
            else:
                dot.setStyleSheet(
                    'background:rgba(255,255,255,0.15);border-radius:5px;border:none;'
                )
                lbl.setStyleSheet(
                    'background:transparent;font-size:10px;'
                    'color:rgba(255,255,255,0.25);'
                )


# ── wizard ─────────────────────────────────────────────────────────────────────

class UninstallWizard(QDialog):
    """Four-page uninstall wizard: Overview → Options → Review → Done."""

    def __init__(self, app: InstalledApp, parent=None):
        super().__init__(parent)
        self._app = app
        self._page_idx = 0
        self._opt_leftovers = True
        self._opt_silent = False
        self._uninstall_ok = False
        self._error_msg = ''

        self.setWindowTitle(f'Uninstall — {app.name}')
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setFixedSize(560, 530)
        self.setModal(True)
        self.setStyleSheet(_DIALOG_QSS)

        self._build_ui()
        self._go_to(0)

    # ── public API ──────────────────────────────────────────────────────────────

    @property
    def uninstall_succeeded(self) -> bool:
        return self._uninstall_ok

    @property
    def find_leftovers_checked(self) -> bool:
        return self._uninstall_ok and self._opt_leftovers

    # ── layout construction ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(_hsep())

        self._step_bar = _StepBar()
        self._step_bar.setStyleSheet('background: #0f1117;')
        root.addWidget(self._step_bar)
        root.addWidget(_hsep())

        self._stack = QStackedWidget()
        self._stack.setStyleSheet('background: #12141b;')
        self._stack.addWidget(self._page_overview())   # 0
        self._stack.addWidget(self._page_options())    # 1
        self._stack.addWidget(self._page_review())     # 2
        self._stack.addWidget(self._page_done())       # 3
        root.addWidget(self._stack, 1)

        root.addWidget(_hsep())
        root.addWidget(self._build_footer())

    # ── header ─────────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        hdr = QWidget()
        hdr.setObjectName('wizHdr')
        hdr.setFixedHeight(90)
        hdr.setStyleSheet(
            'QWidget#wizHdr {'
            ' background: qlineargradient('
            '   x1:0,y1:0,x2:1,y2:1, stop:0 #0f1520, stop:1 #141922);'
            '}'
        )

        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(16)

        lay.addWidget(_icon_label(self._app))

        col = QVBoxLayout()
        col.setSpacing(5)

        name_lbl = QLabel(self._app.name)
        name_lbl.setStyleSheet(
            'font-size: 17px; font-weight: 700;'
            ' color: rgba(255,255,255,0.95);'
        )

        parts = []
        if self._app.publisher:
            parts.append(self._app.publisher)
        if self._app.version:
            parts.append(f'v{self._app.version}')
        if self._app.size_kb:
            parts.append(fmt_size(self._app.size_kb * 1024))
        sub_lbl = QLabel('  ·  '.join(parts) if parts else ' ')
        sub_lbl.setStyleSheet('font-size: 12px; color: rgba(255,255,255,0.38);')

        col.addWidget(name_lbl)
        col.addWidget(sub_lbl)

        badge = QLabel(_SOURCE_BADGE.get(self._app.source, self._app.source))
        badge.setStyleSheet(
            'background: rgba(255,255,255,0.07);'
            ' color: rgba(255,255,255,0.42);'
            ' font-size: 10px; font-weight: 600;'
            ' padding: 4px 10px; border-radius: 4px;'
            ' border: 1px solid rgba(255,255,255,0.10);'
        )
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addLayout(col, 1)
        lay.addWidget(badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        return hdr

    # ── footer ─────────────────────────────────────────────────────────────────

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(64)
        footer.setStyleSheet('QWidget { background: #0f1117; }')

        lay = QHBoxLayout(footer)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(8)

        # Styles applied directly on each button — widget-own stylesheet
        # always wins over any parent or application cascade.
        self._btn_cancel = QPushButton('Cancel')
        self._btn_cancel.setStyleSheet(_BTN_GHOST)
        self._btn_cancel.clicked.connect(self.reject)

        self._btn_back = QPushButton('← Back')
        self._btn_back.setStyleSheet(_BTN_GHOST)
        self._btn_back.clicked.connect(self._on_back)

        self._btn_next = QPushButton('Next →')
        self._btn_next.setStyleSheet(_BTN_PRIMARY)
        self._btn_next.clicked.connect(self._on_next)

        self._btn_uninstall = QPushButton('🗑  Uninstall')
        self._btn_uninstall.setStyleSheet(_BTN_DANGER)
        self._btn_uninstall.clicked.connect(self._on_uninstall)

        self._btn_finish = QPushButton('Finish')
        self._btn_finish.setStyleSheet(_BTN_PRIMARY)
        self._btn_finish.clicked.connect(self.accept)

        lay.addWidget(self._btn_cancel)
        lay.addStretch()
        lay.addWidget(self._btn_back)
        lay.addWidget(self._btn_next)
        lay.addWidget(self._btn_uninstall)
        lay.addWidget(self._btn_finish)
        return footer

    # ── page 0 — Overview ──────────────────────────────────────────────────────

    def _page_overview(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(32, 22, 32, 22)
        lay.setSpacing(18)

        intro = QLabel(
            'This wizard will guide you through removing the application below.\n'
            'Please review the details before continuing.'
        )
        intro.setWordWrap(True)
        intro.setStyleSheet('color: rgba(255,255,255,0.40); font-size: 12px;')
        lay.addWidget(intro)

        card = QFrame()
        card.setStyleSheet(
            'QFrame {'
            ' background: rgba(255,255,255,0.03);'
            ' border: 1px solid rgba(255,255,255,0.07);'
            ' border-radius: 8px;'
            '}'
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 14, 18, 14)
        cl.setSpacing(9)

        rows = [
            ('Name',      self._app.name),
            ('Publisher', self._app.publisher or '—'),
            ('Version',   self._app.version or '—'),
            ('Size',      fmt_size(self._app.size_kb * 1024) if self._app.size_kb else '—'),
            ('Installed', self._app.install_date or '—'),
            ('Source',    _SOURCE_FULL.get(self._app.source, self._app.source)),
        ]
        for label, value in rows:
            row = QHBoxLayout()
            row.setSpacing(14)
            lbl = QLabel(label)
            lbl.setFixedWidth(70)
            lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            lbl.setStyleSheet(
                'color: rgba(255,255,255,0.30);'
                ' font-size: 12px; font-weight: 600;'
            )
            val = QLabel(value)
            val.setStyleSheet('color: rgba(255,255,255,0.82); font-size: 12px;')
            val.setWordWrap(True)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            cl.addLayout(row)

        lay.addWidget(card)
        lay.addStretch()
        return page

    # ── page 1 — Options ───────────────────────────────────────────────────────

    def _page_options(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(32, 22, 32, 22)
        lay.setSpacing(14)

        intro = QLabel('Choose how the uninstall should proceed:')
        intro.setStyleSheet('color: rgba(255,255,255,0.36); font-size: 12px;')
        lay.addWidget(intro)

        # Option: find leftovers
        self._cb_leftovers = QCheckBox('  Find and remove leftover files')
        self._cb_leftovers.setChecked(True)
        self._cb_leftovers.setStyleSheet(
            'QCheckBox { font-size: 13px; font-weight: 600;'
            ' color: rgba(255,255,255,0.88); }'
        )
        note1 = QLabel(
            '    After the uninstall completes, UniTool will scan AppData and\n'
            '    program directories for leftover folders and files.'
        )
        note1.setStyleSheet('color: rgba(255,255,255,0.34); font-size: 11px;')
        lay.addWidget(self._cb_leftovers)
        lay.addWidget(note1)

        lay.addSpacing(4)
        lay.addWidget(_hsep())
        lay.addSpacing(4)

        # Option: silent
        is_store = self._app.source == 'Store'
        has_quiet = bool(self._app.quiet_uninstall_string) or (
            'msiexec' in self._app.uninstall_string.lower()
        )
        self._cb_silent = QCheckBox('  Silent / quiet uninstall')
        self._cb_silent.setChecked(False)
        self._cb_silent.setEnabled(not is_store and has_quiet)
        self._cb_silent.setStyleSheet(
            'QCheckBox { font-size: 13px; font-weight: 600;'
            ' color: rgba(255,255,255,0.88); }'
            'QCheckBox:disabled { color: rgba(255,255,255,0.24); }'
        )
        note2 = QLabel(
            '    Microsoft Store apps are always removed silently.'
            if is_store else
            '    Run the uninstaller without its own confirmation dialogs.\n'
            '    Only available when a quiet uninstall command is registered.'
        )
        note2.setStyleSheet('color: rgba(255,255,255,0.34); font-size: 11px;')
        lay.addWidget(self._cb_silent)
        lay.addWidget(note2)

        if is_store:
            lay.addSpacing(12)
            box = QFrame()
            box.setStyleSheet(
                'QFrame { background: rgba(255,160,64,0.07);'
                ' border: 1px solid rgba(255,160,64,0.22);'
                ' border-radius: 6px; }'
            )
            bl = QHBoxLayout(box)
            bl.setContentsMargins(14, 10, 14, 10)
            bl.setSpacing(10)
            wi = QLabel('⚠')
            wi.setStyleSheet(f'color:{_WARN}; font-size:15px;')
            wt = QLabel(
                'Microsoft Store apps are removed together with all their'
                ' settings and local data.'
            )
            wt.setWordWrap(True)
            wt.setStyleSheet(f'color:{_WARN}; font-size:11px;')
            bl.addWidget(wi, alignment=Qt.AlignmentFlag.AlignTop)
            bl.addWidget(wt, 1)
            lay.addWidget(box)

        lay.addStretch()
        return page

    # ── page 2 — Review ────────────────────────────────────────────────────────

    def _page_review(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(32, 22, 32, 22)
        lay.setSpacing(16)

        hrow = QHBoxLayout()
        hrow.setSpacing(10)
        hi = QLabel('⚠')
        hi.setStyleSheet(f'color:{_RED}; font-size:22px;')
        ht = QLabel('Ready to Uninstall')
        ht.setStyleSheet(
            f'color:{_RED}; font-size:16px; font-weight:700;'
        )
        hrow.addWidget(hi)
        hrow.addWidget(ht)
        hrow.addStretch()
        lay.addLayout(hrow)

        self._summary_card = QFrame()
        self._summary_card.setStyleSheet(
            'QFrame { background: rgba(224,53,53,0.05);'
            ' border: 1px solid rgba(224,53,53,0.18);'
            ' border-radius: 8px; }'
        )
        self._summary_lay = QVBoxLayout(self._summary_card)
        self._summary_lay.setContentsMargins(18, 14, 18, 14)
        self._summary_lay.setSpacing(10)
        lay.addWidget(self._summary_card)

        note = QLabel('⚠  This action cannot be undone.')
        note.setStyleSheet(
            f'color: rgba(255,160,64,0.75);'
            f' font-size: 11px; font-weight: 600;'
        )
        lay.addWidget(note)
        lay.addStretch()
        return page

    def _rebuild_summary(self):
        while self._summary_lay.count():
            item = self._summary_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        entries = [('🗑', f'Uninstall  {self._app.name}', True)]
        if self._opt_silent and self._app.source != 'Store':
            entries.append(('🔇', 'Silent — no confirmation dialogs', False))
        if self._opt_leftovers:
            entries.append(('📁', 'Search for and remove leftover files', False))

        for icon, text, primary in entries:
            row = QHBoxLayout()
            row.setSpacing(12)
            il = QLabel(icon)
            il.setFixedWidth(22)
            il.setAlignment(Qt.AlignmentFlag.AlignCenter)
            il.setStyleSheet('font-size:14px;')
            tl = QLabel(text)
            tl.setStyleSheet(
                f'color:{_RED}; font-size:13px; font-weight:600;'
                if primary else
                'color:rgba(255,255,255,0.60); font-size:12px;'
            )
            row.addWidget(il)
            row.addWidget(tl, 1)
            w = QWidget()
            w.setStyleSheet('background:transparent;')
            w.setLayout(row)
            self._summary_lay.addWidget(w)

    # ── page 3 — Done ──────────────────────────────────────────────────────────

    def _page_done(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 0, 40, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._done_icon = QLabel('✓')
        self._done_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._done_icon.setStyleSheet(
            f'color:{_GREEN}; font-size:54px; font-weight:700;'
        )
        self._done_title = QLabel('Uninstaller Launched')
        self._done_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._done_title.setStyleSheet(
            'color:rgba(255,255,255,0.93);'
            ' font-size:18px; font-weight:700;'
        )
        self._done_msg = QLabel('')
        self._done_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._done_msg.setWordWrap(True)
        self._done_msg.setStyleSheet(
            'color:rgba(255,255,255,0.42); font-size:12px;'
        )

        lay.addStretch()
        lay.addWidget(self._done_icon)
        lay.addSpacing(6)
        lay.addWidget(self._done_title)
        lay.addSpacing(8)
        lay.addWidget(self._done_msg)
        lay.addStretch()
        return page

    # ── navigation ─────────────────────────────────────────────────────────────

    def _go_to(self, idx: int):
        self._page_idx = idx
        self._stack.setCurrentIndex(idx)

        if idx < 3:
            self._step_bar.go(idx)

        # Visibility matrix — each button is always styled; we just show/hide.
        self._btn_cancel.setVisible(idx <= 2)
        self._btn_back.setVisible(idx in (1, 2))
        self._btn_next.setVisible(idx <= 1)
        self._btn_uninstall.setVisible(idx == 2)
        self._btn_finish.setVisible(idx == 3)

    def _on_next(self):
        if self._page_idx == 0:
            self._go_to(1)
        elif self._page_idx == 1:
            self._opt_leftovers = self._cb_leftovers.isChecked()
            self._opt_silent = (
                self._cb_silent.isChecked()
                if self._cb_silent.isEnabled() else False
            )
            self._rebuild_summary()
            self._go_to(2)

    def _on_back(self):
        if self._page_idx > 0:
            self._go_to(self._page_idx - 1)

    def _on_uninstall(self):
        # Disable all footer buttons while running
        for btn in (self._btn_cancel, self._btn_back, self._btn_uninstall):
            btn.setEnabled(False)

        ok, err = uninstall_app(self._app, silent=self._opt_silent)
        self._uninstall_ok = ok

        if ok:
            self._done_icon.setText('✓')
            self._done_icon.setStyleSheet(
                f'color:{_GREEN}; font-size:54px; font-weight:700;'
            )
            self._done_title.setText('Uninstaller Launched')
            msg = f'The uninstaller for "{self._app.name}" has been launched.'
            if self._opt_leftovers:
                msg += (
                    '\n\nUniTool will search for leftover files and folders'
                    '\nonce you close this dialog.'
                )
        else:
            self._done_icon.setText('✕')
            self._done_icon.setStyleSheet(
                f'color:{_RED}; font-size:54px; font-weight:700;'
            )
            self._done_title.setText('Failed to Launch Uninstaller')
            msg = err or 'An unknown error occurred.'

        self._done_msg.setText(msg)
        self._go_to(3)
