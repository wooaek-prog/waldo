# travel-vedit — 여행 유튜브 자동편집 (개인용)

촬영 원본 폴더를 넣으면 **로컬 전처리 → AI 분석 → 디자인 가이드 → 편집 결정(EDL)** 까지 단계별로 진행하는 파이프라인입니다.
무거운 처리는 모두 로컬(무료)에서 하고, 판단이 필요한 단계만 구독 중인 AI CLI(Claude Code, Antigravity)를 호출합니다.

- 대상 장비: iMac 2019 27" (i5-8500 3.0GHz 6코어, Radeon Pro 570X, 24GB, SSD, macOS)
- 현재 범위: 설치, 전처리, 분석, 디자인 가이드, EDL 생성·검증, **모션 그래픽(HTML/CSS/SVG 템플릿 → 투명 영상)**
- 다음 범위: 렌더(LUT·자막·BGM·모션 합성), 타이틀/썸네일 이미지 생성, 검수 루프, 유튜브 문구, 로컬 서버 + Tauri 2 앱

## 구조

```
원본(읽기 전용) ─▶ ingest ─▶ proxy ─▶ scenes ─▶ quality ─▶ keyframes ─▶ stt ─▶ silence ─▶ palette ─▶ bundle
                                                                                                   │
           edl_check ◀─ edl(Claude) ◀─ [승인] moodboard ◀─ design(Claude) ◀─ snap ◀─ analyze(Antigravity)
              │
            [승인] ─▶ motion_context ─▶ motion_spec(Claude) ─▶ [승인] motion ─▶ (다음: render / assets / review / publish)
```

| 엔진 | 담당 단계 | 비용 |
|---|---|---|
| local | ingest, proxy, scenes, quality, keyframes, stt(whisper.cpp), silence, palette, bundle, snap, moodboard, edl_check, motion_context, motion(헤드리스 크롬) | 무료 |
| antigravity | analyze (장면 이해, 장소/하이라이트 태깅) | 구독 |
| claude | design (디자인 가이드), edl (스토리 구성), motion_spec (모션 배치·문구) | 구독 |
| manual | AI 단계의 비상구: 프롬프트를 파일로 내보내고 응답 JSON 을 받아 이어감 | 0원 |

단계 사이는 프로젝트 폴더의 **파일(JSON)** 로만 주고받습니다. 그래서 `pipeline.yaml` 에서 `engine:` 한 줄만 바꾸면 담당 AI 를 교체할 수 있고,
중간에 멈춰도(구독 한도, 오류, 승인 대기) 다음 `run` 에서 그 단계부터 이어갑니다.

## 설치 (iMac)

```bash
git clone <이 저장소> && cd travel-vedit
bash setup/install_mac.sh          # brew: ffmpeg, whisper-cpp, exiftool, python@3.12 / venv / whisper 모델 / 크롬(Playwright) / Pretendard
source .venv/bin/activate
python -m vedit doctor             # 모든 항목 ✅ 확인
```

추가로 직접 할 일:
1. **Claude Code** 설치 후 `claude` 를 한 번 실행해 로그인.
2. **Antigravity** 설치·로그인 후 `agy --help` 로 비대화(헤드리스) 실행 방법을 확인해 `config/engines.yaml` 의 `antigravity.cmd` 를 맞춤.
   확인 전까지는 analyze 단계가 자동으로 manual 모드로 넘어갑니다.
3. 외장 SSD 권장: `projects/` 를 외장 SSD 로 옮기고 심볼릭 링크 (install 스크립트 마지막 안내 참고).
4. `BENCH=1 bash setup/install_mac.sh` 로 whisper 속도를 한 번 측정해 두면 STT 소요 시간을 가늠할 수 있습니다.

## 사용법

```bash
# 1) 프로젝트 생성 (원본 폴더 여러 개면 -s 반복)
python -m vedit init kyoto_2026 -s "/Volumes/SSD/교토/iphone" -s "/Volumes/SSD/교토/drone" \
  --title "교토 단풍 3박4일" --timezone Asia/Tokyo --duration 720

# 2) 실행 — 로컬 단계는 오래 걸리니 밤에 걸어두기 좋음
python -m vedit run kyoto_2026
python -m vedit status kyoto_2026

# 3) 디자인 가이드 확인 → 승인
open projects/kyoto_2026/design/moodboard.html
python -m vedit approve kyoto_2026 moodboard
python -m vedit run kyoto_2026

# 4) EDL 확인(edl.json, edl_report.json) → 승인
python -m vedit approve kyoto_2026 edl_check

# 다시 하고 싶을 때: 해당 단계부터 초기화
python -m vedit reset kyoto_2026 design
# 특정 단계만 강제 재실행
python -m vedit run kyoto_2026 --only quality
```

### 수동 모드 (구독 한도 소진 / CLI 미설치 시)
AI 단계가 실패하면 `projects/<이름>/manual/<단계>.prompt.md` 가 생성됩니다.
내용을 웹 채팅(Claude 또는 Gemini)에 붙여넣고(analyze 는 keyframes 이미지도 첨부),
받은 JSON 을 `manual/<단계>.response.json` 으로 저장한 뒤 `run` 하면 스키마 검증 후 이어갑니다.

## 프로젝트 폴더 산출물

| 파일 | 내용 |
|---|---|
| `media.json` | 클립 목록: 촬영 시각(여행지 시간대), GPS, 해상도, 날짜별 그룹 |
| `proxy/`, `audio/` | 720p 프록시, 16kHz WAV |
| `scenes.json`, `keyframes/` | 장면 분할(긴 컷은 15초 단위), 장면별 대표 프레임 |
| `quality.json` | 장면별 흐림/어두움/과노출/흔들림 플래그 |
| `transcript.json`, `silence.json` | 음성 인식 결과, 무음 구간 |
| `palette.json` | 원본 영상 대표 색 6개 |
| `analysis_input.json` | AI 에 넘기는 압축 묶음 |
| `analysis.json` → `analysis_snapped.json` | AI 분석 결과 → 장면/발화 경계로 타임코드 보정 |
| `design/design_guide.json`, `design/moodboard.html` | 이번 영상 디자인 가이드 + 승인용 미리보기 |
| `edl.json`, `edl_report.json` | 편집 결정 목록 + 검증 결과 |
| `motion_context.json`, `motion_spec.json` | 모션 배치용 타임라인 요약 → AI 가 정한 모션 배치·문구 |
| `motion/*.webm`, `motion/*.png`, `motion/manifest.json` | 투명 모션 영상, 승인용 정지 이미지, 목록 |
| `logs/` | 실행 로그, AI 호출 원문 |

## 모션 그래픽

장소 하단 자막, DAY 전환 카드, 오프닝 타이틀 3종이 기본 제공됩니다. 템플릿은 HTML 파일 하나이고,
색·폰트는 디자인 가이드에서 자동으로 들어갑니다. 자세한 사용법과 **템플릿 작성 규약(새 템플릿을 AI 로 만들 때 포함)** 은 [motion/README.md](motion/README.md).

```bash
python -m vedit motion list
python -m vedit motion preview place_lower_third -p title=후시미이나리     # 브라우저 실시간 미리보기
python -m vedit motion render opening_title --over 클립.mp4 --at 2.0       # 영상 위 합성 미리보기
```

## 설정 파일

- `config/local.yaml` — 프록시 해상도, 장면 감지 민감도, 화질 임계값, whisper 모델/스레드 등
- `config/engines.yaml` — AI CLI 명령어 (CLI 가 바뀌면 여기만 수정)
- `config/pipeline.yaml` — 단계 순서/엔진/승인 여부 (프로젝트 생성 시 복사됨)
- `brand/brand_guide.json` — **채널 고정 브랜드 가이드** (폰트, 로고, 인트로/아웃트로, 허용 LUT 등). 처음에 직접 채워 두세요.
- `prompts/`, `schemas/` — AI 지시문과 출력 형식

## 처음 2~3편에서 할 튜닝
- `quality.json` 을 보며 `config/local.yaml` 의 `blurry_below`, `shaky_above` 조정
- 한국어 위주면 `whisper.language: ko`
- `prompts/*.md` 를 결과에 맞춰 다듬기 (프롬프트가 곧 편집 스타일)

## 테스트
```bash
python -m pytest -q     # 합성 영상으로 로컬 단계·엔진 재시도·수동 모드·승인 흐름 검증
```
