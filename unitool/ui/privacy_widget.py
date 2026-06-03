"""
unitool/ui/privacy_widget.py
Top-level Privacy tab — thin QTabWidget wrapper + platform strip.
"""
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QFrame, QLabel,
)

from ..privacy import get_platform_info
from ..translations import tr
from .privacy_traces_tab import TracesTab
from .privacy_toggle_tab import ToggleTab
from .privacy_ram_tab import RamTab


class PrivacyWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._platform_info = get_platform_info()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_platform_strip())

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._traces_tab = TracesTab()
        self._tel_tab    = ToggleTab('telemetry')
        self._prv_tab    = ToggleTab('privacy')
        self._feat_tab   = ToggleTab('features')
        self._ram_tab    = RamTab(self._platform_info)

        self._tabs.addTab(self._traces_tab, tr('prv_tab_traces'))
        self._tabs.addTab(self._tel_tab,    tr('prv_tab_telemetry'))
        self._tabs.addTab(self._prv_tab,    tr('prv_tab_privacy'))
        self._tabs.addTab(self._feat_tab,   tr('prv_tab_features'))
        self._tabs.addTab(self._ram_tab,    tr('prv_tab_ram'))

        layout.addWidget(self._tabs, 1)

    def _build_platform_strip(self) -> QWidget:
        strip = QFrame()
        strip.setObjectName('platformStrip')
        strip.setFixedHeight(42)
        strip.setStyleSheet(
            'QFrame#platformStrip {'
            '  background: rgba(255,255,255,0.03);'
            '  border-bottom: 1px solid rgba(255,255,255,0.07);'
            '}'
        )
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(20)

        p = self._platform_info
        if p['system'] == 'win32':
            os_text, os_icon = f"Windows  {p['version']}", '🪟'
        elif p['system'] == 'darwin':
            os_text, os_icon = f"macOS {p['version']}", ''
        else:
            os_text, os_icon = p.get('distro') or f"Linux {p['version']}", '🐧'

        os_lbl = QLabel(f'{os_icon}  {os_text}')
        os_lbl.setStyleSheet('color: rgba(255,255,255,0.72); font-size: 12px;')

        arch_lbl = QLabel(p['arch'])
        arch_lbl.setStyleSheet('color: rgba(255,255,255,0.28); font-size: 11px;')

        if p['is_admin']:
            atxt = tr('prv_admin') if p['system'] == 'win32' else tr('prv_root')
            aclr = '#4DDB8A'
        else:
            atxt = tr('prv_std_user')
            aclr = 'rgba(255,255,255,0.38)'

        self._admin_lbl = QLabel(f'⚡ {atxt}')
        self._admin_lbl.setStyleSheet(f'color: {aclr}; font-size: 12px; font-weight: 600;')

        lay.addWidget(os_lbl)
        lay.addWidget(arch_lbl)
        lay.addStretch()
        lay.addWidget(self._admin_lbl)
        return strip

    def retranslate(self):
        self._tabs.setTabText(0, tr('prv_tab_traces'))
        self._tabs.setTabText(1, tr('prv_tab_telemetry'))
        self._tabs.setTabText(2, tr('prv_tab_privacy'))
        self._tabs.setTabText(3, tr('prv_tab_features'))
        self._tabs.setTabText(4, tr('prv_tab_ram'))

        self._traces_tab.retranslate()
        self._tel_tab.retranslate()
        self._prv_tab.retranslate()
        self._feat_tab.retranslate()
        self._ram_tab.retranslate()

        p = self._platform_info
        atxt = (tr('prv_admin') if p['system'] == 'win32' else tr('prv_root')) \
               if p['is_admin'] else tr('prv_std_user')
        self._admin_lbl.setText(f'⚡ {atxt}')
