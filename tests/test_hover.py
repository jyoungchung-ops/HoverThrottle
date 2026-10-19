from pathlib import Path

import numpy as np
import pytest

from hoverthrottle import config
from hoverthrottle.analysis import pipeline
from hoverthrottle.analysis.hover_detect import detect_hover
from hoverthrottle.io import normalize
from hoverthrottle.io.log_reader import LogData, MissingFieldsError, read_log

from synth import SCENARIO, expected_motor_median, make_log, to_blackbox_decode_csv, to_explorer_csv


def _analyze_df(df, headers, mode=config.MODE_STRICT, cells=4):
    log = LogData(path="synthetic.bbl", index=1, df=df, headers=headers, source="test")
    return pipeline.analyze_log(log, pipeline.CraftInput(craft_class=config.CLASS_5INCH, cells=cells, mode=mode))


def _ranges(kind):
    return [(s, e) for s, e, k in SCENARIO if k == kind]


def _overlaps(seg, ranges, start_offset):
    s, e = seg
    return any(s + start_offset < re_ and e + start_offset > rs for rs, re_ in ranges)


@pytest.mark.parametrize("hover_thr", [18.0, 30.0, 45.0])
def test_synthetic_hover_within_1pct(hover_thr):
    df, headers = make_log(hover_thr=hover_thr)
    fa = _analyze_df(df, headers)
    r = fa.result
    assert r.stick_median == pytest.approx(hover_thr, abs=1.0)
    assert r.motor_median == pytest.approx(expected_motor_median(hover_thr), abs=1.0)
    assert r.hover_total_s > 30
    assert not r.low_confidence


def test_maneuvers_and_cruise_excluded_in_strict():
    df, headers = make_log()
    fa = _analyze_df(df, headers)
    # 합성 로그의 time 은 0 에서 시작하므로 t 와 시나리오 시간이 같다
    for seg in fa.detection.segments:
        assert not _overlaps(seg, _ranges("maneuver"), 0), seg
        assert not _overlaps(seg, _ranges("cruise"), 0), seg
        # 기동 직후 1초 홀드오프
        assert not (45.0 <= seg[0] < 45.9), seg


def test_takeoff_landing_trimmed():
    df, headers = make_log()
    fa = _analyze_df(df, headers)
    first_air, last_air = fa.detection.airborne[0], fa.detection.airborne[-1]
    assert first_air[0] >= 1.0 + config.TAKEOFF_LANDING_TRIM_S - 0.1
    assert last_air[1] <= 113.0 - config.TAKEOFF_LANDING_TRIM_S + 0.1


def test_normal_mode_includes_cruise():
    df, headers = make_log()
    fa = _analyze_df(df, headers, mode=config.MODE_NORMAL)
    assert any(_overlaps(seg, _ranges("cruise"), 0) for seg in fa.detection.segments)


def test_voltage_regression_detects_sag():
    df, headers = make_log()
    v = _analyze_df(df, headers).result.voltage
    assert v.reliable and v.slope < 0
    preds = {p[0]: p[2] for p in v.predictions}
    assert preds[3.5] > preds[3.8] > preds[4.1]


def test_no_hover_found():
    scenario = [(0, 1, "ground"), (1, 3, "takeoff"), (3, 40, "maneuver"), (40, 43, "landing"), (43, 45, "ground")]
    df, headers = make_log(scenario=scenario)
    fa = _analyze_df(df, headers)
    assert fa.result.n_samples == 0
    assert fa.result.stick_median is None


def test_without_acc_still_detects():
    df, headers = make_log()
    df = df.drop(columns=["accSmooth[2]"])
    fa = _analyze_df(df, headers)
    assert fa.result.stick_median == pytest.approx(30, abs=1)
    assert any("가속도" in n for n in fa.detection.notes)


def test_blackbox_decode_csv_roundtrip(tmp_path):
    df, _ = make_log()
    p = tmp_path / "LOG00001.01.csv"
    to_blackbox_decode_csv(df, p)
    log = read_log(str(p))
    assert log.units.get("vbatLatest") == "V"
    assert log.units.get("time") == "us"
    res = pipeline.run([(str(p), 1)], pipeline.CraftInput(craft_class=config.CLASS_5INCH))
    fa = res.flights[0]
    # 헤더가 없어도 DShot 범위 추정, 셀 수 자동 추정
    assert fa.scale_info["motor_range_source"] == "guess-dshot"
    assert fa.scale_info["cells"] == 4
    assert res.combined[0] == pytest.approx(30, abs=1)


def test_explorer_csv_headers(tmp_path):
    df, headers = make_log()
    p = tmp_path / "export.csv"
    to_explorer_csv(df, headers, p)
    log = read_log(str(p))
    assert log.headers["motorOutput"] == [48, 2047]
    assert log.headers["acc_1G"] == 2048
    res = pipeline.run([(str(p), 1)], pipeline.CraftInput(craft_class=config.CLASS_5INCH, cells=4))
    assert res.flights[0].scale_info["motor_range_source"] == "header"
    assert res.recommendation.thr_mid == 30
    assert res.recommendation.current_thr_mid == 50


def test_missing_fields(tmp_path):
    df, _ = make_log()
    p = tmp_path / "nomotor.csv"
    df.drop(columns=[c for c in df.columns if c.startswith("motor")]).to_csv(p, index=False)
    with pytest.raises(MissingFieldsError) as ei:
        read_log(str(p))
    assert "motor[0]" in ei.value.missing


FIXTURES = Path(__file__).parent / "fixtures"
REAL_LOGS = sorted(p for p in FIXTURES.glob("*") if p.suffix.lower() in (".bbl", ".bfl", ".csv", ".txt"))


@pytest.mark.skipif(not REAL_LOGS, reason="tests/fixtures 에 실제 로그가 없음")
@pytest.mark.parametrize("path", REAL_LOGS, ids=lambda p: p.name)
def test_real_log_parses(path):
    log = read_log(str(path))
    derived, info = normalize.derive(log.df, log.headers, log.units, None)
    assert derived["thr_pct"].between(0, 100).all()
    assert derived["motor_pct"].between(0, 100).all()
    det = detect_hover(derived)
    assert np.isfinite(det.sample_rate_hz)
