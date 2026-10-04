from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import __version__
from .browser import Source
from .pipeline import BatchWorker, Options
from .storage import DATA, DEFAULT_OUTPUT, ROOT, JobStore

STYLE = """
QMainWindow, QWidget#root { background:#f4f7fb; color:#1c304b; }
QWidget { color:#1c304b; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; font-size:13px; }
QLabel#heading { font-size:28px; font-weight:700; }
QLabel#muted { color:#697d96; }
QLabel#banner { background:#e7efff; color:#20488e; padding:18px; border-radius:12px; }
QPushButton { background:white; border:1px solid #d2deee; padding:10px 16px; border-radius:7px; }
QPushButton:hover { background:#edf2ff; }
QPushButton#primary { background:#245de8; color:white; border:none; font-weight:700; }
QPushButton:disabled { color:#8c9bb2; background:#e7edf6; }
QLineEdit, QComboBox, QPlainTextEdit { background:white; color:#20364f; border:1px solid #d3dfed; padding:8px; border-radius:7px; }
QTableWidget { background:white; alternate-background-color:#f8faff; border:1px solid #dce5f1; border-radius:8px; gridline-color:#edf2f8; }
QHeaderView::section { background:#eaf0f8; color:#57708f; border:0; padding:10px; }
QTableWidget::item:selected { background:#dfeaff; color:#183968; }
QProgressBar { background:#e2eaf7; border:0; border-radius:6px; height:18px; text-align:center; }
QProgressBar::chunk { background:#4374ea; border-radius:6px; }
"""


def button(label, callback, primary=False):
    widget = QPushButton(label)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


class Window(QMainWindow):
    def __init__(self, store=None, output=None):
        super().__init__()
        self.store = store or JobStore()
        self.output = output or DEFAULT_OUTPUT
        self.worker = None
        self.records = []
        self.snapshot = None
        self.closing = False
        self.setWindowTitle(f"Lecture Script · v{__version__}")
        self.resize(1020, 800)
        self.setMinimumSize(820, 640)
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
        QApplication.instance().setFont(QFont("Apple SD Gothic Neo" if sys.platform == "darwin" else "Malgun Gothic", 10))
        palette = QPalette(QColor("#f4f7fb"))
        for role, color in ((QPalette.ColorRole.WindowText, "#1c304b"), (QPalette.ColorRole.Text, "#1c304b"),
                            (QPalette.ColorRole.Base, "#ffffff"), (QPalette.ColorRole.ButtonText, "#1c304b"),
                            (QPalette.ColorRole.Highlight, "#dfeaff"), (QPalette.ColorRole.HighlightedText, "#183968")):
            palette.setColor(role, QColor(color))
        QApplication.instance().setPalette(palette)
        root = QWidget(); root.setObjectName("root"); self.setCentralWidget(root)
        layout = QVBoxLayout(root); layout.setContentsMargins(28, 24, 28, 24); layout.setSpacing(14)
        title = QLabel("강의 창에서, 스크립트까지."); title.setObjectName("heading"); layout.addWidget(title)
        subtitle = QLabel("크롬 확장에서 MP4 영상 · MP3 음성 · 스크립트 선택 저장"); subtitle.setObjectName("muted"); layout.addWidget(subtitle)
        banner = QLabel("크롬에서 한성 eClass 강의 동영상 창을 열면 자동으로 시작합니다.\n확장 설치 후에는 이 앱을 켜두지 않아도 됩니다. 진행 상황은 크롬 확장 아이콘에서 확인하세요.")
        banner.setObjectName("banner"); banner.setWordWrap(True); layout.addWidget(banner)
        actions = QHBoxLayout()
        actions.addWidget(button("크롬 확장 설치 안내", lambda: self.open_path(ROOT / "install-guide.html"), True))
        actions.addWidget(button("확장 폴더 열기", lambda: self.open_path(ROOT / "extension")))
        actions.addWidget(button("저장 폴더 열기", self.open_output))
        actions.addStretch(); layout.addLayout(actions)
        folder = QLabel(str(self.output) + " / 과목명 / video · audio · script")
        folder.setObjectName("muted"); folder.setWordWrap(True); folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(folder)
        local = QHBoxLayout()
        self.course = QLineEdit(); self.course.setPlaceholderText("내 파일의 과목명 입력")
        self.model = QComboBox()
        for label, value in (("빠르게 · small", "small"), ("균형 · medium", "medium"), ("정밀 · large-v3", "large-v3"), ("turbo", "turbo")):
            self.model.addItem(label, value)
        self.run_button = button("내 파일 선택 → 스크립트 생성", self.add_files)
        self.cancel_button = button("중단", self.cancel_jobs); self.cancel_button.setEnabled(False)
        for widget in (self.course, self.model, self.run_button, self.cancel_button): local.addWidget(widget)
        layout.addLayout(local)
        self.progress = QProgressBar(); self.progress.setValue(0); layout.addWidget(self.progress)
        self.progress_label = QLabel("크롬 자동 저장 대기 중 · 내 파일도 한 번에 처리할 수 있습니다.")
        self.progress_label.setTextFormat(Qt.TextFormat.PlainText); self.progress_label.setWordWrap(True); layout.addWidget(self.progress_label)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["강의", "상태", "진행"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 165); self.table.setColumnWidth(2, 320)
        self.table.verticalHeader().hide(); self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.show_result)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.table, 3)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("스크립트 기록을 선택하면 대본이 표시됩니다. 기록을 두 번 누르면 저장 폴더가 열립니다.")
        layout.addWidget(self.preview, 2)
        self.setStyleSheet(STYLE)
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_history); self.timer.start(1000)
        self.refresh_history()

    def open_path(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_output(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.open_path(self.output)

    def add_files(self):
        if self.worker and self.worker.isRunning(): return
        files, _ = QFileDialog.getOpenFileNames(self, "한 번에 처리할 동영상 선택", "",
            "영상·음성 (*.mp4 *.mkv *.webm *.mov *.m4a *.mp3 *.wav *.flac *.ogg);;자막 (*.srt *.vtt)")
        if files:
            self.launch_jobs([Source(file, "local", Path(file).stem, course=self.course.text().strip() or "과목 미지정") for file in files])

    def launch_jobs(self, sources):
        if self.worker and self.worker.isRunning(): return
        options = Options(self.output, model=self.model.currentData(), prefer_subtitles=False)
        self.worker = BatchWorker(sources, options, self.store)
        self.worker.progress.connect(self.report)
        self.worker.problem.connect(self.problem)
        self.worker.result.connect(lambda _: self.refresh_history())
        self.worker.finished.connect(self.finished)
        self.run_button.setEnabled(False); self.cancel_button.setEnabled(True)
        self.worker.start()

    def report(self, value, label):
        self.progress.setValue(value); self.progress_label.setText(label)

    def problem(self, label):
        self.progress_label.setText(label)

    def cancel_jobs(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            self.progress_label.setText("중단 요청 중 · 완료된 영상과 음성은 보관합니다.")

    def finished(self):
        self.run_button.setEnabled(True); self.cancel_button.setEnabled(False)
        self.refresh_history()
        if self.closing: self.close()

    def refresh_history(self):
        records = self.store.recent()
        if records == self.snapshot: return
        selected = self.table.currentRow()
        selected_id = self.records[selected]["id"] if 0 <= selected < len(self.records) else None
        self.snapshot = records; self.records = records
        self.table.blockSignals(True); self.table.setRowCount(len(records))
        labels = {"complete": "완료", "running": "처리 중", "downloading": "미디어 다운로드", "extracting": "음성 추출",
                  "transcribing": "스크립트 생성", "exporting": "선택한 파일 저장", "cleaning": "임시 파일 정리", "cancelled": "중단", "failed": "실패", "interrupted": "중단됨", "audio_ready": "이전 음원"}
        for row, record in enumerate(records):
            for column, value in enumerate((record["title"], labels.get(record["status"], record["status"]),
                                            record.get("error") or record.get("detail") or "")):
                self.table.setItem(row, column, QTableWidgetItem(value))
            if record["id"] == selected_id: self.table.selectRow(row)
        self.table.blockSignals(False)
        if selected_id: self.show_result()

    def show_result(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.records): return
        record = self.records[row]; path = Path(record["output_dir"]) / "transcript.txt"
        try: self.preview.setPlainText(path.read_text(encoding="utf-8"))
        except OSError: self.preview.setPlainText(record.get("error") or record.get("detail") or "스크립트가 아직 생성되지 않았습니다.")

    def open_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.records): self.open_path(Path(self.records[row]["output_dir"]))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.closing = True; self.cancel_jobs(); event.ignore()
        else: event.accept()


def main():
    app = QApplication(sys.argv); app.setStyle("Fusion")
    DATA.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(DATA / "app.lock")); lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "Lecture Script", "프로그램이 이미 실행 중입니다.")
        return 0
    window = Window(); window.show()
    result = app.exec(); lock.unlock(); return result
