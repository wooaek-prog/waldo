"""명령줄 진입점: python -m vedit <command> ..."""

from __future__ import annotations

import argparse

from vedit import pipeline
from vedit.project import Project


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vedit", description="여행 유튜브 영상 자동편집 파이프라인")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="설치/환경 점검")

    p = sub.add_parser("init", help="새 여행 프로젝트 생성")
    p.add_argument("name", help="프로젝트 이름 (영문/숫자, 예: kyoto_2026_10)")
    p.add_argument("--source", "-s", action="append", required=True, help="원본 영상 폴더 (여러 번 지정 가능)")
    p.add_argument("--title", help="영상 가제 (예: 교토 3박4일)")
    p.add_argument("--duration", type=int, help="목표 길이(초), 기본 600")
    p.add_argument("--timezone", help="여행지 시간대 (예: Asia/Tokyo), 기본 Asia/Seoul")
    p.add_argument("--request", help="편집 요청 문장")

    p = sub.add_parser("run", help="파이프라인 실행 (완료된 단계는 건너뜀)")
    p.add_argument("name")
    p.add_argument("--only", nargs="+", help="지정 단계만 강제 실행")

    p = sub.add_parser("status", help="단계별 상태 보기")
    p.add_argument("name")

    p = sub.add_parser("approve", help="승인 대기 단계 승인")
    p.add_argument("name")
    p.add_argument("stage")

    p = sub.add_parser("reset", help="해당 단계부터 다시 실행하도록 초기화")
    p.add_argument("name")
    p.add_argument("stage")

    args = ap.parse_args(argv)

    if args.cmd == "doctor":
        from vedit.doctor import run_doctor
        return 0 if run_doctor() else 1

    if args.cmd == "init":
        proj = Project.init(
            args.name, args.source, title=args.title, duration=args.duration,
            timezone=args.timezone, request=args.request,
        )
        print(f"생성됨: {proj.dir}\n  - project.yaml 에서 요청/목표 길이 확인\n  - 다음: python -m vedit run {args.name}")
        return 0

    proj = Project(args.name)
    if args.cmd == "run":
        return 0 if pipeline.run(proj, only=args.only) else 2
    if args.cmd == "status":
        state = proj.state()
        for stage in proj.pipeline():
            sid = stage["id"]
            info = state.get(sid, {})
            mark = {"done": "✓", "needs_approval": "⏸", "waiting_manual": "✋", "failed": "✗"}.get(
                info.get("status"), "·"
            )
            off = "" if stage.get("enabled", True) else " (비활성)"
            err = f"  ← {info['error'][:80]}" if info.get("error") else ""
            print(f" {mark} {sid:<12} {stage.get('engine', 'local'):<12} {info.get('status', '-')}{off}{err}")
        return 0
    if args.cmd == "approve":
        pipeline.approve(proj, args.stage)
        print(f"승인됨: {args.stage}. 이어서: python -m vedit run {args.name}")
        return 0
    if args.cmd == "reset":
        print("초기화:", ", ".join(pipeline.reset(proj, args.stage)))
        return 0
    return 1
