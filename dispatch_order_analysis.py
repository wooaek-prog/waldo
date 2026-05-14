#!/usr/bin/env python3
"""CSV 기반 한국 발전소 업체별 급전순위 분석 도구.

입력 CSV에서 회사명, 발전기명, 용량, 단가를 읽어 단가 오름차순 기준의
급전순위와 누계용량을 계산합니다.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


COLUMN_ALIASES = {
    "company": ("회사명", "company", "owner", "업체명"),
    "generator": ("발전기명", "generator", "unit", "plant", "발전소명"),
    "capacity": ("용량", "capacity", "mw", "설비용량"),
    "unit_price": ("단가", "price", "unit_price", "cost", "변동비"),
}


@dataclass(frozen=True)
class GeneratorRow:
    company: str
    generator: str
    capacity: float
    unit_price: float


@dataclass(frozen=True)
class DispatchRow:
    rank: int
    company: str
    generator: str
    capacity: float
    cumulative_capacity: float
    company_cumulative_capacity: float
    unit_price: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="단가를 감안한 한국 발전소 업체별 급전순위를 CSV로 산출합니다."
    )
    parser.add_argument("input", type=Path, help="입력 발전기 CSV 파일 경로")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dispatch_order.csv"),
        help="급전순위 출력 CSV 경로 (기본값: dispatch_order.csv)",
    )
    parser.add_argument(
        "--company-summary",
        type=Path,
        help="업체별 용량/단가 요약 CSV 경로",
    )
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
            alias_list = ", ".join(aliases)
            raise ValueError(
                f"필수 컬럼 '{standard_name}'을 찾을 수 없습니다. 허용 별칭: {alias_list}"
            )

    return resolved


def parse_float(value: str, *, row_number: int, column_name: str) -> float:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        raise ValueError(f"{row_number}행 '{column_name}' 값이 비어 있습니다.")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"{row_number}행 '{column_name}' 값 '{value}'을 숫자로 변환할 수 없습니다."
        ) from exc


def read_generators(input_path: Path) -> list[GeneratorRow]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = resolve_columns(reader.fieldnames)
        rows: list[GeneratorRow] = []

        for row_number, row in enumerate(reader, start=2):
            company = row[columns["company"]].strip()
            generator = row[columns["generator"]].strip()
            if not company:
                raise ValueError(f"{row_number}행 회사명이 비어 있습니다.")
            if not generator:
                raise ValueError(f"{row_number}행 발전기명이 비어 있습니다.")

            capacity = parse_float(
                row[columns["capacity"]], row_number=row_number, column_name=columns["capacity"]
            )
            unit_price = parse_float(
                row[columns["unit_price"]],
                row_number=row_number,
                column_name=columns["unit_price"],
            )
            if capacity < 0:
                raise ValueError(f"{row_number}행 용량은 음수일 수 없습니다.")
            if unit_price < 0:
                raise ValueError(f"{row_number}행 단가는 음수일 수 없습니다.")

            rows.append(
                GeneratorRow(
                    company=company,
                    generator=generator,
                    capacity=capacity,
                    unit_price=unit_price,
                )
            )

    if not rows:
        raise ValueError("입력 CSV에 발전기 데이터가 없습니다.")
    return rows


def build_dispatch_order(rows: Iterable[GeneratorRow]) -> list[DispatchRow]:
    sorted_rows = sorted(
        rows,
        key=lambda row: (row.unit_price, row.company, row.generator, -row.capacity),
    )
    cumulative_capacity = 0.0
    company_cumulative: dict[str, float] = {}
    dispatch_rows: list[DispatchRow] = []

    for rank, row in enumerate(sorted_rows, start=1):
        cumulative_capacity += row.capacity
        company_cumulative[row.company] = company_cumulative.get(row.company, 0.0) + row.capacity
        dispatch_rows.append(
            DispatchRow(
                rank=rank,
                company=row.company,
                generator=row.generator,
                capacity=row.capacity,
                cumulative_capacity=cumulative_capacity,
                company_cumulative_capacity=company_cumulative[row.company],
                unit_price=row.unit_price,
            )
        )

    return dispatch_rows


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def write_dispatch_order(output_path: Path, rows: Sequence[DispatchRow]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "급전순위",
                "회사명",
                "발전기명",
                "용량",
                "누계용량",
                "업체별누계용량",
                "단가",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "급전순위": row.rank,
                    "회사명": row.company,
                    "발전기명": row.generator,
                    "용량": format_number(row.capacity),
                    "누계용량": format_number(row.cumulative_capacity),
                    "업체별누계용량": format_number(row.company_cumulative_capacity),
                    "단가": format_number(row.unit_price),
                }
            )


def write_company_summary(output_path: Path, rows: Sequence[DispatchRow]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    companies: dict[str, list[DispatchRow]] = {}
    for row in rows:
        companies.setdefault(row.company, []).append(row)

    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["회사명", "발전기수", "총용량", "최소단가", "가중평균단가", "최대단가"],
        )
        writer.writeheader()
        for company, company_rows in sorted(companies.items()):
            total_capacity = sum(row.capacity for row in company_rows)
            weighted_total = sum(row.capacity * row.unit_price for row in company_rows)
            weighted_average = weighted_total / total_capacity if total_capacity else 0.0
            writer.writerow(
                {
                    "회사명": company,
                    "발전기수": len(company_rows),
                    "총용량": format_number(total_capacity),
                    "최소단가": format_number(min(row.unit_price for row in company_rows)),
                    "가중평균단가": format_number(weighted_average),
                    "최대단가": format_number(max(row.unit_price for row in company_rows)),
                }
            )


def main() -> None:
    args = parse_args()
    generators = read_generators(args.input)
    dispatch_rows = build_dispatch_order(generators)
    write_dispatch_order(args.output, dispatch_rows)
    if args.company_summary:
        write_company_summary(args.company_summary, dispatch_rows)

    print(f"급전순위 {len(dispatch_rows)}건을 '{args.output}'에 저장했습니다.")
    if args.company_summary:
        print(f"업체별 요약을 '{args.company_summary}'에 저장했습니다.")


if __name__ == "__main__":
    main()
