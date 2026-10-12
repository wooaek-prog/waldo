"""pipeline.yaml 순서대로 단계를 실행한다. 끝난 단계는 건너뛰고, 중단되면 다음 실행 때 이어간다."""

from __future__ import annotations

from vedit.engines import EngineError, WaitingManual, run_engine
from vedit.media import ToolMissing
from vedit.project import Project

DONE = "done"
NEEDS_APPROVAL = "needs_approval"


def stage_ids(project: Project) -> list[str]:
    return [s["id"] for s in project.pipeline() if s.get("enabled", True)]


def downstream(project: Project, stage: str) -> list[str]:
    ids = stage_ids(project)
    if stage not in ids:
        raise KeyError(f"없는 단계: {stage} (가능: {', '.join(ids)})")
    return ids[ids.index(stage):]


def reset(project: Project, stage: str) -> list[str]:
    """해당 단계와 그 이후 단계를 미완료로 되돌린다."""
    targets = downstream(project, stage)
    project.clear_state(targets)
    return targets


def approve(project: Project, stage: str) -> None:
    status = project.state().get(stage, {}).get("status")
    if status != NEEDS_APPROVAL:
        raise RuntimeError(f"'{stage}' 는 승인 대기 상태가 아닙니다 (현재: {status})")
    project.set_state(stage, DONE, approved=True)


def run(project: Project, only: list[str] | None = None) -> bool:
    """True = 모든 단계 완료, False = 승인/수동응답/오류로 멈춤."""
    for stage in project.pipeline():
        sid = stage["id"]
        if not stage.get("enabled", True) or (only and sid not in only):
            continue
        status = project.state().get(sid, {}).get("status")
        if status == DONE and not only:
            continue
        if status == NEEDS_APPROVAL:
            project.log(f"⏸  '{sid}' 승인 대기. 확인 후: vedit approve {project.name} {sid}")
            return False

        engines = [stage.get("engine", "local")] + ([stage["fallback"]] if stage.get("fallback") else [])
        project.log(f"▶  {sid} ({' → '.join(engines)})")
        last_error: Exception | None = None
        for engine in engines:
            try:
                run_engine(project, stage, engine)
                last_error = None
                break
            except WaitingManual as e:
                project.set_state(sid, "waiting_manual")
                project.log(f"⏸  {e}")
                return False
            except (EngineError, ToolMissing, RuntimeError) as e:
                last_error = e
                project.log(f"✗  {sid} [{engine}] 실패: {e}")
        if last_error is not None:
            project.set_state(sid, "failed", error=str(last_error)[:1000])
            return False

        if stage.get("approve"):
            project.set_state(sid, NEEDS_APPROVAL, engine=engine)
            project.log(f"⏸  '{sid}' 완료 → 결과 확인 후: vedit approve {project.name} {sid}")
            return False
        project.set_state(sid, DONE, engine=engine)
        project.log(f"✓  {sid}")
    return True
