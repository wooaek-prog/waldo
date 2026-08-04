#!/usr/bin/env python3
"""재건축 신축 매싱이 인접 학교 창문 일조에 미치는 영향을 계산하는 CLI.

사용 예:
    python3 sunlight_analysis.py data/site_yeouido_40_4.json \\
        --date 2026-12-22 --output window_report.csv --summary school_summary.csv

기준일은 별도 지정하지 않으면 해당 연도의 동지(12월 22일, 근사치)를 사용한다.
동지는 태양 고도가 가장 낮아 그림자가 가장 길게 지는 '최악 조건일'이므로
일조권 검토의 관행적 기준일로 쓰인다.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from collections import defaultdict

from sunlight.analysis import WindowResult, analyze_site
from sunlight.model import load_site_model


def _default_winter_solstice() -> dt.date:
    today = dt.date.today()
    year = today.year if today.month <= 12 else today.year + 1
    candidate = dt.date(year, 12, 22)
    if candidate < today:
        candidate = dt.date(year + 1, 12, 22)
    return candidate


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="부지/매싱/학교 창문 정의 JSON 파일")
    parser.add_argument(
        "--date",
        default=None,
        help="분석 기준일 YYYY-MM-DD (기본값: 가장 가까운 동지, 12/22 근사)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="시간 샘플 간격(분), 기본 10분",
    )
    parser.add_argument("--output", default="window_report.csv", help="창문별 결과 CSV 경로")
    parser.add_argument(
        "--summary", default="school_summary.csv", help="학교/동/층별 요약 CSV 경로"
    )
    parser.add_argument(
        "--timeline",
        default=None,
        help="지정 시 시간대별 상세(태양 고도/방위/음영여부) CSV를 이 경로에 저장",
    )
    return parser.parse_args(argv)


def _write_window_report(path: str, results: list[WindowResult]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "학교",
                "동",
                "입면",
                "층",
                "창문ID",
                "X(m)",
                "Y(m)",
                "Z(m)",
                "총일조시간(08-16)",
                "연속일조시간(09-15)",
                "총량기준(4h)충족",
                "연속기준(2h)충족",
                "종합판정",
            ]
        )
        for r in results:
            w = r.window
            writer.writerow(
                [
                    w.school_name,
                    w.building_id,
                    w.facade_name,
                    w.floor,
                    w.id,
                    round(w.position[0], 2),
                    round(w.position[1], 2),
                    round(w.position[2], 2),
                    round(r.total_sunlit_hours, 2),
                    round(r.max_continuous_hours, 2),
                    "O" if r.total_ok else "X",
                    "O" if r.continuous_ok else "X",
                    "적합" if r.meets_standard else "부적합",
                ]
            )


def _write_summary(path: str, results: list[WindowResult]) -> None:
    groups: dict[tuple[str, str, int], list[WindowResult]] = defaultdict(list)
    for r in results:
        w = r.window
        groups[(w.school_name, f"{w.building_id}/{w.facade_name}", w.floor)].append(r)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "학교",
                "동/입면",
                "층",
                "창문수",
                "최소총일조시간",
                "최소연속일조시간",
                "기준미달창문수",
                "판정",
            ]
        )
        for (school, facade, floor), group in sorted(groups.items()):
            min_total = min(g.total_sunlit_hours for g in group)
            min_cont = min(g.max_continuous_hours for g in group)
            fail_count = sum(1 for g in group if not g.meets_standard)
            verdict = "적합" if fail_count == 0 else f"부적합({fail_count}개 창)"
            writer.writerow(
                [
                    school,
                    facade,
                    floor,
                    len(group),
                    round(min_total, 2),
                    round(min_cont, 2),
                    fail_count,
                    verdict,
                ]
            )


def _write_timeline(path: str, results: list[WindowResult]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["창문ID", "시각", "태양고도(deg)", "태양방위(deg)", "일조여부"])
        for r in results:
            for s in r.samples:
                writer.writerow(
                    [
                        r.window.id,
                        s.time.strftime("%Y-%m-%d %H:%M"),
                        round(s.elevation_deg, 2),
                        round(s.azimuth_deg, 2),
                        "일조" if s.sunlit else "음영",
                    ]
                )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    day = dt.date.fromisoformat(args.date) if args.date else _default_winter_solstice()

    model = load_site_model(args.config)
    results = analyze_site(model, day, interval_minutes=args.interval)

    _write_window_report(args.output, results)
    _write_summary(args.summary, results)
    if args.timeline:
        _write_timeline(args.timeline, results)

    total = len(results)
    fails = [r for r in results if not r.meets_standard]
    print(f"부지: {model.location.name} ({model.location.parcel})")
    print(f"분석 기준일: {day.isoformat()} / 창문 샘플 간격: {args.interval}분")
    print(f"검토 대상 창문 수: {total}")
    print(f"기준 미달 창문 수: {len(fails)} ({(len(fails) / total * 100 if total else 0):.1f}%)")
    if fails:
        worst = min(fails, key=lambda r: (r.total_sunlit_hours, r.max_continuous_hours))
        print(
            "최악 사례: "
            f"{worst.window.school_name} {worst.window.building_id}/{worst.window.facade_name} "
            f"{worst.window.floor}층 - 총일조 {worst.total_sunlit_hours:.2f}h, "
            f"연속일조 {worst.max_continuous_hours:.2f}h"
        )
    print(f"창문별 결과: {args.output}")
    print(f"학교/동/층별 요약: {args.summary}")
    if args.timeline:
        print(f"시간대별 상세: {args.timeline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
