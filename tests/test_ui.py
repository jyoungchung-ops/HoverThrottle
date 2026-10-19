import os

import pytest

from hoverthrottle import config
from hoverthrottle.analysis import pipeline
from hoverthrottle.presets import PresetStore
from hoverthrottle.ui import report_html

from synth import make_log, to_explorer_csv


@pytest.fixture(scope="module")
def synth_csv(tmp_path_factory):
    df, headers = make_log(hover_thr=28, cells=6)
    p = tmp_path_factory.mktemp("logs") / "LOG00007.csv"
    to_explorer_csv(df, headers, p)
    return p


@pytest.fixture(scope="module")
def result(synth_csv):
    return pipeline.run([(str(synth_csv), 1)], pipeline.CraftInput(craft_class=config.CLASS_5INCH, weight_g=700))


def test_presets_roundtrip(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    craft = pipeline.CraftInput(craft_class=config.CLASS_TINYWHOOP, cells=1, weight_g=31.5, thr_mid=40, mode=config.MODE_NORMAL)
    store.put("메테오75", craft)
    again = PresetStore(tmp_path / "presets.json")
    assert again.names() == ["메테오75"]
    got = again.get("메테오75")
    assert got.craft_class == config.CLASS_TINYWHOOP and got.cells == 1 and got.weight_g == 31.5
    assert got.name == "메테오75" and got.mode == config.MODE_NORMAL
    again.delete("메테오75")
    assert PresetStore(tmp_path / "presets.json").names() == []


def test_presets_corrupt_file(tmp_path):
    p = tmp_path / "presets.json"
    p.write_text("{not json", encoding="utf-8")
    assert PresetStore(p).names() == []


def test_report_html(result, synth_csv):
    path = report_html.save_report(result)
    assert path == synth_csv.with_name("LOG00007_hover_report.html")
    html = path.read_text(encoding="utf-8")
    assert "스틱 28% / 모터" in html
    assert "set thr_mid = 28" in html
    assert html.count("data:image/png;base64,") == 3
    assert "근사치" in html and "참고 범위" in html


def test_report_hides_voltage_without_field(synth_csv, tmp_path):
    df, headers = make_log(hover_thr=28, cells=6)
    p = tmp_path / "novbat.csv"
    to_explorer_csv(df.drop(columns=["vbatLatest"]), headers, p)
    res = pipeline.run([(str(p), 1)], pipeline.CraftInput(craft_class=config.CLASS_5INCH, cells=6))
    assert res.combined is not None
    assert "전압별 예상 호버" not in report_html.summary_html(res)


def test_hover_csv(result, tmp_path):
    out = report_html.save_hover_csv(result, tmp_path / "h.csv")
    import pandas as pd

    df = pd.read_csv(out, encoding="utf-8-sig")
    assert len(df) == result.flights[0].result.n_samples
    assert {"flight", "t", "thr_s", "motor_s", "cell_v"} <= set(df.columns)


def test_main_window_end_to_end(synth_csv, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from hoverthrottle.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow(presets=PresetStore(tmp_path / "presets.json"))
    win.add_files([str(synth_csv)])
    assert win.selected_targets() == [(str(synth_csv), 1)]
    assert win.mid_spin.get() == 50          # 헤더에서 자동 채움
    win.start_analysis()
    win.worker.wait(60000)
    app.processEvents()
    assert win.result is not None and win.result.recommendation.thr_mid == 28
    assert win.hero_label.text().startswith("스틱 28%")
    assert "set thr_expo = 25" in win.cli_text.toPlainText()
    assert win.cells_hint.text() == "추정: 6S"
    win.close()
