# travel-vedit — AI 에이전트 공통 규칙 (Antigravity 등)

> Antigravity 가 읽는 규칙 파일 위치/이름은 설치 후 공식 문서로 확인해서 맞출 것.
> 파이프라인은 프롬프트에 규칙을 직접 포함해 보내므로, 이 파일은 대화형으로 쓸 때의 보조 안내입니다.

- 이 저장소는 여행 유튜브 영상 자동편집 파이프라인이다. README.md 참고.
- 파이프라인에서 호출되면 **요청된 JSON 하나만 출력**한다. 파일 생성/수정, 명령 실행 금지.
- 영상 분석 시 `projects/<이름>/keyframes/*.jpg` 이미지와 `proxy/*.mp4` 를 직접 열어 보고 판단한다.
- 원본 영상(`project.yaml` 의 source_dirs)은 절대 수정하지 않는다.
- 타임코드는 초 단위 실수. `analysis_input.json` 의 장면 경계를 기준으로 삼는다.
