"""EDL 검증 (edl.json → edl_report.json). 오류가 있으면 실패시켜 렌더 단계로 넘어가지 않게 한다."""

from __future__ import annotations

from vedit.project import Project, read_json, write_json


def run(project: Project, stage: dict) -> None:
    edl = read_json(project.path("edl.json"))
    clips = {c["id"]: c for c in project.media()["clips"]}
    target = project.config.get("target", {}).get("duration_sec", 600)
    chapters = {ch["id"] for ch in edl.get("chapters", [])}

    errors, warnings, total = [], [], 0.0
    for i, item in enumerate(edl["timeline"]):
        where = f"timeline[{i}] {item.get('clip')}"
        clip = clips.get(item["clip"])
        if clip is None:
            errors.append(f"{where}: 없는 클립 ID")
            continue
        if item["in"] >= item["out"]:
            errors.append(f"{where}: in({item['in']}) >= out({item['out']})")
        if item["out"] > clip["duration"] + 0.05:
            errors.append(f"{where}: out({item['out']}) 이 클립 길이({clip['duration']}) 초과")
        if item.get("chapter") and item["chapter"] not in chapters:
            warnings.append(f"{where}: chapters 에 없는 챕터 '{item['chapter']}'")
        if item["out"] - item["in"] < 0.8:
            warnings.append(f"{where}: 0.8초 미만의 매우 짧은 컷")
        total += max(0.0, item["out"] - item["in"])

    if total < target * 0.8 or total > target * 1.2:
        warnings.append(f"총 길이 {total:.0f}초 — 목표 {target}초의 ±20% 범위 밖")

    write_json(project.path("edl_report.json"), {
        "total_sec": round(total, 1), "target_sec": target, "cuts": len(edl["timeline"]),
        "errors": errors, "warnings": warnings,
    })
    for w in warnings:
        project.log(f"  ⚠ {w}")
    if errors:
        raise RuntimeError(f"EDL 오류 {len(errors)}건 (edl_report.json 확인): {errors[:3]}")
    project.log(f"  EDL OK: 컷 {len(edl['timeline'])}개, 총 {total / 60:.1f}분")
