#!/usr/bin/env python3
"""렌더링된 PDF에 러닝 푸터와 페이지 번호를 찍는다. (표지 제외)"""
import io
import sys
import glob

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PyPDF2 import PdfReader, PdfWriter

FOOTER_LEFT = "재건축조합 대의원 교육  |  시공사 선정 시 건설비 검증"
FOOTER_RIGHT = "대우건설 하이엔드 '써밋' 검증 사례"

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def register_font() -> str:
    for path in FONT_CANDIDATES:
        for hit in glob.glob(path):
            try:
                pdfmetrics.registerFont(TTFont("KR", hit))
                return "KR"
            except Exception:
                continue
    return "Helvetica"


def stamp(src: str, dst: str) -> None:
    font = register_font()
    reader = PdfReader(src)
    writer = PdfWriter()
    width, height = A4
    total = len(reader.pages)

    for idx, page in enumerate(reader.pages):
        if idx > 0:  # 표지는 건너뜀
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=A4)
            c.setStrokeColorRGB(0.84, 0.86, 0.88)
            c.setLineWidth(0.5)
            c.line(42, 34, width - 42, 34)

            c.setFont(font, 6.8)
            c.setFillColorRGB(0.42, 0.46, 0.50)
            c.drawString(42, 24, FOOTER_LEFT)
            c.drawRightString(width - 42, 24, FOOTER_RIGHT)

            c.setFont(font, 8.5)
            c.setFillColorRGB(0.086, 0.196, 0.310)
            c.drawCentredString(width / 2, 22, f"— {idx} / {total - 1} —")
            c.save()
            buf.seek(0)
            page.merge_page(PdfReader(buf).pages[0])
        writer.add_page(page)

    writer.add_metadata(
        {
            "/Title": "시공사 선정 시 건설비 검증 — 재건축조합 대의원 워크북",
            "/Subject": "재건축조합 대의원 교육 교재 (인쇄용)",
            "/Keywords": "재건축, 정비사업, 시공사 선정, 공사비 검증, 하이엔드 브랜드, 써밋",
        }
    )
    with open(dst, "wb") as fh:
        writer.write(fh)
    print(f"  총 {total}쪽 (본문 {total - 1}쪽)")


if __name__ == "__main__":
    stamp(sys.argv[1], sys.argv[2])
