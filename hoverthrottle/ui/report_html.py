"""요약 HTML (GUI 요약 탭·리포트 공용), 단일 파일 HTML 리포트, 호버 원시 데이터 CSV."""

from __future__ import annotations

import base64
import datetime as _dt
from html import escape
from pathlib import Path

import pandas as pd

from .. import __version__, config
from ..analysis.pipeline import AnalysisResult
from . import plots


def _f(v, nd=1, unit="") -> str:
    return "—" if v is None else f"{v:.{nd}f}{unit}"


def headline(res: AnalysisResult) -> tuple[str, str]:
    """(큰 숫자 문구, 보조 문구)"""
    if not res.combined:
        return "호버 구간 없음", "안정 호버 구간을 찾지 못했습니다"
    stick, motor = res.combined
    sub = f"호버 구간 {res.total_segments}개 · 총 {res.total_hover_s:.1f}초 · 비행 {len(res.flights)}개"
    if res.craft.mode == config.MODE_NORMAL:
        sub += " · 크루징 포함 추정치"
    return f"스틱 {stick:.0f}% / 모터 {motor:.0f}%", sub


def summary_html(res: AnalysisResult) -> str:
    """요약 본문 (QTextBrowser 가 이해하는 단순 HTML/CSS 만 사용)."""
    craft = res.craft
    h: list[str] = []
    a = h.append

    for path, err in res.errors:
        a(f"<p style='color:#b42318'><b>읽기 오류</b> {escape(path)}: {escape(err.message)}"
          + (f"<br>→ {escape(err.hint)}" if err.hint else "") + "</p>")

    if not res.combined:
        a("<p><b>안정 호버 구간을 찾지 못했습니다.</b> 10~20초 정도 제자리 호버를 포함해 다시 기록하세요.</p>")
        if craft.mode == config.MODE_STRICT:
            a("<p>일반 모드(완만한 크루징 포함)로 다시 분석해 볼 수 있습니다.</p>")
        return "\n".join(h)

    rec = res.recommendation
    if res.low_confidence:
        a(f"<p style='color:#b54708'><b>⚠ 신뢰도 낮음</b> — 호버 구간 총합이 {config.LOW_CONFIDENCE_TOTAL_S:.0f}초 미만입니다."
          + (" 일반 모드 재분석을 권장합니다." if craft.mode == config.MODE_STRICT else "") + "</p>")

    lo, hi = rec.status_range
    icon = {"normal": "✔", "above": "▲", "below": "▼"}[rec.status_level]
    a(f"<h3>기체 상태</h3><p>{icon} <b>{escape(rec.status_text)}</b><br>"
      f"<span style='color:#52514e'>모터 {rec.motor_pct:.1f}% · {escape(config.CRAFT_CLASSES[craft.craft_class])} "
      f"참고 범위 {lo:.0f}~{hi:.0f}% (일반적인 경험치)</span></p>")

    cur = lambda v: "—" if v is None else str(v)  # noqa: E731
    a("<h3>추천 설정</h3><table cellpadding='4' cellspacing='0' border='0'>"
      "<tr><th align='left'>항목</th><th align='right'>현재</th><th align='right'>추천</th><th align='left'></th></tr>"
      f"<tr><td>thr_mid</td><td align='right'>{cur(rec.current_thr_mid)}</td><td align='right'><b>{rec.thr_mid}</b></td>"
      "<td style='color:#52514e'>스로틀 곡선 중심을 실제 호버 지점에</td></tr>"
      f"<tr><td>thr_expo</td><td align='right'>{cur(rec.current_thr_expo)}</td><td align='right'><b>{rec.thr_expo}</b></td>"
      f"<td style='color:#52514e'>범위 {rec.thr_expo_range[0]}~{rec.thr_expo_range[1]}: {escape(rec.thr_expo_meaning)}</td></tr>"
      "</table><p style='color:#52514e'>thr_expo 는 시작점입니다. 취향에 맞게 조정하세요.</p>")
    if rec.throttle_limit_percent is not None:
        a(f"<p><b>선택 사항:</b> throttle_limit_type = SCALE, throttle_limit_percent = {rec.throttle_limit_percent}"
          "<br><span style='color:#b54708'>⚠ 펀치아웃 최대 출력이 그만큼 줄어듭니다.</span></p>")

    a("<h3>비행별 결과</h3><table cellpadding='4' cellspacing='0' border='0'>"
      "<tr><th align='left'>비행</th><th align='right'>호버 구간</th><th align='right'>스틱 % (IQR)</th>"
      "<th align='right'>모터 % (IQR)</th></tr>")
    for fa in res.flights:
        r = fa.result
        a(f"<tr><td>{escape(r.label)}</td><td align='right'>{r.n_segments}개 · {r.hover_total_s:.1f}s</td>"
          f"<td align='right'>{_f(r.stick_median)} ({_f(r.stick_q1)}~{_f(r.stick_q3)})</td>"
          f"<td align='right'>{_f(r.motor_median)} ({_f(r.motor_q1)}~{_f(r.motor_q3)})</td></tr>")
    if len(res.flights) > 1:
        a(f"<tr><td><b>가중 평균</b></td><td></td><td align='right'><b>{res.combined[0]:.1f}</b></td>"
          f"<td align='right'><b>{res.combined[1]:.1f}</b></td></tr>")
    a("</table>")
    raw = [fa.result.raw_stick_median for fa in res.flights if fa.result.raw_stick_median is not None]
    if raw:
        a(f"<p style='color:#52514e'>스틱 %는 현재 thr_mid/thr_expo 곡선을 통과한 rcCommand 값입니다. "
          f"곡선을 역산한 실제 스틱 위치는 약 {raw[0]:.0f}% 입니다.</p>")

    # 전압 필드가 아예 없으면 전압 보정 표는 숨긴다
    with_v = [fa for fa in res.flights if "cell_v" in fa.detection.df]
    if with_v:
        a("<h3>전압별 예상 호버 (모터 %)</h3>")
    for fa in with_v:
        v = fa.result.voltage
        if v.reliable:
            cells = "".join(f"<td align='right'>{m:.1f}%</td>" for _, _, m in v.predictions)
            heads = "".join(f"<th align='right'>{pv:.1f}V ({lab})</th>" for pv, lab, _ in v.predictions)
            a(f"<p>{escape(fa.result.label)} · R² {v.r2:.2f}</p>"
              f"<table cellpadding='4' cellspacing='0' border='0'><tr>{heads}</tr><tr>{cells}</tr></table>")
        else:
            a(f"<p style='color:#52514e'>{escape(fa.result.label)}: {escape(v.message or '전압 영향 판단 불가')}</p>")

    if rec.twr_range:
        a(f"<h3>추력비 추정 (근사치)</h3><p>{rec.twr_range[0]:.1f} ~ {rec.twr_range[1]:.1f} : 1"
          + (f" · 최대 추력 약 {rec.max_thrust_g[0]:,.0f} ~ {rec.max_thrust_g[1]:,.0f} g" if rec.max_thrust_g else "")
          + "<br><span style='color:#52514e'>추력 ∝ 스로틀ⁿ (n = 1.3~1.8) 가정의 근사치입니다.</span></p>")

    warns = list(res.warnings) + list(rec.warnings)
    for fa in res.flights:
        warns += [f"{fa.log.label}: {n}" for n in fa.detection.notes]
    if warns:
        a("<h3>참고·경고</h3><ul>" + "".join(f"<li>{escape(w)}</li>" for w in warns) + "</ul>")
    return "\n".join(h)


def _craft_rows(res: AnalysisResult) -> str:
    c = res.craft
    items = [
        ("기체 이름", c.name or "—"),
        ("기체 등급", config.CRAFT_CLASSES[c.craft_class]),
        ("셀 수", f"{c.cells}S" if c.cells else "자동 추정"),
        ("배터리 용량", f"{c.capacity_mah} mAh" if c.capacity_mah else "—"),
        ("이륙 중량", f"{c.weight_g:.0f} g" if c.weight_g else "—"),
        ("모터 KV / 프롭", f"{c.motor_kv or '—'} KV / {c.prop_in or '—'} 인치"),
        ("분석 모드", config.MODES[c.mode]),
    ]
    return "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in items)


def report_html(res: AnalysisResult) -> str:
    big, sub = headline(res)
    imgs = []
    if res.flights:
        for fa in res.flights:
            imgs.append((f"{fa.log.label} 시간축", plots.figure_png(plots.plot_timeline, fa)))
        imgs.append(("전압 대 호버 모터", plots.figure_png(plots.plot_voltage, res, size=(7, 4))))
        imgs.append(("호버 분포", plots.figure_png(plots.plot_histogram, res, size=(7, 3.6))))
    img_html = "".join(
        f"<figure><img alt='{escape(t)}' src='data:image/png;base64,{base64.b64encode(png).decode()}'></figure>"
        for t, png in imgs
    )
    cli = escape(res.recommendation.cli) if res.recommendation else ""
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>호버 스로틀 리포트</title>
<style>
:root {{ --ink:#0b0b0b; --ink2:#52514e; --line:#e4e3df; --bg:#fcfcfb; --card:#ffffff; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
       font-family:"Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif; line-height:1.5; }}
main {{ max-width:960px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:20px; margin:0 0 4px; }} h3 {{ font-size:15px; margin:24px 0 6px; }}
.hero {{ font-size:40px; font-weight:700; margin:16px 0 0; }} .sub {{ color:var(--ink2); }}
table {{ border-collapse:collapse; }} th, td {{ padding:4px 10px 4px 0; text-align:left; }}
.craft th {{ color:var(--ink2); font-weight:normal; }}
pre {{ background:var(--card); border:1px solid var(--line); padding:12px; border-radius:6px; overflow-x:auto; }}
figure {{ margin:16px 0; }} img {{ max-width:100%; height:auto; border:1px solid var(--line); border-radius:6px; }}
footer {{ color:var(--ink2); font-size:12px; margin-top:32px; }}
</style></head><body><main>
<h1>호버 스로틀 리포트</h1>
<div class="sub">{escape(", ".join(sorted({Path(fa.log.path).name for fa in res.flights})))} · {now}</div>
<div class="hero">{escape(big)}</div><div class="sub">{escape(sub)}</div>
<h3>기체 정보</h3><table class="craft">{_craft_rows(res)}</table>
{summary_html(res)}
<h3>Betaflight CLI</h3><pre>{cli}</pre>
<h3>그래프</h3>{img_html}
<footer>HoverThrottle {__version__}</footer>
</main></body></html>"""


def default_report_path(res: AnalysisResult) -> Path:
    first = Path(res.flights[0].log.path)
    return first.with_name(f"{first.stem}_hover_report.html")


def save_report(res: AnalysisResult, path: Path | None = None) -> Path:
    path = Path(path) if path else default_report_path(res)
    path.write_text(report_html(res), encoding="utf-8")
    return path


def hover_samples(res: AnalysisResult) -> pd.DataFrame:
    parts = []
    for fa in res.flights:
        df = fa.detection.df
        cols = [c for c in ("t", "thr_pct", "thr_s", "motor_pct", "motor_s", "vbat_v", "cell_v",
                            "gyro_roll", "gyro_pitch", "gyro_yaw", "acc_z_g") if c in df]
        part = df.loc[df["hover"], cols].copy()
        part.insert(0, "flight", fa.log.label)
        parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def save_hover_csv(res: AnalysisResult, path: Path) -> Path:
    hover_samples(res).to_csv(path, index=False, encoding="utf-8-sig")
    return Path(path)
