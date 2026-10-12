"""로컬 단계 등록부. pipeline.yaml 의 engine: local 단계는 id(또는 run:) 이름으로 여기서 찾는다."""

from vedit.stages import analysis_prep, edl_check, ingest, palette, proxy, quality, scenes, snap, stt

REGISTRY = {
    "ingest": ingest.run,
    "proxy": proxy.run,
    "scenes": scenes.run,
    "quality": quality.run,
    "keyframes": scenes.keyframes,
    "stt": stt.run,
    "silence": stt.silence,
    "palette": palette.run,
    "bundle": analysis_prep.run,
    "snap": snap.run,
    "moodboard": palette.moodboard,
    "edl_check": edl_check.run,
}
