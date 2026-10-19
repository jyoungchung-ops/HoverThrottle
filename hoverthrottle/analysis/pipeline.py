"""입력 → 로그 읽기 → 호버 검출 → 추천까지 한 번에 묶는 분석 흐름 (CLI·GUI 공용)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from .. import config
from ..io import normalize
from ..io.log_reader import LogData, LogReadError, read_log
from .hover_detect import HoverDetection, detect_hover
from .recommend import FlightResult, Recommendation, combine, flight_result, recommend

_log = logging.getLogger(__name__)


@dataclass
class CraftInput:
    craft_class: str = config.CLASS_5INCH
    cells: int | None = None
    name: str = ""
    capacity_mah: int | None = None
    weight_g: float | None = None
    motor_kv: int | None = None
    prop_in: float | None = None
    thr_mid: int | None = None          # 현재값 (없으면 헤더)
    thr_expo: int | None = None
    mode: str = config.MODE_STRICT


@dataclass
class FlightAnalysis:
    log: LogData
    detection: HoverDetection
    result: FlightResult
    scale_info: dict


@dataclass
class AnalysisResult:
    craft: CraftInput
    flights: list[FlightAnalysis] = field(default_factory=list)
    errors: list[tuple[str, LogReadError]] = field(default_factory=list)
    combined: tuple[float, float] | None = None       # (스틱 %, 모터 %)
    recommendation: Recommendation | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def total_hover_s(self) -> float:
        return sum(f.result.hover_total_s for f in self.flights)

    @property
    def total_segments(self) -> int:
        return sum(f.result.n_segments for f in self.flights)

    @property
    def low_confidence(self) -> bool:
        return self.total_hover_s < config.LOW_CONFIDENCE_TOTAL_S


def analyze_log(log: LogData, craft: CraftInput) -> FlightAnalysis:
    derived, info = normalize.derive(log.df, log.headers, log.units, craft.cells)
    det = detect_hover(derived, craft.mode)
    hdr_mid, hdr_expo = normalize.rate_profile_values(log.headers)
    mid = craft.thr_mid if craft.thr_mid is not None else hdr_mid
    expo = craft.thr_expo if craft.thr_expo is not None else hdr_expo
    res = flight_result(log.label, det, mid, expo)
    return FlightAnalysis(log=log, detection=det, result=res, scale_info=info)


def run(
    targets: list[tuple[str, int]],
    craft: CraftInput,
    progress: Callable[[float, str], None] | None = None,
) -> AnalysisResult:
    """targets: [(파일 경로, 로그 인덱스)]"""
    out = AnalysisResult(craft=craft)
    n = max(len(targets), 1)
    for k, (path, index) in enumerate(targets):
        def sub(p: float, k=k):
            if progress:
                progress((k + p * 0.9) / n, f"{path} #{index} 읽는 중")

        try:
            log = read_log(path, index, sub)
            fa = analyze_log(log, craft)
        except LogReadError as e:
            _log.warning("read failed %s #%d: %s", path, index, e.message)
            out.errors.append((f"{path} #{index}", e))
            continue
        out.flights.append(fa)
        out.warnings += [f"{log.label}: {w}" for w in log.warnings]
        if progress:
            progress((k + 1) / n, f"{path} #{index} 분석 완료")

    out.combined = combine([f.result for f in out.flights])
    if out.combined and out.flights:
        first = out.flights[0]
        hdr_mid, hdr_expo = normalize.rate_profile_values(first.log.headers)
        cur_mid = craft.thr_mid if craft.thr_mid is not None else hdr_mid
        cur_expo = craft.thr_expo if craft.thr_expo is not None else hdr_expo
        bf4 = normalize.is_betaflight4(first.log.headers) or first.log.source == "csv" and not first.log.headers
        out.recommendation = recommend(
            out.combined[0],
            out.combined[1],
            craft.craft_class,
            craft.weight_g,
            cur_mid,
            cur_expo,
            is_bf4=bf4,
        )
    return out
