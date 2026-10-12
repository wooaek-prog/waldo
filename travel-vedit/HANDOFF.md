# 로컬 이어받기 안내 (iMac 2019)

클라우드 세션에서 만든 범위와, iMac 에서 확인·이어서 할 일을 정리한 문서입니다.
로컬 Claude Code 를 열면 이 파일부터 읽게 하세요 (CLAUDE.md 에도 안내되어 있음).

## 1. 지금까지 만든 것 (클라우드 리눅스에서 테스트 17개 통과)

| 영역 | 상태 | 검증 방법 |
|---|---|---|
| 파이프라인 틀 (단계 실행·재개·승인·수동 모드) | ✅ | 단위 테스트 |
| 로컬 전처리 (ingest, proxy, scenes, quality, keyframes, silence, palette, bundle) | ✅ | 합성 영상 |
| 음성 인식 stt (whisper.cpp) | ⚠️ 코드만 | **클라우드에 whisper 미설치 → 로컬에서 첫 검증** |
| AI 단계 (analyze, design, edl, motion_spec, publish) | ⚠️ 형식만 | 가짜 CLI·수동 응답으로 형식/재시도만 검증. **실제 Claude/Antigravity 호출은 로컬에서 첫 검증** |
| 타임코드 보정 snap, EDL 검증 | ✅ | 단위 테스트 |
| 모션 그래픽 (런타임, 템플릿 3종, 렌더러, 병렬 렌더, 캐시) | ✅ | 헤드리스 크롬 렌더·합성 테스트 |
| 최종 렌더 (세그먼트 캐시, 세로 영상 흐림 채움, LUT·보정, 챕터 페이드, 모션 합성, 자막 번인, BGM 사이드체인 덕킹, 2-pass -14 LUFS) | ✅ | 합성 영상 종단 테스트, 전체 파이프라인 1회 완주 |
| HDR(아이폰 HLG/PQ) → SDR 톤매핑 | ✅ (합성 HLG 영상) | **실제 아이폰 영상 색감은 로컬에서 확인** |
| 유튜브 문구 (챕터, 제목·설명란·태그 묶음) | ✅ | 단위 테스트 |

## 2. 로컬에서 할 일 — 순서대로

### ① 설치 확인 (30분)
```bash
git clone <저장소> && cd waldo && git checkout claude/ecstatic-einstein-5680ta && cd travel-vedit
bash setup/install_mac.sh
source .venv/bin/activate
python -m vedit doctor          # ❌ 가 있으면 그 출력 그대로 Claude 에게
python -m pytest -q             # 맥에서도 17개 통과하는지
```
확인 포인트: `h264_videotoolbox` 인코더 ✅, `zscale`/`subtitles` 필터 ✅, Chromium ✅, whisper 모델 ✅.

### ② AI CLI 연결 (30분)
1. `claude` 한 번 실행해 로그인 → `echo '{"a":1} 를 그대로 출력' | claude -p --output-format json` 이 동작하는지.
2. Antigravity 설치·로그인 → `agy --help` 로 **비대화(헤드리스) 실행 방법**을 찾아 `config/engines.yaml` 의 `antigravity.cmd` 수정.
   - 프롬프트를 표준입력으로 받는지(`prompt_via: stdin`) 인자로 받는지(`arg`), 출력이 JSON 래퍼인지(`result_key`) 확인.
   - 영상 분석 단계는 `keyframes/*.jpg` 와 `proxy/*.mp4` 를 **에이전트가 직접 열어 봐야** 함 → 파일 읽기 권한이 필요한지 확인.
3. 안 되면 해당 단계는 자동으로 수동 모드(`manual/<단계>.prompt.md`)로 넘어가므로 막히지는 않음.

### ③ 작은 실제 여행 영상으로 첫 완주 (반나절)
```bash
python -m vedit init test1 -s "<영상 5~10개 폴더>" --title "테스트" --timezone Asia/Tokyo --duration 120
mkdir -p projects/test1/music && cp <저작권 확인된 음악> projects/test1/music/
caffeinate -i python -m vedit run test1      # 승인 지점마다 멈춤 → 확인 후 approve → run
```
기록해서 알려 줄 것:
- 단계별 걸린 시간 (`projects/test1/logs/run.log` 에 시각 기록) — 특히 proxy, stt, motion, render
- `quality.json` 판정이 틀린 장면 (흔들림/흐림 오탐·미탐)
- 아이폰 HDR 영상의 색감 (`output/preview.mp4` 를 원본과 비교) → `config/local.yaml` 의 `hdr.npl`, `hdr.tonemap` 조정
- AI 응답 품질 (analysis / design_guide / edl / motion_spec 이 이상하면 해당 JSON 과 함께)
- 세로 영상, 슬로모션(고프레임) 영상, 드론·액션캠 영상이 있으면 그 결과

### ④ 사파리 미리보기 확인 (10분)
```bash
python -m vedit motion preview place_lower_third
python -m vedit motion preview opening_title
```
크롬 렌더(`motion stills`)와 사파리 미리보기가 같게 보이는지 → 이후 Tauri 앱(사파리 엔진) 화면 개발의 전제.

## 3. 알려진 가정·한계 (실사용하며 판단)

- `dissolve`/`whip` 챕터 전환은 각각 짧은 검정 페이드/컷으로 대체 (진짜 크로스디졸브는 미구현).
- 속도를 바꾼 컷(타임랩스·이동 구간)은 원본 소리 제거.
- 자막은 `audio: keep`(말하는 장면) 컷만. 외국어 현장음 자막/번역은 미구현.
- BGM 은 폴더의 첫 곡 하나를 반복. 챕터별 곡 전환(`music_cues`)은 미구현.
- 유튜브 챕터는 10초 미만 구간을 앞 챕터에 합침 (3개 미만이면 경고).
- 4K 최종 렌더는 i5 에서 오래 걸릴 수 있음 → 밤 배치(`caffeinate -i`) 전제. 하드웨어 인코더 비트레이트는 `render.bitrate_4k`.
- 고프레임(60/120fps) 원본은 목표 fps(기본 30)로 변환됨. 슬로모션 활용은 EDL `speed` < 1 로 가능하나 프롬프트에 아직 안내 없음.

## 4. 다음 개발 순서 (로컬에서)

1. **실제 영상 튜닝** — 위 ③ 결과로 임계값·프롬프트 조정 (가장 중요)
2. `review` 단계 — 미리보기 렌더를 Antigravity 가 보고 문제(어색한 컷, 잘린 말, 반복 장면) 지적 → EDL 수정 루프
3. `assets` 단계 — 디자인 가이드의 image_prompt 로 썸네일·타이틀 배경 생성 (Antigravity 이미지 모델) + 썸네일 합성(실제 프레임 + 큰 글자)
4. 로컬 서버 API (Python, 127.0.0.1 + 토큰) — 프로젝트/단계 실행/로그 스트림/승인/모션 미리보기
5. 웹 화면 (Vite) — 진행판, 승인 화면(무드보드·EDL 타임라인·모션 정지 이미지·미리보기 영상), 모션 스튜디오
6. Tauri 2 껍데기 — 서버 자동 실행, 폴더 끌어놓기, 알림, 앱 안 터미널(Claude Code 대화형)

## 5. 로컬 Claude Code 시작 프롬프트 예시

```
travel-vedit 의 HANDOFF.md 와 CLAUDE.md 를 읽고, 2장 ①번(설치 확인)부터 같이 진행하자.
doctor 와 pytest 결과를 보고 맥 환경에서 실패하는 것이 있으면 원인을 찾아 고쳐 줘.
```
