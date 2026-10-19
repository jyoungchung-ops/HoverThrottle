"""로그 디코더 계층.

모든 입력 형식을 `LogData`(시간축 DataFrame + 헤더 dict)로 바꾼다. 디코더는 `LogReader`
인터페이스로 분리돼 있어 다른 구현으로 교체할 수 있다.

디코딩 순서 (섹션 4):
  1. .bbl/.bfl/.txt → orangebox
  2. 실패하거나 필수 필드가 없으면 번들된 blackbox_decode.exe --index N
  3. .csv → 바로 읽기 (Blackbox Explorer / blackbox_decode 출력)
"""

from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import normalize

_log = logging.getLogger(__name__)

ProgressFn = Callable[[float], None]

BINARY_EXTS = {".bbl", ".bfl", ".txt"}
CSV_EXTS = {".csv"}
SUPPORTED_EXTS = BINARY_EXTS | CSV_EXTS


class LogReadError(Exception):
    """사용자에게 보여줄 한국어 메시지를 담은 읽기 오류."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class MissingFieldsError(LogReadError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            "로그에 필수 필드가 없습니다: " + ", ".join(missing),
            "Betaflight Configurator → Blackbox 탭(또는 CLI `set blackbox_disable_*`)에서 "
            "RC Commands, Motors, Gyro 로깅이 꺼져 있지 않은지 확인하세요. "
            "예: `set blackbox_disable_rc = OFF`, `set blackbox_disable_motors = OFF`, "
            "`set blackbox_disable_gyro = OFF` 후 `save`.",
        )


@dataclass
class FlightInfo:
    """한 파일 안의 비행(로그 인덱스) 요약."""

    index: int                    # 1부터
    size_bytes: int | None = None
    start_time: str | None = None
    duration_s: float | None = None


@dataclass
class LogData:
    path: str
    index: int
    df: pd.DataFrame                       # 표준 필드 이름의 원본 값
    headers: dict[str, Any]
    units: dict[str, str] = field(default_factory=dict)
    source: str = ""                       # orangebox / blackbox_decode / csv
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{Path(self.path).name} #{self.index}"


class LogReader(ABC):
    name = "base"

    @abstractmethod
    def list_flights(self, path: str) -> list[FlightInfo]:
        ...

    @abstractmethod
    def read(self, path: str, index: int = 1, progress: ProgressFn | None = None) -> LogData:
        ...


# ---------------------------------------------------------------------------
# orangebox
# ---------------------------------------------------------------------------

def _keep_columns(names: list[str]) -> list[str]:
    """분석에 쓰는 열만 남긴다 (메모리 절약)."""
    keep = []
    for n in names:
        canon, _ = normalize.canonical_name(n)
        if canon in ("time", "vbatLatest", "amperageLatest", "flightModeFlags", "loopIteration") or canon.startswith(
            ("rcCommand[", "motor[", "gyroADC[", "accSmooth[2]", "setpoint[3]")
        ):
            keep.append(n)
    return keep


def _finish(df: pd.DataFrame, path: str, index: int, headers: dict, units: dict, source: str, warnings: list[str]) -> LogData:
    df, more_units = normalize.normalize_columns(df)
    units = {**units, **more_units}
    df = df.apply(pd.to_numeric, errors="coerce")
    missing = normalize.missing_required(df.columns)
    if missing:
        raise MissingFieldsError(missing)
    df = df.dropna(subset=["time"]).reset_index(drop=True)
    if len(df) < 10:
        raise LogReadError("로그에 유효한 데이터 프레임이 거의 없습니다.", "블랙박스 기록이 정상적으로 저장됐는지 확인하세요.")
    return LogData(path=path, index=index, df=df, headers=headers, units=units, source=source, warnings=warnings)


class OrangeboxReader(LogReader):
    name = "orangebox"

    def list_flights(self, path: str) -> list[FlightInfo]:
        from orangebox.reader import Reader

        r = Reader(path)
        ptrs = r.log_pointers + [os.path.getsize(path)]
        flights = []
        for i in range(r.log_count):
            info = FlightInfo(index=i + 1, size_bytes=ptrs[i + 1] - ptrs[i])
            try:
                r.set_log_index(i + 1)
                dt = r.headers.get("Log start datetime")
                if dt and not str(dt).startswith("0000"):
                    info.start_time = str(dt)
            except Exception as e:  # 손상된 헤더
                _log.warning("header read failed for %s #%d: %s", path, i + 1, e)
            flights.append(info)
        return flights

    def read(self, path: str, index: int = 1, progress: ProgressFn | None = None) -> LogData:
        from orangebox import Parser

        warnings: list[str] = []
        try:
            parser = Parser.load(path, index, allow_invalid_header=True)
        except Exception as e:
            raise LogReadError(f"로그 헤더를 읽을 수 없습니다: {e}", "파일이 Betaflight 블랙박스 로그인지 확인하세요.") from e

        names = parser.field_names
        keep = _keep_columns(names)
        idx = [names.index(n) for n in keep]
        reader = parser.reader
        total = max(len(reader), 1)
        rows = []
        try:
            for n, frame in enumerate(parser.frames()):
                d = frame.data
                rows.append([d[i] if i < len(d) else None for i in idx])
                if progress and n % 20000 == 0:
                    progress(min(reader.tell() / total, 1.0))
        except Exception as e:
            # 잘린 로그: 읽은 부분까지만 사용
            _log.warning("orangebox stopped early on %s #%d: %s", path, index, e)
            warnings.append(f"로그 일부 손상 — 읽을 수 있는 부분({len(rows)} 프레임)까지만 분석합니다.")
        if progress:
            progress(1.0)
        if not rows:
            raise LogReadError("로그에서 데이터 프레임을 읽지 못했습니다.", "로그가 비어 있거나 손상됐을 수 있습니다.")

        df = pd.DataFrame(rows, columns=keep)
        return _finish(df, path, index, parser.headers, {}, self.name, warnings)


# ---------------------------------------------------------------------------
# blackbox_decode.exe
# ---------------------------------------------------------------------------

def find_blackbox_decode() -> str | None:
    """번들(PyInstaller) → 저장소 bin/ → PATH 순서로 찾는다."""
    exe = "blackbox_decode.exe" if os.name == "nt" else "blackbox_decode"
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "bin" / exe)
    candidates.append(Path(__file__).resolve().parents[2] / "bin" / exe)
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which(exe)


class BlackboxDecodeReader(LogReader):
    name = "blackbox_decode"

    def __init__(self, exe: str | None = None):
        self.exe = exe or find_blackbox_decode()

    @property
    def available(self) -> bool:
        return bool(self.exe)

    def list_flights(self, path: str) -> list[FlightInfo]:
        return OrangeboxReader().list_flights(path)

    def read(self, path: str, index: int = 1, progress: ProgressFn | None = None) -> LogData:
        if not self.exe:
            raise LogReadError("blackbox_decode 를 찾을 수 없습니다.")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.run(
            [self.exe, "--index", str(index), "--stdout", path],
            capture_output=True,
            creationflags=flags,
            timeout=600,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        if not text.strip():
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise LogReadError(f"blackbox_decode 변환 실패: {err[:300]}")
        warnings = []
        if proc.returncode != 0:
            warnings.append("blackbox_decode 가 오류를 보고했습니다 — 로그 일부 손상 가능")
        df = pd.read_csv(io.StringIO(text), skipinitialspace=True, low_memory=False)
        df = df[_keep_columns(list(df.columns))]
        headers: dict[str, Any] = {}
        try:
            from orangebox.reader import Reader

            headers = Reader(path, index, allow_invalid_header=True).headers
        except Exception as e:
            _log.warning("header read via orangebox failed: %s", e)
        if progress:
            progress(1.0)
        return _finish(df, path, index, headers, {}, self.name, warnings)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _split_csv_headers(text: str) -> tuple[dict[str, Any], int]:
    """Blackbox Explorer 내보내기의 앞부분 "이름","값" 헤더 줄을 읽고 데이터 시작 줄 번호를 돌려준다."""
    from orangebox.tools import _trycast

    headers: dict[str, Any] = {}
    for lineno, row in enumerate(csv.reader(io.StringIO(text))):
        if not row:
            continue
        first = normalize.canonical_name(row[0])[0]
        if first in ("loopIteration", "time"):
            return headers, lineno
        if len(row) >= 2:
            key = row[0].strip()
            vals = [v.strip() for v in row[1:] if v.strip() != ""]
            if len(vals) == 1 and "," in vals[0]:
                vals = [v.strip() for v in vals[0].split(",")]
            cast = [_trycast(v) for v in vals]
            headers[key] = cast if len(cast) > 1 else (cast[0] if cast else "")
        if lineno > 2000:
            break
    return headers, 0


class CsvReader(LogReader):
    name = "csv"

    def list_flights(self, path: str) -> list[FlightInfo]:
        return [FlightInfo(index=1, size_bytes=os.path.getsize(path))]

    def read(self, path: str, index: int = 1, progress: ProgressFn | None = None) -> LogData:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        headers, start = _split_csv_headers(text)
        try:
            df = pd.read_csv(io.StringIO(text), skiprows=start, skipinitialspace=True, low_memory=False, on_bad_lines="skip")
        except Exception as e:
            raise LogReadError(f"CSV 파일을 읽을 수 없습니다: {e}") from e
        df = df[_keep_columns(list(df.columns))]
        if progress:
            progress(1.0)
        return _finish(df, path, index, headers, {}, self.name, [])


# ---------------------------------------------------------------------------
# 진입 함수
# ---------------------------------------------------------------------------

def _looks_binary_log(path: str) -> bool:
    with open(path, "rb") as f:
        head = f.read(64)
    return head.startswith(b"H Product:")


def list_flights(path: str) -> list[FlightInfo]:
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise LogReadError(f"지원하지 않는 파일 형식입니다: {ext}", "지원 형식: .bbl, .bfl, .txt, .csv")
    if ext in CSV_EXTS or not _looks_binary_log(path):
        return CsvReader().list_flights(path)
    return OrangeboxReader().list_flights(path)


def read_headers(path: str, index: int = 1) -> dict[str, Any]:
    """프레임을 디코딩하지 않고 헤더만 빠르게 읽는다 (입력란 자동 채움용). 실패하면 빈 dict."""
    try:
        if Path(path).suffix.lower() in CSV_EXTS or not _looks_binary_log(path):
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                head = "".join(line for _, line in zip(range(2000), f))
            return _split_csv_headers(head)[0]
        from orangebox.reader import Reader

        return Reader(path, index, allow_invalid_header=True).headers
    except Exception as e:
        _log.warning("header read failed for %s: %s", path, e)
        return {}


def read_log(path: str, index: int = 1, progress: ProgressFn | None = None) -> LogData:
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise LogReadError(f"지원하지 않는 파일 형식입니다: {ext}", "지원 형식: .bbl, .bfl, .txt, .csv")
    if ext in CSV_EXTS or not _looks_binary_log(path):
        return CsvReader().read(path, index, progress)

    first_error: LogReadError | None = None
    try:
        return OrangeboxReader().read(path, index, progress)
    except LogReadError as e:
        first_error = e
        _log.info("orangebox failed (%s); trying blackbox_decode", e.message)
    bd = BlackboxDecodeReader()
    if bd.available:
        try:
            data = bd.read(path, index, progress)
            data.warnings.append("orangebox 파싱 실패로 blackbox_decode 로 변환했습니다.")
            return data
        except LogReadError as e:
            _log.warning("blackbox_decode failed: %s", e.message)
    raise first_error
