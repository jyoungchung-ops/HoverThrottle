import numpy as np
import pandas as pd
import pytest

from hoverthrottle import config
from hoverthrottle.analysis.recommend import (
    inverse_throttle_curve,
    recommend,
    throttle_curve,
    twr_estimate,
)
from hoverthrottle.io import normalize


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("time", ("time", None)),
        (" time (us)", ("time", "us")),
        ("rcCommand[3]", ("rcCommand[3]", None)),
        (" rcCommand[3] ", ("rcCommand[3]", None)),
        ("rcCommands[0]", ("rcCommand[0]", None)),
        ("motor[7]", ("motor[7]", None)),
        ("vbatLatest (V)", ("vbatLatest", "V")),
        ("amperageLatest (A)", ("amperageLatest", "A")),
        ("gyroADC[2]", ("gyroADC[2]", None)),
        ("accSmooth[2]", ("accSmooth[2]", None)),
    ],
)
def test_canonical_name(raw, expected):
    assert normalize.canonical_name(raw) == expected


def test_motor_normalization_uses_header():
    df = pd.DataFrame(
        {
            "time": [0, 1000, 2000],
            "rcCommand[0]": 0, "rcCommand[1]": 0, "rcCommand[2]": 0,
            "rcCommand[3]": [1000, 1500, 2000],
            "motor[0]": [158, 1102.5, 2047], "motor[1]": [158, 1102.5, 2047],
            "gyroADC[0]": 0, "gyroADC[1]": 0, "gyroADC[2]": 0,
        }
    )
    out, info = normalize.derive(df, {"motorOutput": [158, 2047]}, {}, 4)
    assert info["motor_range_source"] == "header"
    assert out["motor_pct"].tolist() == pytest.approx([0, 50, 100])
    assert out["thr_pct"].tolist() == pytest.approx([0, 50, 100])


@pytest.mark.parametrize(
    "volts,cells",
    [(4.2, 1), (3.9, 1), (8.4, 2), (12.6, 3), (16.4, 4), (16.8, 4), (25.2, 6), (17.4, 4)],
)
def test_cell_count_estimate(volts, cells):
    v = np.full(200, volts)
    assert normalize.estimate_cell_count(v) == cells


def test_cell_count_header_wins():
    assert normalize.estimate_cell_count(np.full(10, 16.8), {"vbatcellcount": 6}) == 6


@pytest.mark.parametrize(
    "raw,headers,unit,volts",
    [
        (1680, {"Firmware revision": "Betaflight 4.3.1"}, None, 16.8),
        (16.8, {}, "V", 16.8),
        (1680, {}, None, 16.8),
        (168, {}, None, 16.8),
        (16.8, {}, None, 16.8),
    ],
)
def test_vbat_scale(raw, headers, unit, volts):
    out, _ = normalize.vbat_to_volts(np.array([raw], dtype=float), headers, unit)
    assert out[0] == pytest.approx(volts)


def test_gyro_scale_bits():
    assert normalize.gyro_scale_deg({"gyro_scale": 0x3F800000}) == pytest.approx(1.0)
    assert normalize.gyro_scale_deg({}) == 1.0


def test_throttle_curve_properties():
    x = np.linspace(0, 100, 101)
    assert throttle_curve(x, 50, 0) == pytest.approx(x)
    for mid, expo in [(28, 25), (40, 50), (60, 100), (15, 35)]:
        y = throttle_curve(x, mid, expo)
        # 11점 선형보간이라 mid 가 10의 배수가 아니면 아주 약간 어긋난다 (Betaflight 와 동일)
        assert throttle_curve(np.array([mid]), mid, expo)[0] == pytest.approx(mid, abs=0.5)
        assert y[0] == pytest.approx(0) and y[-1] == pytest.approx(100)
        assert np.all(np.diff(y) >= 0)
        assert inverse_throttle_curve(y, mid, expo) == pytest.approx(x, abs=1e-6)


def test_recommend_typical_5inch():
    rec = recommend(28.3, 24.0, config.CLASS_5INCH, weight_g=650)
    assert rec.thr_mid == 28
    assert rec.thr_expo == 25
    assert rec.status_level == "normal"
    assert rec.throttle_limit_percent is None
    assert "set thr_mid = 28" in rec.cli and "set thr_expo = 25" in rec.cli
    assert rec.cli.strip().endswith("save")
    lo, hi = rec.max_thrust_g
    assert lo < hi


def test_recommend_overpowered_suggests_limit():
    rec = recommend(14.0, 12.0, config.CLASS_5INCH)
    assert rec.thr_expo == 35
    assert rec.throttle_limit_percent == 60      # 12/0.25 = 48 → 60 으로 제한
    assert rec.status_level == "below"
    assert "# set throttle_limit_percent = 60" in rec.cli


def test_recommend_limits_and_warnings():
    assert recommend(5.0, 4.0, config.CLASS_5INCH).thr_mid == 10
    assert recommend(95.0, 90.0, config.CLASS_TINYWHOOP).thr_mid == 90
    assert recommend(50, 50, config.CLASS_TINYWHOOP).thr_expo == 5
    assert recommend(30, 25, config.CLASS_5INCH, is_bf4=False).warnings


def test_twr():
    twr, thrust = twr_estimate(25.0, 600)
    assert twr == pytest.approx((4 ** 1.3, 4 ** 1.8))
    assert thrust == pytest.approx((600 * 4 ** 1.3, 600 * 4 ** 1.8))
