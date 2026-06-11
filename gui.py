"""Modern desktop dashboard for the standalone NSE EOD project."""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QDate, QProcess, Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DB_FILE, DEFAULT_HISTORY_START, FAILED_EOD_FILE, FAILED_SHARES_FILE

PYTHON_EXE = Path(sys.executable)
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SHARE_REFRESH_DAYS = 120


def format_int(value):
    if value in (None, ""):
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def format_text(value):
    if value in (None, ""):
        return "-"
    return str(value)


class StatCard(QFrame):
    def __init__(self, title, accent="#3fb4ff", parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setMinimumHeight(110)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #95a7bd; font-size: 11px; text-transform: uppercase;")
        layout.addWidget(self.title_label)

        self.value_label = QLabel("-")
        self.value_label.setStyleSheet(f"color: {accent}; font-size: 28px; font-weight: 700;")
        layout.addWidget(self.value_label)

        self.note_label = QLabel("")
        self.note_label.setStyleSheet("color: #c5d1de; font-size: 12px;")
        layout.addWidget(self.note_label)
        layout.addStretch()

    def set_value(self, value, note=""):
        self.value_label.setText(str(value))
        self.note_label.setText(note)


class SectionBox(QGroupBox):
    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.setObjectName("SectionBox")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 18, 14, 14)
        self.body.setSpacing(10)


class DashboardWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NSE EOD Control Center")
        self.resize(1520, 940)
        self.process: QProcess | None = None
        self.current_label = ""

        self._build_ui()
        self._apply_theme()
        self._connect_timers()
        self.refresh_dashboard()

    def _build_ui(self):
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.NoFrame)
        sidebar_scroll.setMinimumWidth(370)
        sidebar_container = QWidget()
        self.sidebar = QVBoxLayout(sidebar_container)
        self.sidebar.setContentsMargins(18, 18, 18, 18)
        self.sidebar.setSpacing(16)
        self._build_sidebar()
        self.sidebar.addStretch()
        sidebar_scroll.setWidget(sidebar_container)
        splitter.addWidget(sidebar_scroll)

        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(16)
        self._build_header(main_layout)
        self._build_cards(main_layout)
        self._build_main_content(main_layout)
        splitter.addWidget(main_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1130])

        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

    def _build_sidebar(self):
        title = QLabel("Operations")
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #eff7ff;")
        self.sidebar.addWidget(title)

        subtitle = QLabel("Run backend jobs safely and inspect the health of the NSE EOD database.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 13px; color: #9bb0c6;")
        self.sidebar.addWidget(subtitle)

        run_box = SectionBox("Run Jobs")
        self.sidebar.addWidget(run_box)

        self.symbols_edit = QLineEdit()
        self.symbols_edit.setPlaceholderText("Optional symbols, e.g. RELIANCE,TCS,INFY")
        self.rebuild_actions_cb = QCheckBox("Rebuild affected symbols after syncing corporate actions")
        self.rebuild_actions_cb.setChecked(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.history_start_label = QLabel(DEFAULT_HISTORY_START)
        self.history_start_label.setStyleSheet("color: #e6eef7; font-weight: 600;")
        self.latest_update_label = QLabel("-")
        self.latest_update_label.setStyleSheet("color: #e6eef7; font-weight: 600;")
        grid.addWidget(QLabel("History Start"), 0, 0)
        grid.addWidget(self.history_start_label, 0, 1)
        grid.addWidget(QLabel("Latest Updated"), 1, 0)
        grid.addWidget(self.latest_update_label, 1, 1)
        grid.addWidget(QLabel("Symbols"), 2, 0)
        grid.addWidget(self.symbols_edit, 2, 1)
        run_box.body.addLayout(grid)
        date_help = QLabel(
            "Bootstrap runs from the configured start date to today automatically. "
            "Daily Refresh runs from the latest stored date to today automatically."
        )
        date_help.setWordWrap(True)
        date_help.setStyleSheet("color: #8ea4ba; font-size: 11px;")
        run_box.body.addWidget(date_help)
        run_box.body.addWidget(self.rebuild_actions_cb)

        self.sync_btn = self._action_button("Sync Symbols", self.run_sync_symbols, variant="secondary")
        self.bootstrap_btn = self._action_button("Bootstrap Prices", self.run_bootstrap, variant="primary")
        self.refresh_btn = self._action_button("Daily Refresh", self.run_daily_refresh, variant="secondary")
        self.shares_btn = self._action_button("Sync Shares", self.run_sync_shares, variant="secondary")
        self.actions_btn = self._action_button("Sync Corporate Actions", self.run_sync_actions, variant="secondary")
        self.rename_btn = self._action_button("Apply Symbol Changes", self.run_symbol_changes, variant="danger")
        self.rename_btn.setEnabled(False)
        self.rename_btn.setToolTip("Temporarily disabled. Symbol changes are handled during bootstrap.")
        self.rebuild_btn = self._action_button("Rebuild Adjusted Data", self.run_rebuild, variant="warning")
        self.retry_eod_btn = self._action_button("Retry Failed EOD", self.retry_failed_eod, variant="ghost")
        self.retry_shares_btn = self._action_button("Retry Failed Shares", self.retry_failed_shares, variant="ghost")

        for btn in [
            self.sync_btn,
            self.bootstrap_btn,
            self.refresh_btn,
            self.shares_btn,
            self.actions_btn,
            self.run_sharpe_btn,
            self.rename_btn,
            self.rebuild_btn,
            self.retry_eod_btn,
            self.retry_shares_btn,
        ]:
            run_box.body.addWidget(btn)

        sharpe_box = SectionBox("Sharpe Screener")
        self.sidebar.addWidget(sharpe_box)
        sharpe_grid = QGridLayout()
        sharpe_grid.setHorizontalSpacing(10)
        sharpe_grid.setVerticalSpacing(10)
        self.sharpe_date = QDateEdit()
        self.sharpe_date.setCalendarPopup(True)
        self.sharpe_date.setDate(QDate.currentDate())
        self.sharpe_mcap = QLineEdit("1000")
        self.sharpe_rf = QLineEdit("6.5")
        self.sharpe_turnover = QLineEdit("1.0")
        self.sharpe_long = QLineEdit("6")
        self.sharpe_short = QLineEdit("3")
        sharpe_grid.addWidget(QLabel("As-of Date"), 0, 0)
        sharpe_grid.addWidget(self.sharpe_date, 0, 1)
        sharpe_grid.addWidget(QLabel("MCAP (Cr)"), 1, 0)
        sharpe_grid.addWidget(self.sharpe_mcap, 1, 1)
        sharpe_grid.addWidget(QLabel("ROC Hurdle %"), 2, 0)
        sharpe_grid.addWidget(self.sharpe_rf, 2, 1)
        sharpe_grid.addWidget(QLabel("Turnover (Cr)"), 3, 0)
        sharpe_grid.addWidget(self.sharpe_turnover, 3, 1)
        month_row = QWidget()
        month_layout = QHBoxLayout(month_row)
        month_layout.setContentsMargins(0, 0, 0, 0)
        month_layout.setSpacing(6)
        month_layout.addWidget(self.sharpe_long)
        month_layout.addWidget(QLabel("/"))
        month_layout.addWidget(self.sharpe_short)
        sharpe_grid.addWidget(QLabel("L/S Months"), 4, 0)
        sharpe_grid.addWidget(month_row, 4, 1)
        sharpe_box.body.addLayout(sharpe_grid)
        self.run_sharpe_btn = self._action_button("Run Sharpe Screener", self.run_sharpe_screener, variant="primary")
        sharpe_box.body.addWidget(self.run_sharpe_btn)

        process_box = SectionBox("Run Control")
        self.sidebar.addWidget(process_box)
        self.stop_btn = QPushButton("Stop Current Job")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_current_job)
        self.stop_btn.setObjectName("DangerButton")
        process_box.body.addWidget(self.stop_btn)

        self.current_job_label = QLabel("No active backend job")
        self.current_job_label.setWordWrap(True)
        self.current_job_label.setStyleSheet("color: #9bb0c6; font-size: 12px;")
        process_box.body.addWidget(self.current_job_label)

        health_box = SectionBox("Quick Health")
        self.sidebar.addWidget(health_box)
        self.health_label = QLabel("Refreshing database summary...")
        self.health_label.setWordWrap(True)
        self.health_label.setStyleSheet("color: #c8d3df; font-size: 12px;")
        health_box.body.addWidget(self.health_label)
        self.refresh_button = QPushButton("Refresh Dashboard")
        self.refresh_button.clicked.connect(self.refresh_dashboard)
        self.refresh_button.setObjectName("SecondaryButton")
        health_box.body.addWidget(self.refresh_button)

    def _build_header(self, parent_layout):
        header = QFrame()
        header.setObjectName("HeaderBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        text_wrap = QVBoxLayout()
        text_wrap.setSpacing(4)
        heading = QLabel("NSE EOD Control Center")
        heading.setStyleSheet("font-size: 30px; font-weight: 700; color: #f6fbff;")
        text_wrap.addWidget(heading)

        sub = QLabel("Modern control dashboard for the NSE-first EOD pipeline, with stock-only market-cap tracking.")
        sub.setStyleSheet("font-size: 13px; color: #9bb0c6;")
        text_wrap.addWidget(sub)
        layout.addLayout(text_wrap)
        layout.addStretch()

        self.last_refresh_label = QLabel("Last refresh: -")
        self.last_refresh_label.setStyleSheet("font-size: 12px; color: #8ea4ba;")
        layout.addWidget(self.last_refresh_label)
        parent_layout.addWidget(header)

    def _build_cards(self, parent_layout):
        cards = QGridLayout()
        cards.setHorizontalSpacing(14)
        cards.setVerticalSpacing(14)

        self.card_symbols = StatCard("Active Symbols", accent="#53c7ff")
        self.card_latest_raw = StatCard("Latest Raw Date", accent="#89f7b1")
        self.card_latest_adjusted = StatCard("Latest Adjusted Date", accent="#ffd36a")
        self.card_stock_mcap = StatCard("Stocks With Market Cap", accent="#ff9c7d")
        self.card_share_symbols = StatCard("Stocks With Shares", accent="#d3a8ff")
        self.card_aliases = StatCard("Alias Rows", accent="#9be58f")

        cards.addWidget(self.card_symbols, 0, 0)
        cards.addWidget(self.card_latest_raw, 0, 1)
        cards.addWidget(self.card_latest_adjusted, 0, 2)
        cards.addWidget(self.card_stock_mcap, 1, 0)
        cards.addWidget(self.card_share_symbols, 1, 1)
        cards.addWidget(self.card_aliases, 1, 2)
        parent_layout.addLayout(cards)

    def _build_main_content(self, parent_layout):
        splitter = QSplitter(Qt.Vertical)
        parent_layout.addWidget(splitter, 1)

        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(16)

        query_box = SectionBox("Symbol Inspector")
        top_layout.addWidget(query_box, 1)

        query_row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Enter symbol, e.g. RELIANCE")
        self.query_btn = QPushButton("Inspect Symbol")
        self.query_btn.setObjectName("PrimaryButton")
        self.query_btn.clicked.connect(self.inspect_symbol)
        query_row.addWidget(self.query_edit)
        query_row.addWidget(self.query_btn)
        query_box.body.addLayout(query_row)

        self.symbol_summary = QLabel("No symbol selected.")
        self.symbol_summary.setWordWrap(True)
        self.symbol_summary.setStyleSheet("color: #c8d3df; font-size: 12px;")
        query_box.body.addWidget(self.symbol_summary)

        self.symbol_table = QTableWidget(0, 6)
        self.symbol_table.setHorizontalHeaderLabels(["Layer", "Latest Date", "Rows", "Latest Close", "Shares", "Market Cap Cr"])
        self.symbol_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.symbol_table.verticalHeader().setVisible(False)
        self.symbol_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.symbol_table.setSelectionMode(QTableWidget.NoSelection)
        query_box.body.addWidget(self.symbol_table)

        alias_box = SectionBox("Recent Aliases & Failures")
        top_layout.addWidget(alias_box, 1)
        self.alias_table = QTableWidget(0, 4)
        self.alias_table.setHorizontalHeaderLabels(["Old Symbol", "New Symbol", "Effective Date", "Source"])
        self.alias_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.alias_table.verticalHeader().setVisible(False)
        self.alias_table.setEditTriggers(QTableWidget.NoEditTriggers)
        alias_box.body.addWidget(self.alias_table)

        self.failure_summary = QLabel("No failure summary yet.")
        self.failure_summary.setWordWrap(True)
        self.failure_summary.setStyleSheet("color: #c8d3df; font-size: 12px;")
        alias_box.body.addWidget(self.failure_summary)
        splitter.addWidget(top)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(12)

        log_box = SectionBox("Live Activity Log")
        bottom_layout.addWidget(log_box)

        log_toolbar = QHBoxLayout()
        self.run_state_label = QLabel("Idle")
        self.run_state_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #ffd36a;")
        log_toolbar.addWidget(self.run_state_label)
        log_toolbar.addStretch()
        self.clear_log_btn = QPushButton("Clear Log")
        self.clear_log_btn.setObjectName("SecondaryButton")
        self.clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        log_toolbar.addWidget(self.clear_log_btn)
        log_box.body.addLayout(log_toolbar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Cascadia Code", 10))
        log_box.body.addWidget(self.log_view)
        splitter.addWidget(bottom)
        splitter.setSizes([420, 360])

    def _apply_theme(self):
        self.setStyleSheet(
            """
            QMainWindow {
                background: #0b1220;
            }
            QScrollArea, QWidget {
                background: transparent;
                color: #e6eef7;
                font-family: "Segoe UI";
            }
            #HeaderBar, #StatCard, #SectionBox {
                background: #121c2d;
                border: 1px solid #1e2c44;
                border-radius: 18px;
            }
            QGroupBox#SectionBox {
                margin-top: 12px;
                font-size: 13px;
                font-weight: 700;
                color: #f0f5fb;
            }
            QGroupBox#SectionBox::title {
                subcontrol-origin: margin;
                left: 12px;
                top: -6px;
                padding: 0 8px;
                color: #cfe2ff;
            }
            QLineEdit, QDateEdit, QTextEdit, QTableWidget {
                background: #0f1726;
                border: 1px solid #243651;
                border-radius: 12px;
                color: #e6eef7;
                padding: 8px 10px;
                selection-background-color: #214c75;
            }
            QLineEdit:focus, QDateEdit:focus, QTextEdit:focus {
                border: 1px solid #4cbcff;
            }
            QTableWidget {
                gridline-color: #233146;
            }
            QHeaderView::section {
                background: #172336;
                color: #c5d4e4;
                border: none;
                padding: 8px;
                font-weight: 600;
            }
            QPushButton {
                border: none;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 13px;
                font-weight: 600;
                background: #1a2940;
                color: #edf6ff;
            }
            QPushButton:hover {
                background: #223552;
            }
            QPushButton:disabled {
                background: #121a28;
                color: #617188;
            }
            QPushButton#PrimaryButton {
                background: #1e88ff;
                color: white;
            }
            QPushButton#PrimaryButton:hover {
                background: #2b98ff;
            }
            QPushButton#SecondaryButton {
                background: #1c2c43;
            }
            QPushButton#WarningButton {
                background: #9b5d14;
            }
            QPushButton#DangerButton {
                background: #8d2e3d;
            }
            QPushButton#GhostButton {
                background: #152235;
                color: #b8cbdf;
            }
            QCheckBox {
                color: #d4ddea;
                spacing: 8px;
            }
            QStatusBar {
                background: #0b1220;
                color: #9cb2c9;
            }
            """
        )

    def _connect_timers(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_dashboard)
        self.timer.start(30000)

    def _action_button(self, label, slot, variant="secondary"):
        btn = QPushButton(label)
        mapping = {
            "primary": "PrimaryButton",
            "secondary": "SecondaryButton",
            "warning": "WarningButton",
            "danger": "DangerButton",
            "ghost": "GhostButton",
        }
        btn.setObjectName(mapping.get(variant, "SecondaryButton"))
        btn.clicked.connect(slot)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _run_script(self, script_name, args, label, confirm_message=None):
        if self.process and self.process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Job Running", "A backend job is already running. Please wait or stop it first.")
            return
        if confirm_message:
            answer = QMessageBox.question(self, "Confirm Action", confirm_message)
            if answer != QMessageBox.Yes:
                return

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._consume_process_output)
        self.process.finished.connect(self._process_finished)

        script_path = str(BASE_DIR / script_name)
        self.process.start(str(PYTHON_EXE), [script_path] + args)
        self.current_label = label
        self.current_job_label.setText(f"Running: {label}")
        self.run_state_label.setText(f"Running: {label}")
        self.statusBar().showMessage(f"Running {label}...")
        self.stop_btn.setEnabled(True)
        self._set_run_buttons_enabled(False)
        self._log(f"\n[System] Started {label}\nCommand: {script_name} {' '.join(args)}\n")

    def _consume_process_output(self):
        if not self.process:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._log(text)

    def _process_finished(self, exit_code, _status):
        label = self.current_label or "job"
        state = "finished successfully" if exit_code == 0 else f"failed with exit code {exit_code}"
        self._log(f"\n[System] {label} {state}.\n")
        self.current_job_label.setText("No active backend job")
        self.run_state_label.setText("Idle")
        self.statusBar().showMessage(f"{label} {state}")
        self.stop_btn.setEnabled(False)
        self._set_run_buttons_enabled(True)
        self.process = None
        self.current_label = ""
        self.refresh_dashboard()

    def _set_run_buttons_enabled(self, enabled):
        for btn in [
            self.sync_btn,
            self.bootstrap_btn,
            self.refresh_btn,
            self.shares_btn,
            self.actions_btn,
            self.rename_btn,
            self.rebuild_btn,
            self.retry_eod_btn,
            self.retry_shares_btn,
            self.query_btn,
            self.refresh_button,
        ]:
            btn.setEnabled(enabled)

    def stop_current_job(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            self._log("\n[System] Stopping current backend job...\n")
            self.process.terminate()
            if not self.process.waitForFinished(4000):
                self.process.kill()

    def _log(self, text):
        if not text:
            return
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.insertPlainText(text)
        self.log_view.moveCursor(QTextCursor.End)

    def _selected_symbols(self):
        raw = self.symbols_edit.text().strip()
        if not raw:
            return []
        return [part.strip().upper() for part in raw.split(",") if part.strip()]

    def run_sync_symbols(self):
        self._run_script("sync_symbols.py", [], "Sync Symbols")

    def run_bootstrap(self):
        args = ["--bootstrap", "--start", DEFAULT_HISTORY_START]
        symbols = self._selected_symbols()
        if symbols:
            args.extend(["--symbols", ",".join(symbols)])
        self._run_script(
            "download_eod.py",
            args,
            "Bootstrap Prices",
            confirm_message="This will run a heavy historical bootstrap. Continue?",
        )

    def run_daily_refresh(self):
        args = ["--daily-pipeline"]
        symbols = self._selected_symbols()
        if symbols:
            args.extend(["--symbols", ",".join(symbols)])
        self._run_script("download_eod.py", args, "Daily Refresh")

    def run_sync_shares(self):
        args = ["--recent-days", str(DEFAULT_SHARE_REFRESH_DAYS)]
        self._run_script("sync_share_counts.py", args, "Sync Shares")

    def run_sync_actions(self):
        args = []
        if self.rebuild_actions_cb.isChecked():
            args.append("--rebuild")
        self._run_script("sync_corporate_actions.py", args, "Sync Corporate Actions")

    def run_sharpe_screener(self):
        args = [
            "--date", self.sharpe_date.date().toString("yyyy-MM-dd"),
            "--mcap", self.sharpe_mcap.text().strip() or "1000",
            "--rf", self.sharpe_rf.text().strip() or "6.5",
            "--turnover", self.sharpe_turnover.text().strip() or "1.0",
            "--long-months", self.sharpe_long.text().strip() or "6",
            "--short-months", self.sharpe_short.text().strip() or "3",
        ]
        self._run_script("sharpe_screener.py", args, "Sharpe Screener")

    def run_symbol_changes(self):
        self._run_script(
            "symbol_change_handler.py",
            ["--apply"],
            "Apply Symbol Changes",
            confirm_message="This will apply detected symbol renames to stored history. Continue?",
        )

    def run_rebuild(self):
        args = self._selected_symbols()
        self._run_script(
            "adjust_splits.py",
            args,
            "Rebuild Adjusted Data",
            confirm_message="This will rebuild adjusted series for the selected symbols or the full universe if blank. Continue?",
        )

    def retry_failed_eod(self):
        failed_dates = self._load_failed_eod_dates()
        if not failed_dates:
            QMessageBox.information(self, "Retry Failed EOD", "No failed EOD dates found.")
            return
        start_date = min(failed_dates)
        end_date = max(failed_dates)
        self._run_script(
            "download_eod.py",
            ["--start", start_date, "--end", end_date],
            "Retry Failed EOD",
        )

    def retry_failed_shares(self):
        symbols = self._load_failed_symbols(FAILED_SHARES_FILE)
        if not symbols:
            QMessageBox.information(self, "Retry Failed Shares", "No failed share symbols found.")
            return
        self._run_script(
            "sync_share_counts.py",
            ["--recent-days", str(DEFAULT_SHARE_REFRESH_DAYS), "--symbols", ",".join(symbols)],
            "Retry Failed Shares",
        )

    def _load_failed_symbols(self, path: Path):
        if not path.exists():
            return []
        symbols = []
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                symbol = (row.get("symbol") or "").strip().upper()
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        return symbols

    def _load_failed_eod_dates(self):
        if not FAILED_EOD_FILE.exists():
            return []
        dates = []
        with FAILED_EOD_FILE.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                report_date = (row.get("date") or "").strip()
                if report_date and report_date not in dates:
                    dates.append(report_date)
        return dates

    def refresh_dashboard(self):
        if not DB_FILE.exists():
            self.health_label.setText("Database file not found yet. Run Sync Symbols or Bootstrap Prices to initialize it.")
            return

        with sqlite3.connect(DB_FILE) as conn:
            counts = {
                "active_symbols": conn.execute("SELECT COUNT(*) FROM symbols WHERE active = 1").fetchone()[0],
                "active_stocks": conn.execute("SELECT COUNT(*) FROM symbols WHERE active = 1 AND instrument_type = 'STOCK'").fetchone()[0],
                "active_etfs": conn.execute("SELECT COUNT(*) FROM symbols WHERE active = 1 AND instrument_type = 'ETF'").fetchone()[0],
                "active_indices": conn.execute("SELECT COUNT(*) FROM symbols WHERE active = 1 AND instrument_type = 'INDEX'").fetchone()[0],
                "raw_latest": conn.execute("SELECT MAX(date) FROM raw_eod_prices").fetchone()[0],
                "adjusted_latest": conn.execute("SELECT MAX(date) FROM adjusted_eod_prices").fetchone()[0],
                "share_symbols": conn.execute("SELECT COUNT(DISTINCT symbol) FROM share_history").fetchone()[0],
                "mcap_symbols": conn.execute("SELECT COUNT(DISTINCT symbol) FROM marketcap WHERE market_cap_cr IS NOT NULL").fetchone()[0],
                "aliases": conn.execute("SELECT COUNT(*) FROM symbol_aliases").fetchone()[0],
                "renamed": conn.execute("SELECT COUNT(*) FROM symbols WHERE status = 'renamed'").fetchone()[0],
            }

            symbol_note = (
                f"{format_int(counts['active_stocks'])} stocks | "
                f"{format_int(counts['active_etfs'])} ETFs | "
                f"{format_int(counts['active_indices'])} indices"
            )
            self.card_symbols.set_value(format_int(counts["active_symbols"]), note=symbol_note)
            self.card_latest_raw.set_value(format_text(counts["raw_latest"]), note="Latest raw EOD date")
            self.card_latest_adjusted.set_value(format_text(counts["adjusted_latest"]), note="Latest adjusted rebuild date")
            self.card_stock_mcap.set_value(format_int(counts["mcap_symbols"]), note="Stocks with non-null market cap")
            self.card_share_symbols.set_value(format_int(counts["share_symbols"]), note="Stocks with share history")
            self.card_aliases.set_value(format_int(counts["aliases"]), note=f"{format_int(counts['renamed'])} symbols marked renamed")

            aliases = conn.execute(
                """
                SELECT old_symbol, new_symbol, COALESCE(effective_date, ''), source
                FROM symbol_aliases
                ORDER BY detected_at DESC, old_symbol
                LIMIT 15
                """
            ).fetchall()
            self._populate_table(self.alias_table, aliases)
            self.latest_update_label.setText(format_text(counts["raw_latest"]))

        self.failure_summary.setText(self._failure_summary_text())
        self.health_label.setText(
            f"DB: {DB_FILE.name}\n"
            f"Latest raw date: {format_text(counts['raw_latest'])}\n"
            f"Latest adjusted date: {format_text(counts['adjusted_latest'])}\n"
            f"Stocks with market cap: {format_int(counts['mcap_symbols'])} / {format_int(counts['active_stocks'])}"
        )
        self.last_refresh_label.setText(f"Last refresh: {QDate.currentDate().toString('dd MMM yyyy')}")

    def _failure_summary_text(self):
        parts = []
        for label, path in [("EOD failures", FAILED_EOD_FILE), ("Share failures", FAILED_SHARES_FILE)]:
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        rows = max(sum(1 for _ in handle) - 1, 0)
                except Exception:
                    rows = "?"
                parts.append(f"{label}: {rows}")
            else:
                parts.append(f"{label}: 0")
        return " | ".join(parts)

    def inspect_symbol(self):
        symbol = self.query_edit.text().strip().upper()
        if not symbol:
            QMessageBox.information(self, "Inspect Symbol", "Enter a symbol first.")
            return
        if not DB_FILE.exists():
            return
        with sqlite3.connect(DB_FILE) as conn:
            meta = conn.execute(
                """
                SELECT symbol, company_name, instrument_type, status, active
                FROM symbols
                WHERE symbol = ?
                """,
                (symbol,),
            ).fetchone()
            if not meta:
                self.symbol_summary.setText(f"No symbol found for {symbol}.")
                self.symbol_table.setRowCount(0)
                return

            raw = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM raw_eod_prices WHERE symbol = ?),
                    (SELECT MAX(date) FROM raw_eod_prices WHERE symbol = ?),
                    (SELECT close FROM raw_eod_prices WHERE symbol = ? ORDER BY date DESC LIMIT 1)
                """,
                (symbol, symbol, symbol),
            ).fetchone()
            adjusted = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM adjusted_eod_prices WHERE symbol = ?),
                    (SELECT MAX(date) FROM adjusted_eod_prices WHERE symbol = ?),
                    (SELECT close FROM adjusted_eod_prices WHERE symbol = ? ORDER BY date DESC LIMIT 1)
                """,
                (symbol, symbol, symbol),
            ).fetchone()
            shares = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM share_history WHERE symbol = ?),
                    (SELECT MAX(date) FROM share_history WHERE symbol = ?),
                    (SELECT shares_outstanding FROM share_history WHERE symbol = ? ORDER BY date DESC LIMIT 1)
                """,
                (symbol, symbol, symbol),
            ).fetchone()
            marketcap = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM marketcap WHERE symbol = ?),
                    (SELECT MAX(date) FROM marketcap WHERE symbol = ?),
                    (SELECT market_cap_cr FROM marketcap WHERE symbol = ? ORDER BY date DESC LIMIT 1),
                    (SELECT shares_outstanding FROM marketcap WHERE symbol = ? ORDER BY date DESC LIMIT 1)
                """,
                (symbol, symbol, symbol, symbol),
            ).fetchone()

            summary_rows = [
                ("Raw", raw[1], raw[0], raw[2], None, None),
                ("Adjusted", adjusted[1], adjusted[0], adjusted[2], None, None),
                ("Shares", shares[1], shares[0], None, shares[2], None),
                ("Market Cap", marketcap[1], marketcap[0], None, marketcap[3], marketcap[2]),
            ]

        self.symbol_summary.setText(
            f"{meta[0]} | {meta[1] or '-'}\n"
            f"Type: {meta[2]} | Status: {meta[3]} | Active: {'Yes' if meta[4] else 'No'}"
        )
        self._populate_table(self.symbol_table, summary_rows)

    def _populate_table(self, table: QTableWidget, rows):
        table.setRowCount(0)
        for row_idx, row in enumerate(rows):
            table.insertRow(row_idx)
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(format_text(value))
                item.setFlags(Qt.ItemIsEnabled)
                table.setItem(row_idx, col_idx, item)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("NSE EOD Control Center")
    window = DashboardWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
