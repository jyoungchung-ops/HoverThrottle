"""알려진 호버 값을 가진 합성 Betaflight 로그 생성기."""

from __future__ import annotations

import numpy as np
import pandas as pd

FS = 500.0
MOTOR_LO, MOTOR_HI = 48, 2047
HEADERS = {
    "Firmware revision": "Betaflight 4.4.2 (synthetic) STM32F7X2",
    "motorOutput": [MOTOR_LO, MOTOR_HI],
    "acc_1G": 2048,
    "thrMid": 50,
    "thrExpo": 0,
    "gyro_scale": 0x3F800000,
}

# (시작 s, 끝 s, 종류)
SCENARIO = [
    (0, 1, "ground"),
    (1, 3, "takeoff"),
    (3, 25, "hover"),
    (25, 45, "maneuver"),
    (45, 70, "hover"),
    (70, 90, "cruise"),
    (90, 110, "hover"),
    (110, 113, "landing"),
    (113, 120, "ground"),
]


def make_log(hover_thr: float = 30.0, cells: int = 4, seed: int = 0, scenario=SCENARIO) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    end = scenario[-1][1]
    n = int(end * FS)
    t = np.arange(n) / FS
    thr = np.zeros(n)
    rc = np.zeros((3, n))
    gyro = rng.normal(0, 6, (3, n))
    acc = rng.normal(1.0, 0.02, n)

    for s, e, kind in scenario:
        m = (t >= s) & (t < e)
        k = int(m.sum())
        if kind == "ground":
            thr[m] = 0
            gyro[:, m] = rng.normal(0, 1, (3, k))
        elif kind == "takeoff":
            thr[m] = np.linspace(0, hover_thr + 15, k)
        elif kind == "landing":
            thr[m] = np.linspace(hover_thr - 10, 0, k)
        elif kind == "hover":
            thr[m] = hover_thr + rng.normal(0, 0.8, k)
            rc[:, m] = rng.normal(0, 4, (3, k))           # ±1% 정도 스틱 미세 보정
        elif kind == "maneuver":
            tt = t[m]
            thr[m] = 50 + 30 * np.sin(2 * np.pi * 0.7 * tt)
            rc[0, m] = 400 * np.sin(2 * np.pi * 0.5 * tt)
            rc[1, m] = 300 * np.cos(2 * np.pi * 0.4 * tt)
            gyro[0, m] += 500 * np.sin(2 * np.pi * 0.5 * tt)
            gyro[1, m] += 350 * np.cos(2 * np.pi * 0.4 * tt)
            acc[m] = 1.0 + 0.8 * np.sin(2 * np.pi * 0.7 * tt)
        elif kind == "cruise":
            thr[m] = hover_thr + 12 + rng.normal(0, 0.8, k)
            rc[1, m] = 50 + rng.normal(0, 4, k)           # 피치 10% 완만한 전진
            gyro[:, m] += rng.normal(0, 10, (3, k))

    # 배터리: 셀당 4.15V → 3.6V, 부하 시 약간 강하
    cell_v = np.linspace(4.15, 3.6, n) - 0.05 * (thr / 100)
    # 모터: 스로틀에 비례, 전압이 낮을수록 출력이 더 들어간다
    motor_pct = np.where(thr > 0, 0.8 * thr * (4.0 / cell_v), 0.0)
    motors = []
    for _ in range(4):
        raw = MOTOR_LO + motor_pct / 100 * (MOTOR_HI - MOTOR_LO) + rng.normal(0, 30, n)
        motors.append(np.clip(raw, MOTOR_LO, MOTOR_HI))

    df = pd.DataFrame({"loopIteration": np.arange(n), "time": (t * 1e6 + 5_000_000).astype(np.int64)})
    for i in range(3):
        df[f"rcCommand[{i}]"] = np.round(rc[i])
    df["rcCommand[3]"] = np.round(1000 + thr * 10)
    for i in range(3):
        df[f"gyroADC[{i}]"] = np.round(gyro[i])
    for i, m in enumerate(motors):
        df[f"motor[{i}]"] = np.round(m)
    df["vbatLatest"] = np.round(cell_v * cells * 100)
    df["accSmooth[2]"] = np.round(acc * HEADERS["acc_1G"])
    return df, dict(HEADERS)


def expected_motor_median(hover_thr: float = 30.0) -> float:
    """호버 구간에서의 참 모터 % 중앙값 (전압 효과 포함, 합성식 기준)."""
    t = np.arange(int(SCENARIO[-1][1] * FS)) / FS
    m = np.zeros_like(t, dtype=bool)
    for s, e, kind in SCENARIO:
        if kind == "hover":
            m |= (t >= s + 1) & (t < e - 1)
    cell_v = np.linspace(4.15, 3.6, len(t)) - 0.05 * (hover_thr / 100)
    return float(np.median(0.8 * hover_thr * (4.0 / cell_v[m])))


def to_blackbox_decode_csv(df: pd.DataFrame, path, cells: int = 4) -> None:
    """blackbox_decode 출력처럼 열 이름 앞 공백과 단위 표기, 전압 V 단위로 저장."""
    out = df.copy()
    out["vbatLatest"] = out["vbatLatest"] / 100.0
    cols = {c: c for c in out.columns}
    cols["time"] = "time (us)"
    cols["vbatLatest"] = "vbatLatest (V)"
    out = out.rename(columns=cols)
    out.columns = [c if i == 0 else " " + c for i, c in enumerate(out.columns)]
    out.to_csv(path, index=False)


def to_explorer_csv(df: pd.DataFrame, headers: dict, path) -> None:
    """Blackbox Explorer 내보내기처럼 "이름","값" 헤더 줄 뒤에 데이터."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        for k, v in headers.items():
            if isinstance(v, list):
                v = ",".join(str(x) for x in v)
            elif isinstance(v, int) and v > 0xFFFF:
                v = hex(v)
            f.write(f'"{k}","{v}"\n')
        df.to_csv(f, index=False)
