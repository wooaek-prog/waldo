#!/usr/bin/env python3
"""CSV 기반 금번(2차) 행정·공공기관 이전 대상 리스트 정리 도구.

입력 CSV에서 기관명, 기관유형, 이전지역, 확정단계 등을 읽어 확정단계 우선순위로
정렬한 이전 대상 리스트와 구분별 집계를 산출합니다.
"""

from __future__ import annotations

import argparse
import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


COLUMN_ALIASES = {
    "name": ("기관명", "name", "institution", "기관"),
    "category": ("기관유형", "category", "type", "구분"),
    "ministry": ("소관부처", "ministry", "부처"),
    "current_location": ("현소재지", "current_location", "소재지", "현재소재지"),
    "target_region": ("이전지역", "target_region", "region", "이전지"),
    "schedule": ("이전시기", "schedule", "timeline", "시기"),
    "stage": ("확정단계", "stage", "status", "상태"),
    "source": ("근거", "source", "출처"),
    "note": ("비고", "note", "remark", "메모"),
}

REQUIRED_COLUMNS = ("name", "category", "target_region", "stage")

# 확정단계 정렬 우선순위. 값이 작을수록 확정에 가깝습니다.
STAGE_ORDER = {
    "발표확정": 1,
    "발표검토": 2,
    "언론거론": 3,
    "지자체건의": 4,
    "미정": 5,
}
UNKNOWN_STAGE_ORDER = 99

OUTPUT_FIELDNAMES = [
    "순번",
    "확정단계",
    "기관명",
    "기관유형",
    "소관부처",
    "현소재지",
    "이전지역",
    "이전시기",
    "근거",
    "비고",
]


@dataclass(frozen=True)
class Institution:
    name: str
    category: str
    ministry: str
    current_location: str
    target_region: str
    schedule: str
    stage: str
    source: str
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="금번 행정·공공기관 이전 대상 리스트를 확정단계 기준으로 정리합니다."
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=Path("data/relocation_targets.csv"),
        help="입력 이전 대상 CSV 경로 (기본값: data/relocation_targets.csv)",
    )
    parser.add_argument("--stage", action="append", help="확정단계 필터 (여러 번 지정 가능)")
    parser.add_argument("--category", action="append", help="기관유형 필터 (여러 번 지정 가능)")
    parser.add_argument("--region", action="append", help="이전지역 부분일치 필터 (여러 번 지정 가능)")
    parser.add_argument("--keyword", help="기관명/소관부처/비고 부분일치 검색어")
    parser.add_argument("--output", type=Path, help="정렬된 이전 대상 리스트 CSV 경로")
    parser.add_argument("--summary", type=Path, help="확정단계·기관유형·이전지역 집계 CSV 경로")
    parser.add_argument("--quiet", action="store_true", help="표 출력을 생략합니다.")
    return parser.parse_args()


def normalize_header(header: str) -> str:
    return header.strip().lower().replace(" ", "_")


def resolve_columns(fieldnames: Sequence[str] | None) -> dict[str, str]:
    if not fieldnames:
        raise ValueError("입력 CSV에 헤더가 없습니다.")

    normalized_to_original = {normalize_header(name): name for name in fieldnames}
    resolved: dict[str, str] = {}

    for standard_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            original = normalized_to_original.get(normalize_header(alias))
            if original is not None:
                resolved[standard_name] = original
                break
        else:
            if standard_name in REQUIRED_COLUMNS:
                alias_list = ", ".join(aliases)
                raise ValueError(
                    f"필수 컬럼 '{standard_name}'을 찾을 수 없습니다. 허용 별칭: {alias_list}"
                )

    return resolved


def get_value(row: dict[str, str], columns: dict[str, str], key: str) -> str:
    column = columns.get(key)
    if column is None:
        return ""
    return (row.get(column) or "").strip()


def read_institutions(input_path: Path) -> list[Institution]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = resolve_columns(reader.fieldnames)
        rows: list[Institution] = []

        for row_number, row in enumerate(reader, start=2):
            name = get_value(row, columns, "name")
            if not name:
                raise ValueError(f"{row_number}행 기관명이 비어 있습니다.")

            rows.append(
                Institution(
                    name=name,
                    category=get_value(row, columns, "category"),
                    ministry=get_value(row, columns, "ministry"),
                    current_location=get_value(row, columns, "current_location"),
                    target_region=get_value(row, columns, "target_region"),
                    schedule=get_value(row, columns, "schedule"),
                    stage=get_value(row, columns, "stage") or "미정",
                    source=get_value(row, columns, "source"),
                    note=get_value(row, columns, "note"),
                )
            )

    if not rows:
        raise ValueError("입력 CSV에 기관 데이터가 없습니다.")
    return rows


def stage_rank(stage: str) -> int:
    return STAGE_ORDER.get(stage, UNKNOWN_STAGE_ORDER)


def matches_any(value: str, patterns: Sequence[str] | None, *, exact: bool) -> bool:
    if not patterns:
        return True
    if exact:
        return any(value == pattern.strip() for pattern in patterns)
    return any(pattern.strip() in value for pattern in patterns)


def filter_institutions(rows: Iterable[Institution], args: argparse.Namespace) -> list[Institution]:
    filtered: list[Institution] = []
    for row in rows:
        if not matches_any(row.stage, args.stage, exact=True):
            continue
        if not matches_any(row.category, args.category, exact=True):
            continue
        if not matches_any(row.target_region, args.region, exact=False):
            continue
        if args.keyword:
            haystack = " ".join((row.name, row.ministry, row.note))
            if args.keyword.strip() not in haystack:
                continue
        filtered.append(row)
    return filtered


def sort_institutions(rows: Iterable[Institution]) -> list[Institution]:
    return sorted(rows, key=lambda row: (stage_rank(row.stage), row.category, row.name))


def to_output_row(order: int, row: Institution) -> dict[str, str | int]:
    return {
        "순번": order,
        "확정단계": row.stage,
        "기관명": row.name,
        "기관유형": row.category,
        "소관부처": row.ministry,
        "현소재지": row.current_location,
        "이전지역": row.target_region,
        "이전시기": row.schedule,
        "근거": row.source,
        "비고": row.note,
    }


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(width - display_width(text), 0)


def print_table(rows: Sequence[Institution]) -> None:
    headers = ["순번", "확정단계", "기관명", "기관유형", "이전지역", "이전시기"]
    table = [headers]
    for order, row in enumerate(rows, start=1):
        table.append(
            [str(order), row.stage, row.name, row.category, row.target_region, row.schedule]
        )

    widths = [max(display_width(cell) for cell in column) for column in zip(*table)]
    for index, cells in enumerate(table):
        print("  ".join(pad(cell, width) for cell, width in zip(cells, widths)).rstrip())
        if index == 0:
            print("  ".join("-" * width for width in widths))


def write_list(output_path: Path, rows: Sequence[Institution]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=OUTPUT_FIELDNAMES)
        writer.writeheader()
        for order, row in enumerate(rows, start=1):
            writer.writerow(to_output_row(order, row))


def count_by(rows: Sequence[Institution], key: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        value = getattr(row, key) or "미기재"
        counts[value] = counts.get(value, 0) + 1

    if key == "stage":
        return sorted(counts.items(), key=lambda item: (stage_rank(item[0]), item[0]))
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def write_summary(output_path: Path, rows: Sequence[Institution]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    groups = (("확정단계", "stage"), ("기관유형", "category"), ("이전지역", "target_region"))

    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["구분", "항목", "기관수"])
        writer.writeheader()
        for label, key in groups:
            for value, count in count_by(rows, key):
                writer.writerow({"구분": label, "항목": value, "기관수": count})


def print_stage_counts(rows: Sequence[Institution]) -> None:
    print()
    print("확정단계별 기관수")
    for value, count in count_by(rows, "stage"):
        print(f"- {value}: {count}건")


def main() -> None:
    args = parse_args()
    institutions = read_institutions(args.input)
    selected = sort_institutions(filter_institutions(institutions, args))

    if not selected:
        print("조건에 해당하는 기관이 없습니다.")
        return

    if not args.quiet:
        print_table(selected)
        print_stage_counts(selected)

    if args.output:
        write_list(args.output, selected)
        print(f"\n이전 대상 {len(selected)}건을 '{args.output}'에 저장했습니다.")
    if args.summary:
        write_summary(args.summary, selected)
        print(f"집계 결과를 '{args.summary}'에 저장했습니다.")


if __name__ == "__main__":
    main()
