# 모션 그래픽 템플릿

HTML + CSS + SVG (+ 약간의 JS) 로 만든 템플릿을 헤드리스 크롬에서 **프레임 단위로 캡처**해 투명 배경 영상으로 만든다.
같은 템플릿을 브라우저나 (이후) Tauri 앱 웹뷰에서 열면 실시간 미리보기가 된다.

```
motion/
├─ runtime/runtime.js, runtime.css   # 모든 템플릿 공통 규약 (시간 제어, 파라미터 주입, 글자 맞춤, 점검)
├─ templates/<id>.html               # 템플릿 = HTML 파일 하나
└─ fonts/PretendardVariable.woff2    # 기본 폰트 (setup/install_mac.sh 가 내려받음, git 제외)
```

## 사용법

```bash
python -m vedit motion list                                   # 템플릿과 파라미터
python -m vedit motion preview place_lower_third -p title=후시미이나리   # 브라우저 실시간 미리보기
python -m vedit motion check place_lower_third -p title=아주아주긴장소이름입니다   # 넘침/안전영역/오류 점검
python -m vedit motion stills day_card --times 0.5 1.6 2.9     # 정지 이미지
python -m vedit motion render opening_title --scale 2          # 4K 투명 영상 (motion_out/opening_title.webm)
python -m vedit motion render place_lower_third --over clip.mp4 --at 2.0   # 영상 위 합성 미리보기 mp4
python -m vedit motion render opening_title --codec prores4444 # DaVinci/Final Cut 용 ProRes 4444 (.mov)
python -m vedit motion render day_card --project kyoto_2026    # 그 프로젝트 디자인 가이드 색·폰트 적용
```

파이프라인에서는 `motion_context → motion_spec(Claude) → motion` 단계가 EDL 에 맞춰 자동 배치·렌더한다
(`projects/<이름>/motion/*.webm`, 승인용 `*.png`, `manifest.json`).

## 출력 형식

| 코덱 | 용도 | 1080p 5초 크기 |
|---|---|---|
| `vp9` (.webm, 기본) | 파이프라인 중간 파일, 우리 ffmpeg 합성용 | 약 1~3MB |
| `prores4444` (.mov) | DaVinci Resolve / Final Cut / Premiere 로 넘길 때 | 약 100~400MB |
| `png` (.mov) | 무손실 확인용 | 약 100MB 이상 |

알파가 있는 VP9 는 ffmpeg 에서 **`-c:v libvpx-vp9` 를 `-i` 앞에** 줘야 투명도가 살아난다 (`motion.input_args()` 가 처리).

## 템플릿 작성 규약 (사람/AI 공통)

1. **파일 하나**: `templates/<id>.html`. `<head>` 에 아래 순서로 넣는다.
   ```html
   <script type="application/json" id="vedit-meta">{ ...메타... }</script>
   <link rel="stylesheet" href="../runtime/runtime.css">
   <script src="../runtime/runtime.js"></script>
   ```
2. **메타** (`vedit-meta`): `id`(파일명과 같게), `name`, `description`, `category`(title / lower_third / transition / info),
   `duration`(초), `hold`(모든 요소가 다 보이는 '정지 구간' 시각 — 점검과 승인용 이미지에 사용), `params`.
   - 파라미터: `{"type": "text|color|font|number", "label": "...", "default": ..., "from": "palette.accent"}`
   - `from` 이 있으면 프로젝트 디자인 가이드 값이 자동으로 들어간다 (AI 배치 단계에서는 숨김).
3. **크기**: 1920×1080 기준 px 로 작성하고 모든 요소를 `<div class="stage">` 안에 둔다. 4K 는 렌더러가 2배 배율로 처리.
   중요한 글자는 가장자리 5% 안전 영역 안에.
4. **파라미터 사용**
   - 글자: `<span data-param="title"></span>` → 자동 주입. 비어 있으면 `is-empty` 로 숨김.
   - 색/값: CSS 변수 `var(--accent)`, `var(--duration)` (밑줄은 하이픈: `font_title` → `--font-title`).
   - 폰트: `var(--font-title-family)`, `var(--font-title-weight)` ("Pretendard Black" → family + 900 으로 분리됨).
   - 길이가 들쭉날쭉한 제목에는 `data-fit data-fit-min="56"` + `white-space: nowrap; max-width: …px` → 넘치면 글자 크기 자동 축소.
5. **움직임은 CSS 애니메이션 / Web Animations API 만** 쓴다. 렌더러가 `document.getAnimations()` 의 시간을 직접 옮긴다.
   - 금지: `setTimeout`, `setInterval`, `Date.now()`, 직접 돌리는 `requestAnimationFrame` 루프 (프레임 캡처와 어긋남).
   - JS 로 계산해야 하는 값(숫자 카운터 등)은 `VEDIT.onSeek(t => …)` 에서 t 로부터 계산.
   - 글자 쪼개기 같은 DOM 구성은 `VEDIT.build(params => …)` 안에서 (폰트 로드·글자 맞춤 전에 실행됨).
6. **등장은 `fill-mode: both`, 퇴장은 `forwards`**.
   퇴장 애니메이션에 `both` 를 쓰면 `from` 값이 0초부터 적용돼 첫 프레임이 깨진다 (`check` 가 잡아냄).
   퇴장 시작은 `animation-delay: calc(var(--duration) * 1s - .6s)` 처럼 길이에 맞춰 계산.
7. **영상 위에 얹는 그래픽은 첫·마지막 프레임이 투명**해야 한다 (전환 카드 포함). 배경은 투명 유지.
8. **외부 자원 금지**: 웹 폰트 CDN, 외부 이미지, 외부 스크립트 모두 차단된다. 필요한 파일은 `motion/` 안에 두고 상대 경로로.
9. **완성 후 반드시** `python -m vedit motion check <id>` → 오류/경고 0 확인, `stills` 로 눈으로 확인.

## AI 로 새 템플릿 만들기 (Claude Code 대화형)

```
motion/README.md 의 규약을 지켜서 motion/templates/retro_place.html 을 만들어 줘.
70년대 필름 느낌 장소 타이틀, 4초, 파라미터는 title/subtitle/accent/text/font_title.
만든 뒤 python -m vedit motion check retro_place 와 stills 로 확인하고 경고가 없게 고쳐 줘.
```
점검을 통과하고 사람이 stills 를 승인한 템플릿만 `templates/` 에 남긴다 → 채널 전용 모션 라이브러리가 쌓인다.

## 성능 (참고: 클라우드 테스트 환경 실측, iMac 2019 에서는 다시 측정 필요)

| 템플릿 | 1080p | 4K (`--scale 2`) |
|---|---|---|
| place_lower_third (4.5초) | 약 12초 | 약 53초 |
| day_card (3.2초) | 약 13초 | — |
| opening_title (5초, 블러 효과) | 약 30초 | — |

파이프라인 `motion` 단계는 `config/local.yaml` 의 `motion.workers` 개수만큼 동시에 렌더한다.
