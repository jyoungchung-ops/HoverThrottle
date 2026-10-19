"""명령줄 인터페이스 (1단계: 파싱 + 호버 검출 결과 확인용).

예)
  python -m hoverthrottle list LOG00001.BBL
  python -m hoverthrottle analyze LOG00001.BBL --class 5inch --cells 6
  python -m hoverthrottle analyze a.bbl b.csv --class tinywhoop --cells 1 --mode normal --index 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .analysis import pipeline
from .io.log_reader import LogReadError, list_flights


def _fmt(v, nd=1, unit=""):
    return "—" if v is None else f"{v:.{nd}f}{unit}"


def _print_result(res: pipeline.AnalysisResult) -> None:
    p = print
    craft = res.craft
    p("=" * 64)
    p(f"기체: {craft.name or '(이름 없음)'} | 등급: {config.CRAFT_CLASSES[craft.craft_class]} | 모드: {config.MODES[craft.mode]}")
    p("=" * 64)

    for path, err in res.errors:
        p(f"[오류] {path}: {err.message}")
        if err.hint:
            p(f"       → {err.hint}")

    for fa in res.flights:
        r, info, det = fa.result, fa.scale_info, fa.detection
        p(f"\n■ {r.label}  (디코더: {fa.log.source}, {info.get('sample_rate_hz') or 0:.0f} Hz, "
          f"길이 {det.df['t'].iloc[-1]:.1f}s)")
        p(f"  스케일: 모터 {info['motor_min']:.0f}~{info['motor_max']:.0f} ({info['motor_range_source']}), "
          f"모터 {info['motor_count']}개, 스로틀={info['throttle_source']}, "
          f"전압={info.get('vbat_rule', '없음')}, 셀={info.get('cells')}{' (추정)' if info.get('cells_estimated') else ''}, "
          f"acc_1G={info.get('acc_1G')}")
        air = ", ".join(f"{s:.1f}~{e:.1f}s" for s, e in det.airborne) or "없음"
        p(f"  비행 구간(이착륙 2초 제외): {air}")
        segs = ", ".join(f"{s:.1f}~{e:.1f}s" for s, e in det.segments) or "없음"
        p(f"  호버 구간 {r.n_segments}개, 총 {r.hover_total_s:.1f}s: {segs}")
        for n in det.notes:
            p(f"  참고: {n}")
        if r.stick_median is None:
            p("  → 안정 호버 구간을 찾지 못했습니다.")
            continue
        p(f"  호버 스로틀(rcCommand) 중앙값 {r.stick_median:.1f}%  (IQR {r.stick_q1:.1f}~{r.stick_q3:.1f})")
        if r.raw_stick_median is not None:
            p(f"    └ 현재 곡선 역산 실제 스틱 위치 ≈ {r.raw_stick_median:.1f}%")
        p(f"  호버 모터 출력 중앙값 {r.motor_median:.1f}%  (IQR {r.motor_q1:.1f}~{r.motor_q3:.1f})")
        v = r.voltage
        if v.slope is not None:
            p(f"  전압 회귀: 기울기 {v.slope:.1f} %/V, R²={v.r2:.2f}" + ("" if v.reliable else "  → 전압 영향 판단 불가"))
            if v.reliable:
                for pv, label, m in v.predictions:
                    p(f"    셀당 {pv:.1f}V ({label}): 예상 호버 모터 {m:.1f}%")
        elif v.message:
            p(f"  전압: {v.message}")

    for w in res.warnings:
        p(f"[경고] {w}")

    if not res.combined:
        p("\n안정 호버 구간을 찾지 못했습니다. 10~20초 정도 제자리 호버를 포함해 다시 기록하세요.")
        if craft.mode == config.MODE_STRICT:
            p("→ --mode normal 로 다시 시도해 볼 수 있습니다.")
        return

    rec = res.recommendation
    p("\n" + "-" * 64)
    label = "호버 스로틀" if craft.mode == config.MODE_STRICT else "호버 스로틀 (크루징 포함 추정치)"
    p(f"{label}: 스틱 {rec.stick_pct:.1f}% / 모터 {rec.motor_pct:.1f}%"
      f"   [{len(res.flights)}개 비행, 구간 {res.total_segments}개, 총 {res.total_hover_s:.1f}s]")
    if res.low_confidence:
        p(f"신뢰도 낮음: 호버 구간 총합이 {config.LOW_CONFIDENCE_TOTAL_S:.0f}초 미만입니다."
          + (" --mode normal 재분석을 권장합니다." if craft.mode == config.MODE_STRICT else ""))
    lo, hi = rec.status_range
    p(f"기체 상태: {rec.status_text}  (참고 범위 모터 {lo:.0f}~{hi:.0f}%)")
    cur = lambda v: "—" if v is None else str(v)  # noqa: E731
    p(f"thr_mid : 현재 {cur(rec.current_thr_mid)} → 추천 {rec.thr_mid}")
    p(f"thr_expo: 현재 {cur(rec.current_thr_expo)} → 추천 {rec.thr_expo} "
      f"(범위 {rec.thr_expo_range[0]}~{rec.thr_expo_range[1]}, {rec.thr_expo_meaning}) — 시작점이며 취향에 맞게 조정하세요")
    if rec.throttle_limit_percent is not None:
        p(f"선택 사항: throttle_limit_type=SCALE, throttle_limit_percent={rec.throttle_limit_percent} "
          "(펀치아웃 출력이 줄어듭니다)")
    if rec.twr_range:
        p(f"추력비(근사치): {rec.twr_range[0]:.1f} ~ {rec.twr_range[1]:.1f} : 1"
          + (f", 최대 추력 ≈ {rec.max_thrust_g[0]:.0f} ~ {rec.max_thrust_g[1]:.0f} g" if rec.max_thrust_g else ""))
    for w in rec.warnings:
        p(f"[경고] {w}")
    p("\n" + rec.cli)


def _cmd_list(args) -> int:
    for path in args.files:
        try:
            flights = list_flights(path)
        except (LogReadError, OSError) as e:
            print(f"{path}: {getattr(e, 'message', e)}")
            continue
        print(f"{path}: 비행 {len(flights)}개")
        for f in flights:
            size = f"{f.size_bytes / 1024:.0f} KB" if f.size_bytes else "?"
            print(f"  #{f.index}  {size}  {f.start_time or ''}")
    return 0


def _cmd_analyze(args) -> int:
    targets: list[tuple[str, int]] = []
    for path in args.files:
        if not Path(path).is_file():
            print(f"파일이 없습니다: {path}")
            return 2
        if args.index:
            targets += [(path, i) for i in args.index]
        else:
            try:
                targets += [(path, f.index) for f in list_flights(path)]
            except LogReadError as e:
                print(f"{path}: {e.message}")
                return 2
    craft = pipeline.CraftInput(
        craft_class=args.craft_class,
        cells=args.cells,
        name=args.name or "",
        weight_g=args.weight,
        thr_mid=args.thr_mid,
        thr_expo=args.thr_expo,
        mode=args.mode,
    )

    def progress(p, msg):
        if not args.quiet:
            print(f"\r[{p * 100:5.1f}%] {msg[-60:]:<60}", end="", file=sys.stderr, flush=True)

    res = pipeline.run(targets, craft, progress)
    if not args.quiet:
        print(file=sys.stderr)
    _print_result(res)

    if args.dump_windows:
        for fa in res.flights:
            out = Path(args.dump_windows) / f"{Path(fa.log.path).stem}_{fa.log.index}_windows.csv"
            fa.detection.windows.to_csv(out, index=False)
            print(f"윈도우 진단 CSV: {out}")
    return 0 if res.combined else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(prog="hoverthrottle", description="FPV 호버링 스로틀 추천")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="로그 파일 안의 비행 목록")
    p_list.add_argument("files", nargs="+")
    p_list.set_defaults(func=_cmd_list)

    p_an = sub.add_parser("analyze", help="호버 스로틀 분석")
    p_an.add_argument("files", nargs="+")
    p_an.add_argument("--class", dest="craft_class", required=True, choices=list(config.CRAFT_CLASSES))
    p_an.add_argument("--cells", type=int, choices=range(1, 7), help="배터리 셀 수 (생략 시 자동 추정)")
    p_an.add_argument("--mode", choices=list(config.MODES), default=config.MODE_STRICT)
    p_an.add_argument("--index", type=int, action="append", help="분석할 로그 인덱스 (반복 가능, 기본: 전체)")
    p_an.add_argument("--weight", type=float, help="이륙 중량 g")
    p_an.add_argument("--thr-mid", type=int, help="현재 thr_mid (기본: 헤더)")
    p_an.add_argument("--thr-expo", type=int, help="현재 thr_expo (기본: 헤더)")
    p_an.add_argument("--name", help="기체 이름")
    p_an.add_argument("--dump-windows", metavar="DIR", help="윈도우별 판정 지표 CSV 저장 폴더 (임계값 검토용)")
    p_an.add_argument("-q", "--quiet", action="store_true")
    p_an.set_defaults(func=_cmd_analyze)

    args = ap.parse_args(argv)
    return args.func(args)
