"""열 이름 별칭 정규화와 단위·스케일 변환.

입력 형식(orangebox / blackbox_decode CSV / Blackbox Explorer CSV)마다 열 이름과 단위가
조금씩 다르다. 여기서 모두 Betaflight 원본 필드 이름(`rcCommand[3]`, `motor[0]` …)으로
맞춘 뒤, 분석에 쓰는 파생 열(t, thr_pct, motor_pct, cell_v, acc_z_g …)을 만든다.

주의: 스케일 규칙은 실제 샘플 로그로 확인해야 한다. 헤더 값이 있으면 항상 헤더를 우선한다.
"""

from __future__ import annotations

import math
import re
import struct
from typing import Any

import numpy as np
import pandas as pd

from .. import config

REQUIRED_FIELDS = [
    "time",
    "rcCommand[0]",
    "rcCommand[1]",
    "rcCommand[2]",
    "rcCommand[3]",
    "motor[0]",
    "gyroADC[0]",
    "gyroADC[1]",
    "gyroADC[2]",
]

# 정규화 키(소문자, 공백·단위 제거) → 표준 필드 이름
_ALIASES = {
    "time": "time",
    "time_us": "time",
    "vbat": "vbatLatest",
    "vbatlatest": "vbatLatest",
    "amperage": "amperageLatest",
    "amperagelatest": "amperageLatest",
    "flightmodeflags": "flightModeFlags",
    "loopiteration": "loopIteration",
}
_INDEXED = {
    "rccommand": "rcCommand",
    "rccommands": "rcCommand",
    "motor": "motor",
    "gyroadc": "gyroADC",
    "gyro": "gyroADC",
    "accsmooth": "accSmooth",
    "setpoint": "setpoint",
}

_UNIT_RE = re.compile(r"\s*\(([^)]*)\)\s*$")
_INDEX_RE = re.compile(r"^([a-z]+)\[?(\d+)\]?$")


def canonical_name(raw: str) -> tuple[str, str | None]:
    """원본 열 이름 → (표준 이름, 단위). 모르는 이름은 공백만 제거해 그대로 돌려준다."""
    name = raw.strip()
    unit = None
    m = _UNIT_RE.search(name)
    if m:
        unit = m.group(1).strip()
        name = name[: m.start()].strip()
    key = name.replace(" ", "").lower()
    if key in _ALIASES:
        return _ALIASES[key], unit
    m = _INDEX_RE.match(key)
    if m and m.group(1) in _INDEXED:
        return f"{_INDEXED[m.group(1)]}[{m.group(2)}]", unit
    return name.replace(" ", ""), unit


def normalize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """열 이름을 표준화하고 {표준 이름: 단위} 를 돌려준다."""
    rename: dict[str, str] = {}
    units: dict[str, str] = {}
    seen: set[str] = set()
    for col in df.columns:
        canon, unit = canonical_name(str(col))
        if canon in seen:  # 중복 열은 첫 번째만 유지
            continue
        seen.add(canon)
        rename[col] = canon
        if unit:
            units[canon] = unit
    out = df[list(rename.keys())].rename(columns=rename)
    return out, units


def missing_required(columns) -> list[str]:
    cols = set(columns)
    missing = [f for f in REQUIRED_FIELDS if f not in cols]
    # 스로틀은 setpoint[3] 로 대체 가능
    if "rcCommand[3]" in missing and "setpoint[3]" in cols:
        missing.remove("rcCommand[3]")
    return missing


# ---------------------------------------------------------------------------
# 헤더 해석
# ---------------------------------------------------------------------------

def header_value(headers: dict[str, Any], *names: str, default=None):
    for n in names:
        if n in headers and headers[n] not in ("", None):
            return headers[n]
    return default


def _as_float_bits(v: Any) -> float | None:
    """Betaflight 헤더의 0x3f800000 같은 IEEE754 비트 표현을 float 로."""
    if isinstance(v, str):
        try:
            v = int(v, 16) if v.lower().startswith("0x") else float(v)
        except ValueError:
            return None
    if isinstance(v, int) and v > 0xFFFF:
        return struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
    if isinstance(v, (int, float)):
        return float(v)
    return None


def firmware_info(headers: dict[str, Any]) -> tuple[str, int | None]:
    """(펌웨어 문자열, 메이저 버전). 예: ('Betaflight 4.4.2 (...)', 4)."""
    rev = str(header_value(headers, "Firmware revision", default="") or "")
    m = re.search(r"Betaflight\s+(\d+)\.", rev, re.I)
    major = int(m.group(1)) if m else None
    return rev, major


def is_betaflight4(headers: dict[str, Any]) -> bool:
    _, major = firmware_info(headers)
    return major is not None and major >= 4


def motor_output_range(headers: dict[str, Any], motor_values: np.ndarray | None = None) -> tuple[float, float, str]:
    """(최소, 최대, 출처). 헤더 motorOutput 우선, 없으면 데이터로 추정."""
    mo = header_value(headers, "motorOutput")
    if isinstance(mo, (list, tuple)) and len(mo) >= 2:
        return float(mo[0]), float(mo[1]), "header"
    if isinstance(mo, str) and "," in mo:
        lo, hi = mo.split(",")[:2]
        return float(lo), float(hi), "header"
    if motor_values is not None and len(motor_values):
        hi = float(np.nanmax(motor_values))
        lo = float(np.nanmin(motor_values))
        if hi <= 2047 and lo < 900:          # DShot
            return 48.0, 2047.0, "guess-dshot"
        return 1000.0, 2000.0, "guess-pwm"
    return 48.0, 2047.0, "default"


def vbat_to_volts(raw: np.ndarray, headers: dict[str, Any], unit: str | None) -> tuple[np.ndarray, str]:
    """vbatLatest 원시값 → V. (값, 사용한 규칙)"""
    if unit and unit.lower() == "v":
        return raw.astype(float), "csv-unit-V"
    if unit and unit.lower() == "mv":
        return raw.astype(float) / 1000.0, "csv-unit-mV"
    if is_betaflight4(headers):
        return raw.astype(float) / 100.0, "bf4-centivolt"
    med = float(np.nanmedian(raw)) if len(raw) else 0.0
    # 실제 LiPo 1S~6S 범위(2.7~26.1V)에 들어가는 단위를 고른다.
    if med >= 270:
        return raw.astype(float) / 100.0, "guess-centivolt"
    if med > 27:
        return raw.astype(float) / 10.0, "guess-decivolt"
    return raw.astype(float), "guess-volt"


def estimate_cell_count(volts: np.ndarray, headers: dict[str, Any] | None = None) -> int | None:
    """헤더 vbatcellcount 우선, 없으면 비행 초반 전압으로 추정."""
    if headers:
        hc = header_value(headers, "vbatcellcount", "vbat_cellcount", "cellCount")
        if isinstance(hc, (int, float)) and 1 <= hc <= 8:
            return int(hc)
    v = volts[np.isfinite(volts) & (volts > 2.5)]
    if not len(v):
        return None
    head = v[: max(1, len(v) // 20)]
    ref = float(np.percentile(head, 95))
    n = math.ceil(ref / config.CELL_MAX_V - 1e-6)
    return int(min(max(n, 1), 8))


def acc_1g(headers: dict[str, Any]) -> float | None:
    v = header_value(headers, "acc_1G")
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def gyro_scale_deg(headers: dict[str, Any]) -> float:
    """gyroADC 원시값 → deg/s 배율. Betaflight 4.x 는 gyro_scale=1.0 (원시값이 deg/s)."""
    v = _as_float_bits(header_value(headers, "gyro_scale"))
    if v is None or not (0 < v < 100):
        return 1.0
    return v


def rate_profile_values(headers: dict[str, Any]) -> tuple[int | None, int | None]:
    """헤더의 현재 thr_mid / thr_expo (0~100)."""
    mid = header_value(headers, "thrMid", "thr_mid")
    expo = header_value(headers, "thrExpo", "thr_expo")
    mid = int(mid) if isinstance(mid, (int, float)) else None
    expo = int(expo) if isinstance(expo, (int, float)) else None
    return mid, expo


# ---------------------------------------------------------------------------
# 파생 열
# ---------------------------------------------------------------------------

def motor_columns(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if re.fullmatch(r"motor\[\d+\]", c)]
    return sorted(cols, key=lambda c: int(c[6:-1]))


def derive(df: pd.DataFrame, headers: dict[str, Any], units: dict[str, str], cells: int | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """분석용 파생 열을 붙인 DataFrame 과 사용한 스케일 정보를 돌려준다.

    파생 열: t (s), thr_pct, motor_pct, rc_roll_pct, rc_pitch_pct,
            gyro_roll, gyro_pitch, gyro_yaw (deg/s), vbat_v, cell_v, acc_z_g
    """
    info: dict[str, Any] = {}
    out = pd.DataFrame(index=df.index)

    t_us = df["time"].astype(float).to_numpy()
    if units.get("time", "us").lower() in ("s", "sec"):
        t_us = t_us * 1e6
    out["t"] = (t_us - t_us[0]) / 1e6

    if "rcCommand[3]" in df:
        thr = (df["rcCommand[3]"].astype(float) - 1000.0) / 10.0
        info["throttle_source"] = "rcCommand[3]"
    else:
        thr = df["setpoint[3]"].astype(float) / 10.0
        info["throttle_source"] = "setpoint[3]"
    out["thr_pct"] = thr.clip(0, 100)

    mcols = motor_columns(df)
    motors = df[mcols].astype(float).to_numpy()
    lo, hi, src = motor_output_range(headers, motors)
    info.update(motor_min=lo, motor_max=hi, motor_range_source=src, motor_count=len(mcols))
    out["motor_pct"] = np.clip((motors.mean(axis=1) - lo) / (hi - lo) * 100.0, 0, 100)

    for i, axis in enumerate(("roll", "pitch")):
        out[f"rc_{axis}_pct"] = df[f"rcCommand[{i}]"].astype(float) / config.RC_DEFLECTION_FULL * 100.0

    gs = gyro_scale_deg(headers)
    info["gyro_scale"] = gs
    for i, axis in enumerate(("roll", "pitch", "yaw")):
        out[f"gyro_{axis}"] = df[f"gyroADC[{i}]"].astype(float) * gs

    if "vbatLatest" in df:
        volts, rule = vbat_to_volts(df["vbatLatest"].astype(float).to_numpy(), headers, units.get("vbatLatest"))
        volts = np.where(volts > 0.5, volts, np.nan)
        out["vbat_v"] = volts
        info["vbat_rule"] = rule
        if cells is None:
            cells = estimate_cell_count(volts, headers)
            info["cells_estimated"] = True
        if cells:
            out["cell_v"] = volts / cells
    info["cells"] = cells

    one_g = acc_1g(headers)
    if "accSmooth[2]" in df and one_g:
        out["acc_z_g"] = df["accSmooth[2]"].astype(float) / one_g
    info["acc_1G"] = one_g

    if "flightModeFlags" in df:
        out["flightModeFlags"] = df["flightModeFlags"]

    # 시간 역행·중복 샘플 제거
    keep = np.concatenate([[True], np.diff(out["t"].to_numpy()) > 0])
    out = out[keep].reset_index(drop=True)

    dt = np.median(np.diff(out["t"].to_numpy())) if len(out) > 1 else float("nan")
    info["sample_rate_hz"] = (1.0 / dt) if dt and dt > 0 else None
    return out, info
