"""단계 실행 엔진.

- local     : vedit.stages 의 파이썬 함수 실행 (ffmpeg, OpenCV, whisper.cpp 등)
- claude    : Claude Code CLI 를 헤드리스로 호출 (구독 사용)
- antigravity: Antigravity CLI(agy) 를 헤드리스로 호출 (구독 사용)
- manual    : 프롬프트를 파일로 내보내고, 사람이 웹 채팅 등에서 받은 JSON 을 넣어 이어감 (비용 0)

AI 엔진은 'JSON 만 받아서 스키마 검증 → 실패 시 오류를 알려주고 재시도' 만 한다.
파일 수정이나 명령 실행은 항상 이 프로그램이 한다.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import jsonschema

from vedit.project import ROOT, Project, read_json, write_json

TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".md", ".txt", ".srt"}
MAX_INLINE_CHARS = 120_000


class EngineError(RuntimeError):
    pass


class WaitingManual(RuntimeError):
    """수동 모드: 응답 파일을 기다리는 중."""


# ---------------------------------------------------------------- 공통 유틸
def extract_json(text: str):
    """모델 출력에서 JSON 본문만 꺼낸다 (```json 블록 우선, 없으면 첫 { ~ 마지막 })."""
    fenced = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    if fenced:
        return json.loads(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("출력에서 JSON 을 찾지 못했습니다")
    return json.loads(text[start : end + 1])


def schema_errors(data, schema: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    ][:20]


def build_prompt(project: Project, stage: dict) -> tuple[str, dict | None]:
    template = (ROOT / stage["prompt"]).read_text(encoding="utf-8")
    ctx = {
        "title": project.config.get("title", project.name),
        "request": project.config.get("request", ""),
        "target_duration_sec": project.config.get("target", {}).get("duration_sec", 600),
        "project_dir": str(project.dir),
    }
    for key, val in ctx.items():
        template = template.replace("{{" + key + "}}", str(val))

    parts = [template, "\n\n---\n# 입력 자료"]
    for rel in stage.get("in", []):
        p = project.resolve_input(rel)
        if p.is_dir():
            files = sorted(x for x in p.rglob("*") if x.is_file())
            listing = "\n".join(f"- {f}" for f in files[:400])
            parts.append(f"\n## {rel} (폴더, 필요하면 파일을 직접 열어 보세요)\n{listing}")
        elif p.suffix.lower() in TEXT_SUFFIXES and p.exists():
            body = p.read_text(encoding="utf-8")
            if len(body) > MAX_INLINE_CHARS:
                body = body[:MAX_INLINE_CHARS] + "\n...(이하 생략: 파일을 직접 열어 확인)"
            parts.append(f"\n## {rel}\n```\n{body}\n```")
        else:
            parts.append(f"\n## {rel}\n파일 경로: {p}")

    schema = None
    if stage.get("schema"):
        schema = read_json(ROOT / stage["schema"])
        parts.append(
            "\n---\n# 출력 형식 (반드시 지킬 것)\n"
            "아래 JSON Schema 를 만족하는 JSON 하나만 출력하세요. 설명 문장, 마크다운, 파일 수정 금지.\n"
            f"```json\n{json.dumps(schema, ensure_ascii=False, indent=1)}\n```"
        )
    return "\n".join(parts), schema


def _save_output(project: Project, stage: dict, data) -> None:
    write_json(project.path(stage["out"]), data)


# ---------------------------------------------------------------- CLI 엔진
def run_cli(project: Project, stage: dict, engine_name: str) -> None:
    cfg = project.engines.get(engine_name)
    if not cfg:
        raise EngineError(f"config/engines.yaml 에 '{engine_name}' 설정이 없습니다")
    prompt, schema = build_prompt(project, stage)
    retries = int(cfg.get("retries", 3))
    logs = project.path("logs")
    logs.mkdir(exist_ok=True)

    for attempt in range(1, retries + 1):
        cmd = [str(c) for c in cfg["cmd"]]
        stdin = None
        if cfg.get("prompt_via", "stdin") == "stdin":
            stdin = prompt
        else:
            cmd = cmd + [prompt]
        project.log(f"  [{engine_name}] 호출 {attempt}/{retries}: {shlex.join(cfg['cmd'])}")
        try:
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True,
                timeout=int(cfg.get("timeout", 900)), cwd=project.dir,
            )
        except FileNotFoundError as e:
            raise EngineError(f"'{cmd[0]}' 실행 파일이 없습니다 ({e})") from e
        except subprocess.TimeoutExpired as e:
            raise EngineError(f"{engine_name} 시간 초과") from e

        (logs / f"{stage['id']}.{engine_name}.{attempt}.log").write_text(
            f"$ {shlex.join(cmd[:len(cfg['cmd'])])}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
            encoding="utf-8",
        )
        if proc.returncode != 0:
            raise EngineError(f"{engine_name} 종료코드 {proc.returncode}: {proc.stderr.strip()[-500:]}")

        text = proc.stdout
        if cfg.get("result_key"):  # 예: claude --output-format json → {"result": "..."}
            try:
                text = json.loads(text)[cfg["result_key"]]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        try:
            data = extract_json(text)
            errors = schema_errors(data, schema) if schema else []
        except (ValueError, json.JSONDecodeError) as e:
            errors = [f"JSON 파싱 실패: {e}"]
        if not errors:
            _save_output(project, stage, data)
            return
        project.log(f"  출력 검증 실패: {errors[:3]}")
        prompt += (
            "\n\n---\n# 이전 출력의 오류 (수정해서 JSON 전체를 다시 출력)\n" + "\n".join(f"- {e}" for e in errors)
        )
    raise EngineError(f"{engine_name}: {retries}회 시도 모두 스키마 검증 실패 (logs/ 확인)")


# ---------------------------------------------------------------- 수동 엔진
def run_manual(project: Project, stage: dict) -> None:
    mdir = project.path("manual")
    mdir.mkdir(exist_ok=True)
    prompt_file = mdir / f"{stage['id']}.prompt.md"
    response_file = mdir / f"{stage['id']}.response.json"

    if response_file.exists():
        _, schema = build_prompt(project, stage)
        raw = response_file.read_text(encoding="utf-8")
        try:
            data = extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            raise EngineError(f"{response_file.name} JSON 파싱 실패: {e}") from e
        errors = schema_errors(data, schema) if schema else []
        if errors:
            raise EngineError(f"{response_file.name} 스키마 오류: {errors[:5]}")
        _save_output(project, stage, data)
        return

    prompt, _ = build_prompt(project, stage)
    prompt_file.write_text(prompt, encoding="utf-8")
    raise WaitingManual(
        f"수동 모드: {prompt_file} 내용을 AI 채팅에 붙여넣고 (이미지는 함께 첨부),\n"
        f"  받은 JSON 을 {response_file} 로 저장한 뒤 다시 run 하세요."
    )


# ---------------------------------------------------------------- 로컬 엔진
def run_local(project: Project, stage: dict) -> None:
    from vedit.stages import REGISTRY

    fn = REGISTRY.get(stage.get("run", stage["id"]))
    if fn is None:
        raise EngineError(f"로컬 단계 '{stage.get('run', stage['id'])}' 가 없습니다")
    fn(project, stage)


def run_engine(project: Project, stage: dict, engine: str) -> None:
    if engine == "local":
        run_local(project, stage)
    elif engine == "manual":
        run_manual(project, stage)
    else:
        run_cli(project, stage, engine)


def output_exists(project: Project, stage: dict) -> bool:
    out = stage.get("out")
    return bool(out) and Path(project.path(out)).exists()
