"""섹션 6: 호버 스로틀 산출과 추천값 계산."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import config
from .hover_detect import HoverDetection


# ---------------------------------------------------------------------------
# Betaflight 스로틀 곡선 (rc.c generateThrottleCurve 와 같은 식)
# ---------------------------------------------------------------------------
# 블랙박스의 rcCommand[3] 은 thr_mid/thr_expo 곡선을 *통과한 뒤* 값이다.
# 곡선은 (thr_mid, thr_mid) 를 (11점 보간 오차 범위에서) 지나므로, 호버에 필요한 rcCommand 값을 그대로
# thr_mid 로 두면 곡선의 평평한 중심이 실제 호버 지점에 놓인다.

def throttle_curve(stick_pct: np.ndarray, mid: float, expo: float) -> np.ndarray:
    """스틱 위치 % → rcCommand 스로틀 % (11점 룩업 + 선형보간, Betaflight 와 동일)."""
    xs = np.arange(0, 101, 10, dtype=float)
    tmp = xs - mid
    y = np.where(tmp > 0, 100 - mid, np.where(tmp < 0, mid, 1.0))
    ys = mid + tmp * (100 - expo + expo * tmp * tmp / (y * y)) / 100.0
    return np.interp(stick_pct, xs, ys)


def inverse_throttle_curve(out_pct: np.ndarray, mid: float, expo: float) -> np.ndarray:
    """rcCommand 스로틀 % → 실제 스틱 위치 %."""
    xs = np.arange(0, 101, 10, dtype=float)
    ys = throttle_curve(xs, mid, expo)
    return np.interp(out_pct, ys, xs)


# ---------------------------------------------------------------------------
# 결과 구조
# ---------------------------------------------------------------------------

@dataclass
class VoltageFit:
    slope: float | None = None           # 모터 % / 셀당 V
    intercept: float | None = None
    r2: float | None = None
    predictions: list[tuple[float, str, float]] = field(default_factory=list)  # (V, 라벨, 모터 %)
    reliable: bool = False
    message: str = ""


@dataclass
class FlightResult:
    label: str
    mode: str
    n_samples: int
    hover_total_s: float
    n_segments: int
    low_confidence: bool
    stick_median: float | None = None     # rcCommand 기준 스로틀 % (곡선 적용 후)
    stick_q1: float | None = None
    stick_q3: float | None = None
    motor_median: float | None = None
    motor_q1: float | None = None
    motor_q3: float | None = None
    raw_stick_median: float | None = None  # 곡선을 역산한 실제 스틱 위치 %
    voltage: VoltageFit = field(default_factory=VoltageFit)


@dataclass
class Recommendation:
    stick_pct: float
    motor_pct: float
    thr_mid: int
    thr_expo: int
    thr_expo_range: tuple[int, int]
    thr_expo_meaning: str
    current_thr_mid: int | None = None
    current_thr_expo: int | None = None
    throttle_limit_percent: int | None = None
    status_range: tuple[float, float] | None = None
    status_text: str = ""
    status_level: str = ""                 # "normal" / "above" / "below"
    twr_range: tuple[float, float] | None = None
    max_thrust_g: tuple[float, float] | None = None
    cli: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 6-1. 호버 스로틀 산출
# ---------------------------------------------------------------------------

def _quartiles(x: np.ndarray) -> tuple[float, float, float]:
    q1, med, q3 = np.nanpercentile(x, [25, 50, 75])
    return float(med), float(q1), float(q3)


def voltage_fit(cell_v: np.ndarray, motor: np.ndarray, t: np.ndarray) -> VoltageFit:
    """호버 샘플에서 셀당 전압 → 모터 % 선형회귀 (0.1초 단위로 묶어 자기상관을 줄인다)."""
    ok = np.isfinite(cell_v) & np.isfinite(motor)
    if ok.sum() < 20:
        return VoltageFit(message="전압 데이터 부족")
    bins = np.floor(t[ok] / 0.1).astype(np.int64)
    _, inv = np.unique(bins, return_inverse=True)
    cnt = np.bincount(inv)
    v = np.bincount(inv, cell_v[ok]) / cnt
    m = np.bincount(inv, motor[ok]) / cnt
    if len(v) < 10 or (np.max(v) - np.min(v)) < 0.05:
        return VoltageFit(message="전압 변화 폭이 작아 전압 영향 판단 불가")
    slope, intercept = np.polyfit(v, m, 1)
    pred = slope * v + intercept
    ss_res = float(np.sum((m - pred) ** 2))
    ss_tot = float(np.sum((m - m.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    fit = VoltageFit(slope=float(slope), intercept=float(intercept), r2=r2)
    fit.predictions = [(pv, label, float(slope * pv + intercept)) for pv, label in config.VOLTAGE_POINTS]
    fit.reliable = r2 >= config.VOLTAGE_R2_MIN
    fit.message = "" if fit.reliable else f"전압 영향 판단 불가 (R²={r2:.2f})"
    return fit


def flight_result(label: str, det: HoverDetection, current_mid: int | None = None, current_expo: int | None = None) -> FlightResult:
    df = det.df
    mask = det.mask
    res = FlightResult(
        label=label,
        mode=det.mode,
        n_samples=int(mask.sum()),
        hover_total_s=det.total_s,
        n_segments=len(det.segments),
        low_confidence=det.low_confidence,
    )
    if not mask.any():
        return res
    stick = df["thr_s"].to_numpy()[mask]
    motor = df["motor_s"].to_numpy()[mask]
    res.stick_median, res.stick_q1, res.stick_q3 = _quartiles(stick)
    res.motor_median, res.motor_q1, res.motor_q3 = _quartiles(motor)
    if current_mid is not None and current_expo is not None:
        res.raw_stick_median = float(inverse_throttle_curve(np.array([res.stick_median]), current_mid, current_expo)[0])
    if "cell_v" in df:
        res.voltage = voltage_fit(df["cell_v"].to_numpy()[mask], motor, df["t"].to_numpy()[mask])
    else:
        res.voltage = VoltageFit(message="전압 필드 없음")
    return res


def combine(results: list[FlightResult]) -> tuple[float, float] | None:
    """비행별 중앙값의 샘플 수 가중 평균 (스틱 %, 모터 %)."""
    good = [r for r in results if r.n_samples and r.stick_median is not None]
    if not good:
        return None
    w = np.array([r.n_samples for r in good], dtype=float)
    stick = float(np.average([r.stick_median for r in good], weights=w))
    motor = float(np.average([r.motor_median for r in good], weights=w))
    return stick, motor


# ---------------------------------------------------------------------------
# 6-2 ~ 6-7. 추천
# ---------------------------------------------------------------------------

def recommend_expo(stick_pct: float) -> tuple[int, tuple[int, int], str]:
    for upper, rng, meaning in config.THR_EXPO_TABLE:
        if stick_pct < upper:
            return int(round(sum(rng) / 2)), rng, meaning
    raise AssertionError("unreachable")


def class_status(craft_class: str, motor_pct: float) -> tuple[tuple[float, float], str, str]:
    spec = config.CLASS_HOVER_RANGE[craft_class]
    lo, hi = spec["range"]
    if motor_pct > hi:
        return (lo, hi), spec["above"], "above"
    if motor_pct < lo:
        return (lo, hi), spec["below"], "below"
    return (lo, hi), "정상 범위", "normal"


def twr_estimate(motor_pct: float, weight_g: float | None = None):
    h = motor_pct / 100.0
    if h <= 0:
        return None, None
    lo_n, hi_n = config.TWR_EXPONENTS
    twr = ((1 / h) ** lo_n, (1 / h) ** hi_n)
    thrust = (weight_g * twr[0], weight_g * twr[1]) if weight_g else None
    return twr, thrust


def build_cli(thr_mid: int, thr_expo: int, limit_percent: int | None) -> str:
    lines = [
        "# HoverThrottle 추천값 (현재 rateprofile에 적용)",
        f"set thr_mid = {thr_mid}",
        f"set thr_expo = {thr_expo}",
    ]
    if limit_percent is not None:
        lines += [
            "# 선택 사항: 스로틀 리밋 (펀치아웃 최대 출력이 줄어듭니다). 쓰려면 아래 두 줄의 # 를 지우세요.",
            "# set throttle_limit_type = SCALE",
            f"# set throttle_limit_percent = {limit_percent}",
        ]
    lines.append("save")
    return "\n".join(lines)


def recommend(
    stick_pct: float,
    motor_pct: float,
    craft_class: str,
    weight_g: float | None = None,
    current_mid: int | None = None,
    current_expo: int | None = None,
    is_bf4: bool = True,
) -> Recommendation:
    lo, hi = config.THR_MID_LIMITS
    thr_mid = int(min(max(round(stick_pct), lo), hi))
    expo, expo_rng, meaning = recommend_expo(stick_pct)

    limit = None
    if motor_pct < config.THROTTLE_LIMIT_SUGGEST_BELOW_MOTOR_PCT:
        llo, lhi = config.THROTTLE_LIMIT_PERCENT_RANGE
        limit = int(min(max(round(motor_pct / config.THROTTLE_LIMIT_TARGET_HOVER), llo), lhi))

    rng, text, level = class_status(craft_class, motor_pct)
    twr, thrust = twr_estimate(motor_pct, weight_g)

    rec = Recommendation(
        stick_pct=stick_pct,
        motor_pct=motor_pct,
        thr_mid=thr_mid,
        thr_expo=expo,
        thr_expo_range=expo_rng,
        thr_expo_meaning=meaning,
        current_thr_mid=current_mid,
        current_thr_expo=current_expo,
        throttle_limit_percent=limit,
        status_range=rng,
        status_text=text,
        status_level=level,
        twr_range=twr,
        max_thrust_g=thrust,
    )
    rec.cli = build_cli(thr_mid, expo, limit)
    if not is_bf4:
        rec.warnings.append("Betaflight 4.x 로그가 아닙니다 — 설정 이름이 다를 수 있습니다.")
    return rec
