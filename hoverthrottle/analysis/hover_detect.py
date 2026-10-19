"""섹션 5: 호버링 구간 검출.

기압계가 없는 기체가 많으므로 "자세 안정 + 스로틀 안정 + 수직 가속도 ≈ 0"을 동시에 만족하는
구간을 호버로 본다. 입력은 normalize.derive() 가 만든 파생 열 DataFrame 이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import config
from ..config import HoverThresholds


@dataclass
class HoverDetection:
    mode: str
    thresholds: HoverThresholds
    df: pd.DataFrame                              # thr_s, motor_s(평활) 와 hover 마스크가 추가된 데이터
    segments: list[tuple[float, float]]           # 채택된 호버 구간 (시작 s, 끝 s)
    airborne: list[tuple[float, float]]           # 이륙·착륙 2초를 잘라낸 비행 구간
    windows: pd.DataFrame                         # 윈도우별 판정 지표 (진단용)
    sample_rate_hz: float
    notes: list[str] = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return float(sum(e - s for s, e in self.segments))

    @property
    def low_confidence(self) -> bool:
        return self.total_s < config.LOW_CONFIDENCE_TOTAL_S

    @property
    def mask(self) -> np.ndarray:
        return self.df["hover"].to_numpy()


def _samples(seconds: float, fs: float) -> int:
    return max(1, int(round(seconds * fs)))


def _intervals(flag: np.ndarray, t: np.ndarray) -> list[tuple[float, float]]:
    """불리언 배열의 True 연속 구간 → [(t시작, t끝)]."""
    if not len(flag):
        return []
    d = np.diff(np.concatenate([[0], flag.astype(np.int8), [0]]))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1) - 1
    return [(float(t[s]), float(t[e])) for s, e in zip(starts, ends)]


def airborne_intervals(t: np.ndarray, thr_s: np.ndarray) -> list[tuple[float, float]]:
    """스로틀이 켜진 비행 구간을 찾고, 이륙 직후·착륙 직전을 잘라낸다."""
    raw = _intervals(thr_s > config.AIRBORNE_THROTTLE_PCT, t)
    merged: list[list[float]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] < config.AIRBORNE_MERGE_GAP_S:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    trim = config.TAKEOFF_LANDING_TRIM_S
    out = []
    for s, e in merged:
        if e - s < config.AIRBORNE_MIN_S:
            continue
        s2, e2 = s + trim, e - trim
        if e2 > s2:
            out.append((s2, e2))
    return out


def detect_hover(
    data: pd.DataFrame,
    mode: str = config.MODE_STRICT,
    thresholds: HoverThresholds | None = None,
) -> HoverDetection:
    th = thresholds or config.THRESHOLDS[mode]
    df = data.copy()
    t = df["t"].to_numpy()
    fs = 1.0 / float(np.median(np.diff(t)))
    notes: list[str] = []

    # 0.1초 이동평균 평활 (원본 샘플링 주기는 유지)
    sw = _samples(config.SMOOTH_WINDOW_S, fs)
    df["thr_s"] = df["thr_pct"].rolling(sw, center=True, min_periods=1).mean()
    df["motor_s"] = df["motor_pct"].rolling(sw, center=True, min_periods=1).mean()

    thr_slow = df["thr_pct"].rolling(_samples(0.5, fs), center=True, min_periods=1).mean().to_numpy()
    airborne = airborne_intervals(t, thr_slow)
    in_air = np.zeros(len(df), dtype=bool)
    for s, e in airborne:
        in_air |= (t >= s) & (t <= e)

    has_acc = "acc_z_g" in df and df["acc_z_g"].notna().any()
    if not has_acc:
        notes.append("Z축 가속도(accSmooth[2]/acc_1G)가 없어 가속도 조건 없이 판정했습니다.")

    # 0.5초 슬라이딩 윈도우, 50% 겹침
    nw = _samples(config.WINDOW_S, fs)
    step = max(1, int(round(nw * (1 - config.WINDOW_OVERLAP))))
    cols = {
        "thr": df["thr_s"].to_numpy(),
        "motor": df["motor_s"].to_numpy(),
        "gr": df["gyro_roll"].to_numpy(),
        "gp": df["gyro_pitch"].to_numpy(),
        "gy": df["gyro_yaw"].to_numpy(),
        "rr": np.abs(df["rc_roll_pct"].to_numpy()),
        "rp": np.abs(df["rc_pitch_pct"].to_numpy()),
        "acc": df["acc_z_g"].to_numpy() if has_acc else None,
    }

    rows = []
    for i0 in range(0, len(df) - nw + 1, step):
        i1 = i0 + nw
        sl = slice(i0, i1)
        rms = lambda x: float(np.sqrt(np.nanmean(x[sl] ** 2)))  # noqa: E731
        row = {
            "i0": i0,
            "i1": i1,
            "t0": t[i0],
            "t1": t[i1 - 1],
            "in_air": bool(in_air[i0] and in_air[i1 - 1]),
            "gyro_rp_rms": max(rms(cols["gr"]), rms(cols["gp"])),
            "gyro_yaw_rms": rms(cols["gy"]),
            "stick_rp_dev": float(
                max(
                    np.nanpercentile(cols["rr"][sl], config.STICK_PERCENTILE),
                    np.nanpercentile(cols["rp"][sl], config.STICK_PERCENTILE),
                )
            ),
            "thr_mean": float(np.nanmean(cols["thr"][sl])),
            "thr_std": float(np.nanstd(cols["thr"][sl])),
            "motor_std": float(np.nanstd(cols["motor"][sl])),
            "acc_z": float(np.nanmean(cols["acc"][sl])) if has_acc else np.nan,
        }
        rows.append(row)
    win = pd.DataFrame(rows)
    if win.empty:
        df["hover"] = False
        return HoverDetection(mode, th, df, [], airborne, win, fs, notes + ["로그가 너무 짧습니다."])

    ok = (
        win["in_air"]
        & (win["gyro_rp_rms"] < th.gyro_rp_rms)
        & (win["gyro_yaw_rms"] < th.gyro_yaw_rms)
        & (win["stick_rp_dev"] <= th.stick_rp_dev_pct)
        & (win["thr_std"] < th.throttle_std_pct)
        & (win["motor_std"] < th.motor_std_pct)
        & (win["thr_mean"] > th.throttle_min_pct)
        & (win["thr_mean"] < th.throttle_max_pct)
    )
    if has_acc:
        ok &= (win["acc_z"] - 1.0).abs() <= th.acc_z_tol_g

    # 프롭워시·급격한 기동 직후 1초 제외
    violent = (win["gyro_rp_rms"] > th.gyro_rp_rms * config.PROPWASH_FACTOR) | (
        win["gyro_yaw_rms"] > th.gyro_yaw_rms * config.PROPWASH_FACTOR
    )
    holdoff = np.zeros(len(win), dtype=bool)
    t0s, t1s = win["t0"].to_numpy(), win["t1"].to_numpy()
    for end in t1s[violent.to_numpy()]:
        holdoff |= (t0s < end + config.PROPWASH_HOLDOFF_S) & (t1s > end)
    win["holdoff"] = holdoff
    win["ok"] = ok & ~holdoff

    # 통과한 윈도우를 이어 붙여 1초 이상 연속 구간만 채택
    segments: list[tuple[float, float]] = []
    cur: list[float] | None = None
    for t0, t1, good in zip(t0s, t1s, win["ok"].to_numpy()):
        if good:
            if cur is not None and t0 <= cur[1] + 1e-9:
                cur[1] = t1
            else:
                if cur is not None:
                    segments.append((cur[0], cur[1]))
                cur = [t0, t1]
        elif cur is not None:
            segments.append((cur[0], cur[1]))
            cur = None
    if cur is not None:
        segments.append((cur[0], cur[1]))
    segments = [(s, e) for s, e in segments if e - s >= config.MIN_SEGMENT_S]

    hover = np.zeros(len(df), dtype=bool)
    for s, e in segments:
        hover |= (t >= s) & (t <= e)
    df["hover"] = hover
    return HoverDetection(mode, th, df, segments, airborne, win, fs, notes)
