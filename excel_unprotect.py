#!/usr/bin/env python3
"""Remove Excel worksheet protection metadata from .xlsx files.

주의: 본 스크립트는 본인이 소유했거나 명시적 권한을 받은 파일에만 사용하세요.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET


NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def remove_sheet_protection(xlsx_path: Path, output_path: Path) -> int:
    """Remove <sheetProtection> tags from all worksheet xml files.

    Returns the number of sheets modified.
    """
    modified_count = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        with zipfile.ZipFile(xlsx_path, "r") as zin:
            zin.extractall(tmpdir_path)

        worksheets_dir = tmpdir_path / "xl" / "worksheets"
        if worksheets_dir.exists():
            for xml_file in worksheets_dir.glob("*.xml"):
                tree = ET.parse(xml_file)
                root = tree.getroot()

                protections = root.findall("main:sheetProtection", NS)
                if not protections:
                    continue

                for protection in protections:
                    root.remove(protection)

                tree.write(xml_file, encoding="utf-8", xml_declaration=True)
                modified_count += 1

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for file in tmpdir_path.rglob("*"):
                if file.is_file():
                    arcname = file.relative_to(tmpdir_path)
                    zout.write(file, arcname)

    return modified_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=".xlsx 파일의 시트 보호 태그(sheetProtection)를 제거합니다."
    )
    parser.add_argument("input", type=Path, help="입력 .xlsx 파일 경로")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="출력 파일 경로 (기본값: 원본명_unprotected.xlsx)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="원본 파일을 직접 덮어씁니다 (백업 파일 .bak 생성)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.input.exists() or args.input.suffix.lower() != ".xlsx":
        raise SystemExit("입력 파일은 존재하는 .xlsx 파일이어야 합니다.")

    if args.in_place:
        backup = args.input.with_suffix(args.input.suffix + ".bak")
        shutil.copy2(args.input, backup)
        target_output = args.input
    else:
        target_output = args.output or args.input.with_name(
            f"{args.input.stem}_unprotected.xlsx"
        )

    modified = remove_sheet_protection(args.input, target_output)

    if args.in_place:
        print(f"완료: {modified}개 시트 보호 제거, 원본 덮어쓰기. 백업: {backup}")
    else:
        print(f"완료: {modified}개 시트 보호 제거, 출력 파일: {target_output}")


if __name__ == "__main__":
    main()
