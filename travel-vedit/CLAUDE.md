# travel-vedit — Claude Code 작업 규칙

여행 유튜브 영상 자동편집 파이프라인 (개인용, iMac 2019 Intel / macOS).
구조와 사용법은 README.md 참고.

## 두 가지 사용 방식
1. **파이프라인 엔진으로 호출될 때** (`claude -p` 로 프롬프트가 들어옴):
   요청된 JSON 하나만 출력한다. 파일 생성/수정, 명령 실행 금지.
2. **대화형 편집실로 쓸 때** (사용자가 직접 Claude Code 를 열어 요청):
   - 편집 수정 요청("2챕터 30초 줄여줘")은 `projects/<이름>/edl.json` 을 고친 뒤
     `python -m vedit run <이름> --only edl_check` 로 검증한다.
   - 디자인 수정은 `design/design_guide.json` 을 고친 뒤 `--only moodboard`.
   - 모션 문구/배치 수정은 `motion_spec.json` 을 고친 뒤 `--only motion` (바뀐 것만 다시 렌더).
   - **새 모션 템플릿**은 `motion/README.md` 의 작성 규약을 따르고, `python -m vedit motion check <id>` 경고 0 +
     `motion stills` 로 확인한 뒤 사용자에게 승인받는다.
   - 코드 수정 후에는 `python -m pytest -q` 를 돌린다.

## 절대 규칙
- `source_dirs` 의 **원본 영상은 읽기만** 한다. 이동/삭제/덮어쓰기 금지.
- 모든 산출물은 `projects/<이름>/` 안에만 쓴다.
- JSON 산출물은 `schemas/` 의 스키마를 만족해야 한다.
- EDL 의 in/out 은 `analysis_snapped.json` 구간 안에서만, 클립 길이를 넘지 않게.
- 단계 간 데이터는 파일로만 주고받는다 (새 단계를 추가할 때도 동일).

## 코드 구조
- `vedit/pipeline.py` 단계 실행/재개/승인, `vedit/engines.py` local·claude·antigravity·manual 엔진
- `vedit/stages/` 로컬 단계 (새 단계는 `stages/__init__.py` REGISTRY 에 등록)
- `vedit/motion.py` 모션 렌더러 (Playwright 프레임 캡처 → ffmpeg), `motion/` 템플릿과 공통 런타임
- 설정: `config/local.yaml`, `config/engines.yaml`, `config/pipeline.yaml`
