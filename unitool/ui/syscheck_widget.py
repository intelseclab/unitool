import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QProgressBar,
    QTextEdit, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from ..syscheck import Finding, CATEGORIES, scan_all, fix_finding, generate_report
from ..translations import tr

_RISK_FG: dict[str, QColor] = {
    'high':   QColor(224, 80,  80),
    'medium': QColor(255, 160, 64),
    'low':    QColor(220, 195, 60),
    'info':   QColor(140, 140, 140),
}
_RISK_BG: dict[str, QColor] = {
    'high':   QColor(70,  16, 16),
    'medium': QColor(68,  44, 10),
    'low':    QColor(52,  48,  8),
    'info':   QColor(32,  32, 32),
}
_COLOR_FIXED = QColor(77, 219, 138)


# ── Worker threads ─────────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    done     = pyqtSignal(list)

    def run(self):
        findings = scan_all(progress_callback=self._cb)
        self.done.emit(findings)

    def _cb(self, done: int, total: int, step: str):
        self.progress.emit(done, total, step)


class _FixWorker(QThread):
    done = pyqtSignal(int, int)  # ok, total

    def __init__(self, findings: list[Finding], parent=None):
        super().__init__(parent)
        self._findings = findings

    def run(self):
        ok = 0
        for f in self._findings:
            success, _ = fix_finding(f)
            if success:
                f.fixed = True
                ok += 1
        self.done.emit(ok, len(self._findings))


# ── Main widget ────────────────────────────────────────────────────────────────

class SysCheckWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._findings: list[Finding] = []
        self._shown:    list[Finding] = []
        self._selected_cat: str | None = None
        self._scan_worker: _ScanWorker | None = None
        self._fix_worker:  _FixWorker  | None = None
        self._setup_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_cmd_bar())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_category_panel())
        splitter.addWidget(self._build_content_area())
        splitter.setSizes([210, 1000])
        layout.addWidget(splitter, 1)

    def _build_cmd_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('cmdBar')
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._btn_scan = QPushButton(tr('sc_scan_btn'))
        self._btn_scan.setObjectName('primaryCmd')
        self._btn_scan.clicked.connect(self._scan)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._btn_fix = QPushButton(tr('sc_fix_btn'))
        self._btn_fix.setObjectName('dangerCmd')
        self._btn_fix.setEnabled(False)
        self._btn_fix.clicked.connect(self._fix_selected)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet('background: rgba(255,255,255,0.10); max-width: 1px; border: none;')

        self._btn_report = QPushButton(tr('sc_report_btn'))
        self._btn_report.setObjectName('cmdBtn')
        self._btn_report.setEnabled(False)
        self._btn_report.clicked.connect(self._save_report)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedWidth(120)
        self._progress.setFixedHeight(6)
        self._progress.setVisible(False)

        self._status_lbl = QLabel(tr('sc_status_idle'))
        self._status_lbl.setStyleSheet('color: rgba(255,255,255,0.40); font-size: 12px;')

        lay.addWidget(self._btn_scan)
        lay.addSpacing(4)
        lay.addWidget(sep1)
        lay.addSpacing(4)
        lay.addWidget(self._btn_fix)
        lay.addSpacing(4)
        lay.addWidget(sep2)
        lay.addSpacing(4)
        lay.addWidget(self._btn_report)
        lay.addStretch()
        lay.addWidget(self._progress)
        lay.addSpacing(8)
        lay.addWidget(self._status_lbl)
        return bar

    def _build_category_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('leftPanel')
        panel.setFixedWidth(210)
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

        all_item = QListWidgetItem(f'◉  {tr("sc_all_categories")}')
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self._cat_list.addItem(all_item)

        for cat_id, icon, tr_key in CATEGORIES:
            item = QListWidgetItem(f'{icon}  {tr(tr_key)}')
            item.setData(Qt.ItemDataRole.UserRole, cat_id)
            self._cat_list.addItem(item)

        self._cat_list.setCurrentRow(0)
        self._cat_list.currentItemChanged.connect(self._on_cat_changed)
        lay.addWidget(self._cat_list, 1)
        return panel

    def _build_content_area(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(8, 10, 8, 8)
        outer.setSpacing(0)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self._build_findings_table())
        vsplit.addWidget(self._build_detail_panel())
        vsplit.setSizes([380, 160])
        outer.addWidget(vsplit, 1)
        return panel

    def _build_findings_table(self) -> QWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            '', tr('sc_col_risk'), tr('sc_col_title'),
            tr('sc_col_category'), tr('sc_col_location'),
        ])
        self._table.setAlternatingRowColors(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.itemChanged.connect(self._on_check_changed)
        self._table.currentCellChanged.connect(self._on_row_selected)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(0, 28)
        return self._table

    def _build_detail_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName('scDetailPanel')
        panel.setStyleSheet(
            'QFrame#scDetailPanel {'
            '  background: rgba(255,255,255,0.02);'
            '  border-top: 1px solid rgba(255,255,255,0.07);'
            '}'
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        hdr = QLabel(tr('sc_detail_title'))
        hdr.setStyleSheet(
            'color: rgba(255,255,255,0.32); font-size: 9px; font-weight: 700; '
            'letter-spacing: 0.8px;'
        )
        lay.addWidget(hdr)

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setStyleSheet(
            'QTextEdit {'
            '  background: transparent; border: none;'
            '  color: rgba(255,255,255,0.72); font-size: 12px;'
            '  font-family: Consolas, "Courier New", monospace;'
            '}'
        )
        self._detail_text.setPlaceholderText(tr('sc_detail_empty'))
        lay.addWidget(self._detail_text, 1)
        return panel

    # ── Data population ────────────────────────────────────────────────────────

    def _populate_table(self):
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._shown.clear()

        for f in self._findings:
            if self._selected_cat is not None and f.category != self._selected_cat:
                continue
            self._shown.append(f)

        self._table.setRowCount(len(self._shown))
        bold_font = QFont()
        bold_font.setBold(True)
        bold_font.setPointSize(9)

        for row, f in enumerate(self._shown):
            bg = _RISK_BG.get(f.risk, QColor(32, 32, 32))

            # col 0: checkbox
            chk = QTableWidgetItem()
            chk.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setBackground(bg)
            self._table.setItem(row, 0, chk)

            # col 1: risk badge
            risk_lbl = tr(f'sc_risk_{f.risk}')
            risk_item = QTableWidgetItem(risk_lbl)
            risk_item.setForeground(_RISK_FG.get(f.risk, QColor(200, 200, 200)))
            risk_item.setFont(bold_font)
            risk_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            risk_item.setBackground(bg)
            self._table.setItem(row, 1, risk_item)

            # col 2: title
            title_item = QTableWidgetItem(f.title)
            title_item.setBackground(bg)
            if f.fixed:
                title_item.setForeground(_COLOR_FIXED)
            self._table.setItem(row, 2, title_item)

            # col 3: category
            cat_tr_key = next(
                (tk for cid, _, tk in CATEGORIES if cid == f.category), f.category
            )
            cat_item = QTableWidgetItem(tr(cat_tr_key))
            cat_item.setForeground(QColor(150, 150, 150))
            cat_item.setBackground(bg)
            self._table.setItem(row, 3, cat_item)

            # col 4: location (truncated)
            loc = f.location if len(f.location) <= 64 else '…' + f.location[-61:]
            loc_item = QTableWidgetItem(loc)
            loc_item.setForeground(QColor(110, 110, 110))
            loc_item.setBackground(bg)
            self._table.setItem(row, 4, loc_item)

        self._table.blockSignals(False)
        self._update_fix_btn()

    def _update_category_badges(self):
        counts: dict[str | None, int] = {None: len(self._findings)}
        for f in self._findings:
            counts[f.category] = counts.get(f.category, 0) + 1

        for i in range(self._cat_list.count()):
            item = self._cat_list.item(i)
            cat_id = item.data(Qt.ItemDataRole.UserRole)
            if cat_id is None:
                base = f'◉  {tr("sc_all_categories")}'
                n = counts.get(None, 0)
            else:
                icon, tr_key = next(
                    ((ico, tk) for cid, ico, tk in CATEGORIES if cid == cat_id),
                    ('', cat_id),
                )
                base = f'{icon}  {tr(tr_key)}'
                n = counts.get(cat_id, 0)
            item.setText(f'{base}  ({n})' if n else base)

    def _reset_category_badges(self):
        for i in range(self._cat_list.count()):
            item = self._cat_list.item(i)
            cat_id = item.data(Qt.ItemDataRole.UserRole)
            if cat_id is None:
                item.setText(f'◉  {tr("sc_all_categories")}')
            else:
                icon, tr_key = next(
                    ((ico, tk) for cid, ico, tk in CATEGORIES if cid == cat_id),
                    ('', cat_id),
                )
                item.setText(f'{icon}  {tr(tr_key)}')

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _on_cat_changed(self, current: QListWidgetItem, _prev):
        if current is None:
            return
        self._selected_cat = current.data(Qt.ItemDataRole.UserRole)
        self._populate_table()

    def _on_check_changed(self, item: QTableWidgetItem):
        if item.column() == 0:
            self._update_fix_btn()

    def _on_row_selected(self, row: int, *_):
        if row < 0 or row >= len(self._shown):
            self._detail_text.setPlainText('')
            return
        f = self._shown[row]
        cat_tr_key = next(
            (tk for cid, _, tk in CATEGORIES if cid == f.category), f.category
        )
        lines = [
            f'Finding:   {f.title}',
            f'Category:  {tr(cat_tr_key)}',
            f'Risk:      {f.risk.upper()}',
            '',
            f'Detail:',
            f'  {f.detail}',
            '',
            f'Location:  {f.location}',
        ]
        if f.value:
            lines.append(f'Value:     {f.value}')
        if f.fixable:
            lines += ['', f'Fixable:   Yes  (method: {f.fix_method})']
        if f.fixed:
            lines += ['', '✓ This finding has been fixed.']
        self._detail_text.setPlainText('\n'.join(lines))

    def _update_fix_btn(self):
        has_fixable = any(
            self._table.item(r, 0) is not None
            and self._table.item(r, 0).checkState() == Qt.CheckState.Checked
            and r < len(self._shown)
            and self._shown[r].fixable
            and not self._shown[r].fixed
            for r in range(self._table.rowCount())
        )
        self._btn_fix.setEnabled(has_fixable)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _scan(self):
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self._findings.clear()
        self._shown.clear()
        self._table.setRowCount(0)
        self._detail_text.setPlainText('')
        self._btn_scan.setEnabled(False)
        self._btn_fix.setEnabled(False)
        self._btn_report.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._status_lbl.setText(tr('sc_status_scanning'))
        self._reset_category_badges()

        self._scan_worker = _ScanWorker(self)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.done.connect(self._on_scan_done)
        self._scan_worker.start()

    def _on_scan_progress(self, done: int, total: int, step: str):
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        self._status_lbl.setText(step)

    def _on_scan_done(self, findings: list):
        self._findings = findings
        self._progress.setVisible(False)
        self._btn_scan.setEnabled(True)
        self._btn_report.setEnabled(bool(findings))

        high   = sum(1 for f in findings if f.risk == 'high')
        medium = sum(1 for f in findings if f.risk == 'medium')
        low    = sum(1 for f in findings if f.risk == 'low')

        if findings:
            self._status_lbl.setText(
                tr('sc_status_found', n=len(findings), high=high, medium=medium, low=low)
            )
        else:
            self._status_lbl.setText(tr('sc_status_clean'))

        self._update_category_badges()
        self._populate_table()

    def _fix_selected(self):
        to_fix = [
            self._shown[r]
            for r in range(self._table.rowCount())
            if (
                self._table.item(r, 0) is not None
                and self._table.item(r, 0).checkState() == Qt.CheckState.Checked
                and r < len(self._shown)
                and self._shown[r].fixable
                and not self._shown[r].fixed
            )
        ]
        if not to_fix:
            QMessageBox.information(self, '', tr('sc_no_fixable'))
            return

        reply = QMessageBox.question(
            self,
            tr('sc_fix_confirm_title'),
            tr('sc_fix_confirm_msg', n=len(to_fix)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._btn_fix.setEnabled(False)
        self._btn_scan.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)

        self._fix_worker = _FixWorker(to_fix, self)
        self._fix_worker.done.connect(self._on_fix_done)
        self._fix_worker.start()

    def _on_fix_done(self, ok: int, total: int):
        self._progress.setVisible(False)
        self._btn_scan.setEnabled(True)
        self._status_lbl.setText(tr('sc_fix_done', ok=ok, total=total))
        self._populate_table()

    def _save_report(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('sc_report_title'),
            os.path.join(os.path.expanduser('~'), 'Desktop', 'UniTool_SysCheck.txt'),
            'Text Files (*.txt)',
        )
        if not path:
            return
        try:
            report = generate_report(self._findings)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(report)
            QMessageBox.information(self, '', tr('sc_report_saved', path=path))
        except Exception as exc:
            QMessageBox.warning(self, '', tr('sc_report_error', err=str(exc)))

    # ── Retranslation ──────────────────────────────────────────────────────────

    def retranslate(self):
        self._btn_scan.setText(tr('sc_scan_btn'))
        self._btn_fix.setText(tr('sc_fix_btn'))
        self._btn_report.setText(tr('sc_report_btn'))
        self._detail_text.setPlaceholderText(tr('sc_detail_empty'))
        self._table.setHorizontalHeaderLabels([
            '', tr('sc_col_risk'), tr('sc_col_title'),
            tr('sc_col_category'), tr('sc_col_location'),
        ])
        if not self._findings:
            self._status_lbl.setText(tr('sc_status_idle'))
        # refresh category list labels (without losing selection)
        self._reset_category_badges()
        if self._findings:
            self._update_category_badges()
