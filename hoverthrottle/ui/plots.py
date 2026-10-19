"""그래프 그리기 (Qt 와 무관 — GUI 와 HTML 리포트가 같이 쓴다)."""

from __future__ import annotations

import io

import matplotlib
import numpy as np
from matplotlib import font_manager
from matplotlib.figure import Figure

from ..analysis.pipeline import AnalysisResult, FlightAnalysis

# 참조 팔레트 (dataviz reference palette, light)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
STICK_COLOR, MOTOR_COLOR = SERIES[0], SERIES[1]
HOVER_BAND = "#1baf7a"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"

_KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"]
_fonts_ready = False


def setup_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _KOREAN_FONTS:
        if name in available:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
    _fonts_ready = True


def _style(ax, xlabel: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_xlabel(xlabel, color=INK_2)
    ax.set_ylabel(ylabel, color=INK_2)
    ax.tick_params(colors=INK_2, length=0)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def _decimate(n: int, target: int = 20000) -> slice:
    return slice(None, None, max(1, n // target))


def plot_timeline(fig: Figure, fa: FlightAnalysis) -> None:
    """(a) 시간축 스로틀·모터 평균 + 호버 구간 음영."""
    setup_fonts()
    fig.clear()
    fig.set_facecolor(SURFACE)
    ax = fig.add_subplot(111)
    df = fa.detection.df
    sl = _decimate(len(df))
    t = df["t"].to_numpy()[sl]
    for i, (s, e) in enumerate(fa.detection.segments):
        ax.axvspan(s, e, color=HOVER_BAND, alpha=0.18, linewidth=0, label="호버 구간" if i == 0 else None)
    ax.plot(t, df["thr_s"].to_numpy()[sl], color=STICK_COLOR, linewidth=1.2, label="스로틀 (rcCommand)")
    ax.plot(t, df["motor_s"].to_numpy()[sl], color=MOTOR_COLOR, linewidth=1.2, label="모터 평균 출력")
    r = fa.result
    if r.stick_median is not None:
        ax.axhline(r.stick_median, color=STICK_COLOR, linewidth=0.8, linestyle="--")
        ax.axhline(r.motor_median, color=MOTOR_COLOR, linewidth=0.8, linestyle="--")
    ax.set_ylim(0, 100)
    ax.set_xlim(t[0], t[-1])
    _style(ax, "시간 (s)", "%")
    ax.set_title(f"{fa.log.label} — 스로틀·모터 출력", color=INK, loc="left", fontsize=11)
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=9, ncols=3, borderaxespad=0.2)
    fig.tight_layout()


def _binned_voltage(fa: FlightAnalysis):
    df = fa.detection.df
    if "cell_v" not in df:
        return None
    m = df["hover"].to_numpy() & np.isfinite(df["cell_v"].to_numpy())
    if m.sum() < 20:
        return None
    t = df["t"].to_numpy()[m]
    bins = np.floor(t / 0.1).astype(np.int64)
    _, inv = np.unique(bins, return_inverse=True)
    cnt = np.bincount(inv)
    v = np.bincount(inv, df["cell_v"].to_numpy()[m]) / cnt
    mo = np.bincount(inv, df["motor_s"].to_numpy()[m]) / cnt
    return v, mo


def plot_voltage(fig: Figure, result: AnalysisResult) -> bool:
    """(b) 셀당 전압 대 호버 모터 % 산점도 + 회귀선. 전압 데이터가 없으면 False."""
    setup_fonts()
    fig.clear()
    fig.set_facecolor(SURFACE)
    ax = fig.add_subplot(111)
    drew = False
    flights = result.flights[: len(SERIES)]  # 산점도는 3개 계열까지만 색 구분이 안전
    for i, fa in enumerate(flights):
        b = _binned_voltage(fa)
        if b is None:
            continue
        v, mo = b
        color = SERIES[i]
        ax.scatter(v, mo, s=10, color=color, alpha=0.55, edgecolors="none", label=fa.log.label)
        fit = fa.result.voltage
        if fit.slope is not None:
            xs = np.linspace(v.min(), v.max(), 10)
            ax.plot(xs, fit.slope * xs + fit.intercept, color=color, linewidth=2,
                    linestyle="-" if fit.reliable else ":")
        drew = True
    if not drew:
        ax.text(0.5, 0.5, "전압 데이터가 없거나 호버 구간이 부족합니다", ha="center", va="center", color=INK_2, transform=ax.transAxes)
        ax.set_axis_off()
    else:
        _style(ax, "셀당 전압 (V)", "호버 모터 출력 (%)")
        ax.invert_xaxis()  # 왼쪽이 만충 → 오른쪽으로 갈수록 방전
        if len(result.flights) > 1:
            ax.legend(frameon=False, fontsize=9)
    ax.set_title("셀당 전압 대 호버 모터 출력 (점선 = R² 낮음)", color=INK, loc="left", fontsize=11)
    fig.tight_layout()
    return drew


def plot_histogram(fig: Figure, result: AnalysisResult) -> None:
    """(c) 호버 구간 스로틀 히스토그램 (스틱·모터)."""
    setup_fonts()
    fig.clear()
    fig.set_facecolor(SURFACE)
    ax = fig.add_subplot(111)
    stick = [fa.detection.df.loc[fa.detection.df["hover"], "thr_s"].to_numpy() for fa in result.flights]
    motor = [fa.detection.df.loc[fa.detection.df["hover"], "motor_s"].to_numpy() for fa in result.flights]
    stick = np.concatenate(stick) if stick else np.array([])
    motor = np.concatenate(motor) if motor else np.array([])
    if not len(stick):
        ax.text(0.5, 0.5, "호버 구간이 없습니다", ha="center", va="center", color=INK_2, transform=ax.transAxes)
        ax.set_axis_off()
    else:
        lo = float(np.floor(min(stick.min(), motor.min()))) - 1
        hi = float(np.ceil(max(stick.max(), motor.max()))) + 1
        bins = np.arange(lo, hi + 0.5, 0.5)
        ax.hist(stick, bins=bins, color=STICK_COLOR, alpha=0.75, label="스로틀 (rcCommand)",
                edgecolor=SURFACE, linewidth=0.5, density=True)
        ax.hist(motor, bins=bins, color=MOTOR_COLOR, alpha=0.75, label="모터 평균 출력",
                edgecolor=SURFACE, linewidth=0.5, density=True)
        if result.combined:
            for val, c in zip(result.combined, (STICK_COLOR, MOTOR_COLOR)):
                ax.axvline(val, color=c, linewidth=1.5, linestyle="--")
                ax.annotate(f"{val:.1f}%", (val, 1), xycoords=("data", "axes fraction"), xytext=(4, -12),
                            textcoords="offset points", color=INK, fontsize=9)
        _style(ax, "%", "비율")
        ax.legend(frameon=False, fontsize=9)
    ax.set_title("호버 구간 분포", color=INK, loc="left", fontsize=11)
    fig.tight_layout()


def figure_png(draw, *args, size=(9, 3.6), dpi=110) -> bytes:
    """그리기 함수를 새 Figure 에 실행하고 PNG 바이트를 돌려준다 (리포트용)."""
    fig = Figure(figsize=size, dpi=dpi)
    draw(fig, *args)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    return buf.getvalue()
