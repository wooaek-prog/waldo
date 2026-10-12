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

    _add_motion_parser(sub)

    args = ap.parse_args(argv)

    if args.cmd == "doctor":
        from vedit.doctor import run_doctor
        return 0 if run_doctor() else 1

    if args.cmd == "motion":
        return _motion(args)

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


# ---------------------------------------------------------------- vedit motion …
def _add_motion_parser(sub) -> None:
    m = sub.add_parser("motion", help="모션 그래픽 템플릿 (목록/점검/미리보기/렌더)")
    msub = m.add_subparsers(dest="motion_cmd", required=True)

    def common(p):
        p.add_argument("template", help="템플릿 id (motion/templates/<id>.html) 또는 .html 경로")
        p.add_argument("--param", "-p", action="append", default=[], metavar="KEY=VALUE",
                       help="파라미터 지정 (여러 번). 예: -p title=기요미즈데라")
        p.add_argument("--project", help="이 프로젝트의 design/design_guide.json 색·폰트를 적용")

    msub.add_parser("list", help="템플릿 목록과 파라미터")
    common(p := msub.add_parser("check", help="템플릿 규약·글자 넘침·안전 영역 점검"))
    common(p := msub.add_parser("preview", help="브라우저에서 실시간 미리보기 열기"))
    p.add_argument("--no-open", action="store_true", help="주소만 출력")
    common(p := msub.add_parser("stills", help="정지 이미지 저장 (기본: 정지 구간 1장)"))
    p.add_argument("--times", type=float, nargs="+", help="캡처 시각(초)")
    p.add_argument("--out-dir", default="motion_out", help="저장 폴더")
    p.add_argument("--scale", type=float, default=0.5)
    common(p := msub.add_parser("render", help="투명 배경 영상(.mov)으로 렌더"))
    p.add_argument("--out", "-o", help="출력 파일 (기본: motion_out/<template>.webm, 확장자는 코덱에 맞춰짐)")
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--scale", type=float, default=1.0, help="1=1080p, 2=4K")
    p.add_argument("--codec", choices=["vp9", "prores4444", "png"], help="기본 vp9. 편집 프로그램용은 prores4444")
    p.add_argument("--over", help="이 영상 위에 합성한 미리보기 mp4 도 만듦")
    p.add_argument("--at", type=float, default=1.0, help="--over 영상에서 모션이 시작할 시각(초)")


def _parse_params(items: list[str]) -> dict:
    import json

    params = {}
    for item in items:
        key, sep, val = item.partition("=")
        if not sep:
            raise SystemExit(f"--param 형식 오류: {item} (KEY=VALUE)")
        try:
            params[key] = json.loads(val) if val[:1] in "[{0123456789-" and val not in ("-",) else val
        except json.JSONDecodeError:
            params[key] = val
    return params


def _motion(args) -> int:
    import json
    import subprocess
    import sys
    from pathlib import Path

    from vedit import motion

    if args.motion_cmd == "list":
        for t in motion.list_templates():
            print(f"\n■ {t['id']}  — {t['name']} ({t['duration']}초)\n  {t.get('description', '')}")
            for k, s in t["params"].items():
                src = f"  ← {s['from']}" if s.get("from") else ""
                print(f"    {k:<14} {s.get('type', ''):<7} {s.get('label', '')}  [기본: {s.get('default')}]{src}")
        return 0

    params = _parse_params(args.param)
    guide = None
    if args.project:
        from vedit.project import read_json

        gpath = Project(args.project).path("design/design_guide.json")
        guide = read_json(gpath) if gpath.exists() else None
        if guide is None:
            print(f"(참고) {gpath} 가 없어 기본 색·폰트를 사용합니다")

    if args.motion_cmd == "check":
        r = motion.check(args.template, params, guide)
        for e in r["errors"]:
            print(" ❌", e)
        for w in r["warnings"]:
            print(" ⚠️ ", w)
        print(f" {'✅ 통과' if not r['errors'] else '❌ 오류'}: {r['template']} ({r['duration']}초, 정지 구간 {r['hold']}초)")
        return 1 if r["errors"] else 0

    if args.motion_cmd == "preview":
        url = motion.preview_url(args.template, params or None)
        print(url)
        if not args.no_open and sys.platform == "darwin":
            subprocess.run(["open", url])
        return 0

    if args.motion_cmd == "stills":
        for p in motion.stills(args.template, Path(args.out_dir), args.times, params, args.scale, guide):
            print(p)
        return 0

    if args.motion_cmd == "render":
        name = Path(args.template).stem
        out = Path(args.out or f"motion_out/{name}{motion.output_suffix(args.codec)}")
        r = motion.render(args.template, out, params, fps=args.fps, scale=args.scale, design_guide=guide,
                          codec=args.codec)
        out = Path(r["path"])
        for w in r["warnings"]:
            print(" ⚠️ ", w)
        print(json.dumps({k: r[k] for k in ("path", "frames", "duration", "seconds_taken")}, ensure_ascii=False))
        if args.over:
            preview = out.with_name(out.stem + "_preview.mp4")
            motion.composite(Path(args.over), [(out, args.at)], preview)
            print(preview)
        return 0
    return 1
