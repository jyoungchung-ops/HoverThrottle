"""메인 창: 왼쪽 입력 패널 + 오른쪽 결과 탭(요약·그래프·CLI)."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config
from ..analysis import pipeline
from ..applog import app_dir
from ..io import normalize
from ..io.log_reader import SUPPORTED_EXTS, LogReadError, MissingFieldsError, list_flights, read_headers
from ..presets import PresetStore
from . import plots, report_html

_log = logging.getLogger(__name__)

FILE_FILTER = "블랙박스 로그 (*.bbl *.bfl *.txt *.csv *.BBL *.BFL *.TXT *.CSV);;모든 파일 (*)"


def resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / rel


# ---------------------------------------------------------------------------
# 작은 위젯들
# ---------------------------------------------------------------------------

class FileDropList(QListWidget):
    """파일을 끌어다 놓을 수 있는 첨부 목록."""

    filesDropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setMinimumHeight(70)
        self.setMaximumHeight(120)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            e.acceptProposedAction()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            from PySide6.QtGui import QPainter

            p = QPainter(self.viewport())
            p.setPen(self.palette().placeholderText().color())
            p.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap, "여기에 BBL/CSV 파일을\n끌어다 놓으세요")


class OptionalSpin(QSpinBox):
    """최솟값을 '미입력'으로 쓰는 정수 입력."""

    def __init__(self, lo: int, hi: int, empty_text: str = "—", suffix: str = ""):
        super().__init__()
        self.setRange(lo - 1, hi)
        self.setSpecialValueText(empty_text)
        self.setSuffix(suffix)
        self.setValue(lo - 1)

    def get(self) -> int | None:
        return None if self.value() == self.minimum() else self.value()

    def put(self, v) -> None:
        self.setValue(self.minimum() if v is None else int(v))


class OptionalDoubleSpin(QDoubleSpinBox):
    def __init__(self, lo: float, hi: float, decimals: int = 1, suffix: str = ""):
        super().__init__()
        self.setDecimals(decimals)
        self.setRange(lo - 1, hi)
        self.setSpecialValueText("—")
        self.setSuffix(suffix)
        self.setValue(lo - 1)

    def get(self) -> float | None:
        return None if self.value() == self.minimum() else float(self.value())

    def put(self, v) -> None:
        self.setValue(self.minimum() if v is None else float(v))


class PlotPanel(QWidget):
    """matplotlib 캔버스 + 확대·이동 툴바."""

    def __init__(self, height: float = 3.4):
        super().__init__()
        plots.setup_fonts()
        self.figure = Figure(figsize=(8, height), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(int(height * 90))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(NavigationToolbar2QT(self.canvas, self))
        lay.addWidget(self.canvas)

    def draw(self, fn, *args) -> None:
        fn(self.figure, *args)
        self.canvas.draw_idle()


class AnalysisWorker(QThread):
    progressed = Signal(float, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, targets, craft):
        super().__init__()
        self.targets = targets
        self.craft = craft

    def run(self):
        try:
            res = pipeline.run(self.targets, self.craft, lambda p, m: self.progressed.emit(p, m))
            self.done.emit(res)
        except Exception:  # 예상 못 한 오류는 로그에 남기고 대화상자로
            _log.exception("analysis crashed")
            self.failed.emit(traceback.format_exc(limit=3))


# ---------------------------------------------------------------------------
# 메인 창
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, presets: PresetStore | None = None):
        super().__init__()
        self.setWindowTitle(f"HoverThrottle {__version__} — FPV 호버링 스로틀 추천")
        self.setMinimumSize(1100, 700)
        icon = resource_path("assets/icon.ico")
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))

        self.presets = presets or PresetStore()
        self.result: pipeline.AnalysisResult | None = None
        self.worker: AnalysisWorker | None = None
        self._files: list[str] = []

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_input_panel())
        splitter.addWidget(self._build_result_panel())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 800])
        self.setCentralWidget(splitter)
        self._show_empty()

    # ----- 입력 패널 ---------------------------------------------------------

    def _build_input_panel(self) -> QWidget:
        inner = QWidget()
        lay = QVBoxLayout(inner)

        # 프리셋
        g = QGroupBox("기체 프리셋")
        h = QHBoxLayout(g)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumContentsLength(12)
        self.preset_combo.activated.connect(self._on_preset_selected)
        save_btn = QPushButton("저장")
        save_btn.clicked.connect(self._save_preset)
        del_btn = QPushButton("삭제")
        del_btn.clicked.connect(self._delete_preset)
        h.addWidget(self.preset_combo, 1)
        h.addWidget(save_btn)
        h.addWidget(del_btn)
        lay.addWidget(g)
        self._reload_presets()

        # 기체 정보
        g = QGroupBox("기체 정보")
        f = QFormLayout(g)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("선택")
        self.class_combo = QComboBox()
        for key, label in config.CRAFT_CLASSES.items():
            self.class_combo.addItem(label, key)
        self.class_combo.setCurrentIndex(list(config.CRAFT_CLASSES).index(config.CLASS_5INCH))
        self.cells_combo = QComboBox()
        self.cells_combo.addItem("자동 (로그에서 추정)", None)
        for n in range(1, 7):
            self.cells_combo.addItem(f"{n}S", n)
        self.cells_hint = QLabel("")
        self.cells_hint.setStyleSheet("color: palette(placeholder-text);")
        self.capacity_spin = OptionalSpin(50, 20000, suffix=" mAh")
        self.weight_spin = OptionalDoubleSpin(5, 5000, 0, " g")
        self.kv_spin = OptionalSpin(100, 30000, suffix=" KV")
        self.prop_spin = OptionalDoubleSpin(0.5, 15, 1, " 인치")
        self.mid_spin = OptionalSpin(0, 100, "헤더 값")
        self.expo_spin = OptionalSpin(0, 100, "헤더 값")
        f.addRow("기체 이름", self.name_edit)
        f.addRow("기체 등급 *", self.class_combo)
        cells_row = QHBoxLayout()
        cells_row.addWidget(self.cells_combo, 1)
        cells_row.addWidget(self.cells_hint)
        f.addRow("배터리 셀 수 *", cells_row)
        f.addRow("배터리 용량", self.capacity_spin)
        f.addRow("이륙 중량 (배터리 포함)", self.weight_spin)
        f.addRow("모터 KV", self.kv_spin)
        f.addRow("프롭 크기", self.prop_spin)
        f.addRow("현재 thr_mid", self.mid_spin)
        f.addRow("현재 thr_expo", self.expo_spin)
        lay.addWidget(g)

        # 로그 파일
        g = QGroupBox("로그 파일 *")
        v = QVBoxLayout(g)
        self.file_list = FileDropList()
        self.file_list.filesDropped.connect(self.add_files)
        v.addWidget(self.file_list)
        h = QHBoxLayout()
        add_btn = QPushButton("파일 선택…")
        add_btn.clicked.connect(self._choose_files)
        rm_btn = QPushButton("선택 제거")
        rm_btn.clicked.connect(self._remove_selected_files)
        h.addWidget(add_btn)
        h.addWidget(rm_btn)
        h.addStretch(1)
        v.addLayout(h)
        lay.addWidget(g)

        # 비행 선택
        g = QGroupBox("비행 선택")
        v = QVBoxLayout(g)
        self.flight_tree = QTreeWidget()
        self.flight_tree.setHeaderLabels(["비행", "크기", "시작 시각", "길이"])
        self.flight_tree.setRootIsDecorated(True)
        self.flight_tree.setMinimumHeight(110)
        hdr = self.flight_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        v.addWidget(self.flight_tree)
        lay.addWidget(g)

        # 분석 모드 + 실행
        g = QGroupBox("분석")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for key, label in config.MODES.items():
            rb = QRadioButton(label)
            rb.setProperty("mode", key)
            rb.setChecked(key == config.MODE_STRICT)
            self.mode_group.addButton(rb)
            h.addWidget(rb)
        v.addLayout(h)
        self.run_btn = QPushButton("분석 실행")
        self.run_btn.setMinimumHeight(36)
        f2 = self.run_btn.font()
        f2.setBold(True)
        self.run_btn.setFont(f2)
        self.run_btn.clicked.connect(self.start_analysis)
        v.addWidget(self.run_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        v.addWidget(self.progress)
        v.addWidget(self.status_label)
        lay.addWidget(g)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(380)
        return scroll

    # ----- 결과 패널 ---------------------------------------------------------

    def _build_result_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tabs = QTabWidget()

        # 요약
        summary = QWidget()
        sv = QVBoxLayout(summary)
        self.hero_label = QLabel()
        hf = self.hero_label.font()
        hf.setPointSize(26)
        hf.setBold(True)
        self.hero_label.setFont(hf)
        self.hero_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.hero_sub = QLabel()
        self.hero_sub.setStyleSheet("color: palette(placeholder-text);")
        self.retry_btn = QPushButton("일반 모드로 재분석")
        self.retry_btn.clicked.connect(self._retry_normal)
        self.retry_btn.setVisible(False)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(self.hero_label)
        col.addWidget(self.hero_sub)
        top.addLayout(col, 1)
        top.addWidget(self.retry_btn, 0, Qt.AlignTop)
        sv.addLayout(top)
        self.summary_view = QTextBrowser()
        self.summary_view.setOpenExternalLinks(False)
        sv.addWidget(self.summary_view, 1)
        self.tabs.addTab(summary, "요약")

        # 그래프
        graphs = QWidget()
        gv = QVBoxLayout(graphs)
        h = QHBoxLayout()
        h.addWidget(QLabel("시간축 그래프 비행:"))
        self.flight_combo = QComboBox()
        self.flight_combo.currentIndexChanged.connect(self._draw_timeline)
        h.addWidget(self.flight_combo, 1)
        gv.addLayout(h)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        self.plot_time = PlotPanel(3.2)
        self.plot_volt = PlotPanel(3.2)
        self.plot_hist = PlotPanel(3.0)
        for p in (self.plot_time, self.plot_volt, self.plot_hist):
            iv.addWidget(p)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(inner)
        gv.addWidget(sc, 1)
        self.tabs.addTab(graphs, "그래프")

        # CLI
        cli = QWidget()
        cv = QVBoxLayout(cli)
        self.cli_text = QPlainTextEdit()
        self.cli_text.setReadOnly(True)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(11)
        self.cli_text.setFont(mono)
        cv.addWidget(self.cli_text, 1)
        h = QHBoxLayout()
        self.cli_note = QLabel("Betaflight Configurator → CLI 탭에 붙여 넣고 Enter 를 누르세요.")
        self.cli_note.setWordWrap(True)
        self.copy_btn = QPushButton("클립보드 복사")
        self.copy_btn.clicked.connect(self._copy_cli)
        h.addWidget(self.cli_note, 1)
        h.addWidget(self.copy_btn)
        cv.addLayout(h)
        self.tabs.addTab(cli, "CLI")

        lay.addWidget(self.tabs, 1)
        h = QHBoxLayout()
        h.addStretch(1)
        self.csv_btn = QPushButton("호버 원시 데이터 CSV…")
        self.csv_btn.clicked.connect(self._export_csv)
        self.save_btn = QPushButton("결과 저장 (HTML)")
        self.save_btn.clicked.connect(self._save_report)
        h.addWidget(self.csv_btn)
        h.addWidget(self.save_btn)
        lay.addLayout(h)
        return w

    # ----- 프리셋 ------------------------------------------------------------

    def _reload_presets(self, select: str | None = None) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItem("(프리셋 선택)", None)
        for n in self.presets.names():
            self.preset_combo.addItem(n, n)
        if select:
            self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(select)))

    def _on_preset_selected(self, _i: int) -> None:
        name = self.preset_combo.currentData()
        craft = self.presets.get(name) if name else None
        if craft:
            self.set_craft(craft)

    def _save_preset(self) -> None:
        default = self.name_edit.text().strip() or (self.preset_combo.currentData() or "")
        name, ok = QInputDialog.getText(self, "프리셋 저장", "프리셋 이름:", text=default)
        name = name.strip()
        if not ok or not name:
            return
        if name in self.presets.names() and QMessageBox.question(
            self, "프리셋 저장", f"'{name}' 프리셋을 덮어쓸까요?"
        ) != QMessageBox.Yes:
            return
        craft = self.craft_input()
        craft.name = name
        self.name_edit.setText(name)
        try:
            self.presets.put(name, craft)
        except OSError as e:
            self._error("프리셋을 저장하지 못했습니다.", f"{self.presets.path}\n{e}")
            return
        self._reload_presets(name)

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentData()
        if not name:
            return
        if QMessageBox.question(self, "프리셋 삭제", f"'{name}' 프리셋을 삭제할까요?") == QMessageBox.Yes:
            self.presets.delete(name)
            self._reload_presets()

    # ----- 입력 값 -----------------------------------------------------------

    def mode(self) -> str:
        b = self.mode_group.checkedButton()
        return b.property("mode") if b else config.MODE_STRICT

    def set_mode(self, mode: str) -> None:
        for b in self.mode_group.buttons():
            b.setChecked(b.property("mode") == mode)

    def craft_input(self) -> pipeline.CraftInput:
        return pipeline.CraftInput(
            craft_class=self.class_combo.currentData(),
            cells=self.cells_combo.currentData(),
            name=self.name_edit.text().strip(),
            capacity_mah=self.capacity_spin.get(),
            weight_g=self.weight_spin.get(),
            motor_kv=self.kv_spin.get(),
            prop_in=self.prop_spin.get(),
            thr_mid=self.mid_spin.get(),
            thr_expo=self.expo_spin.get(),
            mode=self.mode(),
        )

    def set_craft(self, c: pipeline.CraftInput) -> None:
        self.name_edit.setText(c.name or "")
        self.class_combo.setCurrentIndex(max(0, self.class_combo.findData(c.craft_class)))
        self.cells_combo.setCurrentIndex(0 if c.cells is None else max(0, self.cells_combo.findData(c.cells)))
        self.capacity_spin.put(c.capacity_mah)
        self.weight_spin.put(c.weight_g)
        self.kv_spin.put(c.motor_kv)
        self.prop_spin.put(c.prop_in)
        self.mid_spin.put(c.thr_mid)
        self.expo_spin.put(c.thr_expo)
        self.set_mode(c.mode)

    # ----- 파일 --------------------------------------------------------------

    def _choose_files(self) -> None:
        start = str(Path(self._files[-1]).parent) if self._files else ""
        paths, _ = QFileDialog.getOpenFileNames(self, "로그 파일 선택", start, FILE_FILTER)
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list[str]) -> None:
        bad = []
        for p in paths:
            p = str(Path(p))
            if Path(p).suffix.lower() not in SUPPORTED_EXTS:
                bad.append(Path(p).name)
                continue
            if p in self._files:
                continue
            try:
                flights = list_flights(p)
            except (LogReadError, OSError) as e:
                self._error(f"{Path(p).name} 을(를) 열 수 없습니다.", getattr(e, "message", str(e)))
                continue
            self._files.append(p)
            self.file_list.addItem(Path(p).name)
            self.file_list.item(self.file_list.count() - 1).setToolTip(p)
            self._add_flight_items(p, flights)
            self._autofill_from_headers(p)
        if bad:
            self._error("지원하지 않는 파일 형식입니다.", ", ".join(bad) + "\n지원 형식: .bbl, .bfl, .txt, .csv")

    def _add_flight_items(self, path: str, flights) -> None:
        top = QTreeWidgetItem([Path(path).name, "", "", ""])
        top.setData(0, Qt.UserRole, path)
        top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        top.setCheckState(0, Qt.Checked)
        for f in flights:
            size = f"{f.size_bytes / 1024:.0f} KB" if f.size_bytes else ""
            child = QTreeWidgetItem([f"비행 #{f.index}", size, f.start_time or "", ""])
            child.setData(0, Qt.UserRole, (path, f.index))
            child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
            child.setCheckState(0, Qt.Checked)
            top.addChild(child)
        self.flight_tree.addTopLevelItem(top)
        top.setExpanded(True)

    def _autofill_from_headers(self, path: str) -> None:
        """헤더에서 읽을 수 있는 값은 비어 있는 입력란에만 채운다."""
        h = read_headers(path)
        if not h:
            return
        mid, expo = normalize.rate_profile_values(h)
        if self.mid_spin.get() is None and mid is not None:
            self.mid_spin.put(mid)
        if self.expo_spin.get() is None and expo is not None:
            self.expo_spin.put(expo)
        cells = normalize.header_value(h, "vbatcellcount", "vbat_cellcount", "cellCount")
        if self.cells_combo.currentData() is None and isinstance(cells, int) and 1 <= cells <= 6:
            self.cells_combo.setCurrentIndex(self.cells_combo.findData(cells))

    def _remove_selected_files(self) -> None:
        rows = sorted((self.file_list.row(i) for i in self.file_list.selectedItems()), reverse=True)
        for r in rows:
            path = self._files.pop(r)
            self.file_list.takeItem(r)
            for i in range(self.flight_tree.topLevelItemCount()):
                if self.flight_tree.topLevelItem(i).data(0, Qt.UserRole) == path:
                    self.flight_tree.takeTopLevelItem(i)
                    break

    def selected_targets(self) -> list[tuple[str, int]]:
        out = []
        for i in range(self.flight_tree.topLevelItemCount()):
            top = self.flight_tree.topLevelItem(i)
            for j in range(top.childCount()):
                c = top.child(j)
                if c.checkState(0) == Qt.Checked:
                    out.append(tuple(c.data(0, Qt.UserRole)))
        return out

    # ----- 분석 --------------------------------------------------------------

    def start_analysis(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self._files:
            self._error("로그 파일을 첨부하세요.", "파일 선택 버튼을 누르거나 BBL/CSV 파일을 목록에 끌어다 놓으세요.")
            return
        targets = self.selected_targets()
        if not targets:
            self._error("분석할 비행을 선택하세요.", "비행 선택 목록에서 하나 이상 체크하세요.")
            return
        self.run_btn.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("분석 중…")
        self.worker = AnalysisWorker(targets, self.craft_input())
        self.worker.progressed.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, p: float, msg: str) -> None:
        self.progress.setValue(int(p * 1000))
        self.status_label.setText(Path(msg.split(" #")[0]).name + (" #" + msg.split(" #", 1)[1] if " #" in msg else ""))

    def _on_failed(self, tb: str) -> None:
        self.run_btn.setEnabled(True)
        self.status_label.setText("분석 실패")
        self._error("분석 중 예상하지 못한 오류가 발생했습니다.",
                    f"상세 내용은 {app_dir() / 'app.log'} 에 기록됐습니다.\n\n{tb[-600:]}")

    def _on_done(self, res: pipeline.AnalysisResult) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setValue(1000)
        self.status_label.setText(f"완료 — 비행 {len(res.flights)}개 분석")
        self.show_result(res)

        for path, err in res.errors:
            detail = err.hint
            if isinstance(err, MissingFieldsError):
                detail = "누락된 필드: " + ", ".join(err.missing) + "\n\n" + err.hint
            self._error(f"{Path(path.rsplit(' #', 1)[0]).name}: {err.message}", detail)
        if res.flights and not res.combined:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("호버 구간 없음")
            box.setText("안정 호버 구간을 찾지 못했습니다.")
            box.setInformativeText("10~20초 정도 제자리 호버를 포함해 다시 기록하세요.")
            retry = None
            if res.craft.mode == config.MODE_STRICT:
                retry = box.addButton("일반 모드로 재시도", QMessageBox.AcceptRole)
            box.addButton("닫기", QMessageBox.RejectRole)
            box.exec()
            if retry is not None and box.clickedButton() is retry:
                self._retry_normal()

    def _retry_normal(self) -> None:
        self.set_mode(config.MODE_NORMAL)
        self.start_analysis()

    # ----- 결과 표시 ---------------------------------------------------------

    def _show_empty(self) -> None:
        self.hero_label.setText("—")
        self.hero_sub.setText("기체 정보와 로그 파일을 입력하고 [분석 실행]을 누르세요.")
        self.summary_view.setHtml("")
        self.cli_text.setPlainText("")
        for b in (self.save_btn, self.csv_btn, self.copy_btn):
            b.setEnabled(False)

    def show_result(self, res: pipeline.AnalysisResult) -> None:
        self.result = res
        big, sub = report_html.headline(res)
        self.hero_label.setText(big)
        self.hero_sub.setText(sub)
        self.summary_view.setHtml(report_html.summary_html(res))
        self.retry_btn.setVisible(res.craft.mode == config.MODE_STRICT and bool(res.flights) and
                                  (res.combined is None or res.low_confidence))
        rec = res.recommendation
        self.cli_text.setPlainText(rec.cli if rec else "# 호버 구간이 없어 추천값을 만들 수 없습니다.")
        self.cli_note.setText(
            ("⚠ " + " ".join(rec.warnings) + "\n" if rec and rec.warnings else "")
            + "Betaflight Configurator → CLI 탭에 붙여 넣고 Enter 를 누르세요."
        )
        has = bool(res.flights)
        self.copy_btn.setEnabled(rec is not None)
        self.save_btn.setEnabled(has)
        self.csv_btn.setEnabled(res.combined is not None)

        # 셀 수 추정값 표시, 비행 길이 채우기
        cells = {fa.scale_info.get("cells") for fa in res.flights if fa.scale_info.get("cells_estimated")}
        self.cells_hint.setText(f"추정: {', '.join(f'{c}S' for c in cells if c)}" if cells else "")
        lengths = {(fa.log.path, fa.log.index): fa.detection.df["t"].iloc[-1] for fa in res.flights}
        for i in range(self.flight_tree.topLevelItemCount()):
            top = self.flight_tree.topLevelItem(i)
            for j in range(top.childCount()):
                c = top.child(j)
                key = tuple(c.data(0, Qt.UserRole))
                if key in lengths:
                    m, s = divmod(int(lengths[key]), 60)
                    c.setText(3, f"{m}:{s:02d}")

        self.flight_combo.blockSignals(True)
        self.flight_combo.clear()
        for fa in res.flights:
            self.flight_combo.addItem(fa.log.label)
        self.flight_combo.blockSignals(False)
        self._draw_timeline()
        if res.flights:
            self.plot_volt.draw(plots.plot_voltage, res)
            self.plot_hist.draw(plots.plot_histogram, res)

    def _draw_timeline(self, *_):
        if not self.result or not self.result.flights:
            return
        i = max(0, self.flight_combo.currentIndex())
        self.plot_time.draw(plots.plot_timeline, self.result.flights[i])

    # ----- 내보내기 ----------------------------------------------------------

    def _copy_cli(self) -> None:
        QApplication.clipboard().setText(self.cli_text.toPlainText())
        self.status_label.setText("CLI 명령어를 클립보드에 복사했습니다.")

    def _save_report(self) -> None:
        if not self.result or not self.result.flights:
            return
        default = report_html.default_report_path(self.result)
        try:
            path = report_html.save_report(self.result, default)
        except OSError:
            path_str, _ = QFileDialog.getSaveFileName(self, "리포트 저장", str(default.name), "HTML (*.html)")
            if not path_str:
                return
            try:
                path = report_html.save_report(self.result, Path(path_str))
            except OSError as e:
                self._error("리포트를 저장하지 못했습니다.", str(e))
                return
        self.status_label.setText(f"리포트 저장: {path}")
        QMessageBox.information(self, "결과 저장", f"리포트를 저장했습니다.\n{path}")

    def _export_csv(self) -> None:
        if not self.result or not self.result.flights:
            return
        first = Path(self.result.flights[0].log.path)
        path, _ = QFileDialog.getSaveFileName(
            self, "호버 원시 데이터 저장", str(first.with_name(f"{first.stem}_hover_samples.csv")), "CSV (*.csv)"
        )
        if not path:
            return
        try:
            report_html.save_hover_csv(self.result, Path(path))
        except OSError as e:
            self._error("CSV를 저장하지 못했습니다.", str(e))
            return
        self.status_label.setText(f"CSV 저장: {path}")

    # ----- 공용 --------------------------------------------------------------

    def _error(self, message: str, detail: str = "") -> None:
        _log.warning("dialog: %s | %s", message, detail)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("HoverThrottle")
        box.setText(message)
        if detail:
            box.setInformativeText(detail)
        box.exec()

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.wait(2000)
        super().closeEvent(e)


def run_app(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(config.APP_NAME)
    if sys.platform == "win32":
        app.setFont(QFont("Malgun Gothic", 9))
    win = MainWindow()
    win.show()
    files = [a for a in (argv or sys.argv)[1:] if Path(a).is_file()]
    if files:
        win.add_files(files)
    return app.exec()
