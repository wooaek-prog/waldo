"""프로젝트 폴더, 설정, 단계 상태(state.json) 관리."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
CONFIG_DIR = ROOT / "config"


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    """임시 파일에 쓴 뒤 교체한다. 중간에 끊겨도 깨진 JSON이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


class Project:
    def __init__(self, name: str, projects_dir: Path | None = None):
        self.name = name
        self.dir = (projects_dir or PROJECTS_DIR) / name
        if not (self.dir / "project.yaml").exists():
            raise FileNotFoundError(f"프로젝트가 없습니다: {self.dir} (먼저 `vedit init` 실행)")
        self.config: dict = load_yaml(self.dir / "project.yaml")
        self.local: dict = load_yaml(CONFIG_DIR / "local.yaml")
        self.engines: dict = load_yaml(CONFIG_DIR / "engines.yaml")

    # ---- 생성 ----
    @classmethod
    def init(cls, name: str, sources: list[str], projects_dir: Path | None = None, **extra) -> "Project":
        pdir = (projects_dir or PROJECTS_DIR) / name
        if (pdir / "project.yaml").exists():
            raise FileExistsError(f"이미 존재하는 프로젝트: {pdir}")
        pdir.mkdir(parents=True, exist_ok=True)
        config = {
            "name": name,
            "title": extra.get("title") or name,
            "source_dirs": [str(Path(s).expanduser().resolve()) for s in sources],
            "timezone": extra.get("timezone") or "Asia/Seoul",
            "request": extra.get("request")
            or "자연스럽고 따뜻한 여행 브이로그. 첫 15초 하이라이트 훅, 장소별 챕터, 잔잔한 BGM.",
            "target": {"duration_sec": int(extra.get("duration") or 600), "resolution": "3840x2160", "fps": 30},
        }
        with open(pdir / "project.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
        # 파이프라인은 프로젝트별로 복사해 두고 필요하면 그 프로젝트만 수정한다.
        shutil.copy(CONFIG_DIR / "pipeline.yaml", pdir / "pipeline.yaml")
        return cls(name, projects_dir)

    # ---- 경로 ----
    def path(self, rel: str | Path) -> Path:
        return self.dir / rel

    def resolve_input(self, rel: str) -> Path:
        """입력 경로: 프로젝트 폴더 기준, 없으면 저장소 루트 기준(brand/ 등)."""
        p = self.dir / rel
        return p if p.exists() else ROOT / rel

    # ---- 파이프라인/상태 ----
    def pipeline(self) -> list[dict]:
        return load_yaml(self.dir / "pipeline.yaml")["stages"]

    def state(self) -> dict:
        p = self.dir / "state.json"
        return read_json(p) if p.exists() else {}

    def set_state(self, stage: str, status: str, **info) -> None:
        state = self.state()
        state[stage] = {"status": status, "at": datetime.now().isoformat(timespec="seconds"), **info}
        write_json(self.dir / "state.json", state)

    def clear_state(self, stages: list[str]) -> None:
        state = self.state()
        for s in stages:
            state.pop(s, None)
        write_json(self.dir / "state.json", state)

    def media(self) -> dict:
        return read_json(self.path("media.json"))

    def log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        self.path("logs").mkdir(exist_ok=True)
        with open(self.path("logs/run.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
