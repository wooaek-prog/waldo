# 인쇄용 워크북 빌드

`docs/재건축_시공사선정_건설비검증_대의원교재.md`의 내용을 A4 인쇄용으로 재편집한 워크북 소스입니다.

| 파일 | 설명 |
| --- | --- |
| `워크북.html` | 본문 + 인라인 SVG 삽화 17종 + 인쇄용 CSS(A4, 페이지 브레이크 제어) |
| `build_pdf.sh` | Chromium 헤드리스로 PDF 렌더링 후 페이지 번호 스탬프 |
| `stamp.py` | 러닝 푸터·페이지 번호를 찍는 후처리 (표지 제외) |

산출물: `docs/재건축_시공사선정_건설비검증_대의원워크북.pdf` (표지 포함 44쪽)

## 빌드

```bash
./docs/workbook/build_pdf.sh
```

### 사전 요구사항

- **한글 폰트** — `fonts-noto-cjk` 또는 `fonts-nanum`
- **Chromium** — 헤드리스 렌더링용. `CHROME` 환경변수로 경로 지정 가능
- **Python 패키지** — `reportlab`, `PyPDF2` (페이지 번호 스탬프용)

```bash
apt-get install -y fonts-noto-cjk fonts-nanum
pip install reportlab pypdf2
```

빌드 스크립트는 `reportlab`과 `PyPDF2`가 함께 import 되는 인터프리터를 자동으로 탐색합니다.

## 편집

내용 수정은 `워크북.html`에서 합니다. 삽화는 모두 인라인 SVG라 별도 이미지 파일이 없으며,
텍스트·수치를 HTML에서 직접 고칠 수 있습니다.

페이지 나눔은 CSS로 제어합니다.

- `.chapnum` — 챕터 시작 시 강제 페이지 나눔
- `figure`, `table.d`, `.box`, `.ws`, `.ckl .ck` — 내부 분리 금지
- `.ckl` — 체크리스트는 페이지를 넘겨 이어짐 (여백 최소화)
