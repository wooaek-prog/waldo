"""최종 합성: EDL → 완성본 mp4 (+ 업로드용 자막 .srt, 유튜브 챕터).

render_preview : 프록시(720p)로 빠르게 — 흐름·타이밍·모션 위치 확인용 (몇 분)
render         : 원본으로 목표 해상도 — 밤에 걸어 두는 최종본

1) 컷별 세그먼트 인코딩 — 자르기, 화면비 맞춤(세로 영상은 흐린 배경 채움), 색보정(LUT+보정값),
   속도, 챕터 전환 페이드, 원본 소리(유지/낮춤/제거). 같은 설정이면 캐시 재사용.
2) 세그먼트 이어 붙이기 (재인코딩 없음, 소리는 PCM 으로 이어 붙여 AAC 틈 누적 방지)
3) 마무리 — 모션 합성 + 자막 번인 + BGM(원본 소리가 나오면 자동으로 줄어드는 사이드체인 덕킹)
   + 유튜브 기준 음량(-14 LUFS)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from vedit import luts, motion
from vedit.media import ffprobe, has_encoder, require, tonemap_filter
from vedit.project import ROOT, Project, read_json, write_json

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".aif", ".aiff"}


# ---------------------------------------------------------------- 공통
def _cfg(project: Project) -> dict:
    return project.local.get("render", {}) or {}


def _optional(project: Project, rel: str) -> dict | None:
    p = project.path(rel)
    return read_json(p) if p.exists() else None


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _dims(project: Project, preview: bool) -> tuple[int, int, float]:
    target = project.config.get("target", {})
    w, h = (int(v) for v in str(target.get("resolution", "1920x1080")).lower().split("x"))
    fps = float(target.get("fps", 30))
    if preview:
        ph = int(_cfg(project).get("preview_height", 720))
        if ph < h:
            w, h = _even(w * ph / h), ph
    return w, h, fps


def _run(cmd: list[str], cwd: Path, what: str) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{what} 실패:\n{' '.join(cmd)}\n{proc.stderr[-1500:]}")


def _display_aspect(path: Path, cache: dict) -> float:
    if path not in cache:
        v = next(s for s in ffprobe(path)["streams"] if s.get("codec_type") == "video")
        w, h = v["width"], v["height"]
        rot = 0
        for sd in v.get("side_data_list", []):
            rot = int(sd.get("rotation", rot))
        rot = rot or int(v.get("tags", {}).get("rotate", 0))
        if abs(rot) % 180 == 90:
            w, h = h, w
        sar = v.get("sample_aspect_ratio", "1:1")
        num, _, den = sar.partition(":")
        try:
            sar_f = float(num) / float(den) if float(num) > 0 and float(den) > 0 else 1.0
        except ValueError:
            sar_f = 1.0
        cache[path] = w * sar_f / h
    return cache[path]


def timeline(edl: dict) -> list[dict]:
    """컷마다 완성본 기준 시작 시각과 길이."""
    out, t = [], 0.0
    for i, item in enumerate(edl["timeline"]):
        speed = float(item.get("speed") or 1)
        length = (item["out"] - item["in"]) / speed
        out.append({**item, "index": i, "speed": speed, "start": t, "length": length})
        t += length
    return out


def _video_codec(project: Project, preview: bool, w: int) -> list[str]:
    cfg = _cfg(project)
    use_vt = cfg.get("video_codec", "auto") == "auto" and has_encoder("h264_videotoolbox")
    if preview:
        return ["-c:v", "h264_videotoolbox", "-b:v", "6M"] if use_vt else \
               ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    if use_vt:
        return ["-c:v", "h264_videotoolbox", "-b:v", str(cfg.get("bitrate_4k", "60M") if w >= 3000 else cfg.get("bitrate_hd", "20M"))]
    return ["-c:v", "libx264", "-preset", str(cfg.get("x264_preset", "medium")), "-crf", str(cfg.get("crf", 17))]


# ---------------------------------------------------------------- 1) 세그먼트
def _grade_filters(guide: dict | None, workdir: Path) -> list[str]:
    if not guide:
        return []
    grade = guide.get("grade", {})
    filters = []
    lut = luts.ensure(grade.get("lut"))
    if lut:
        local = workdir / "grade.cube"
        if not local.exists() or local.read_bytes() != lut.read_bytes():
            local.write_bytes(lut.read_bytes())
        filters.append("lut3d=file=grade.cube")  # 작업 폴더 기준 상대 경로 (경로 특수문자 문제 회피)
    sat, con = float(grade.get("saturation", 1)), float(grade.get("contrast", 1))
    if abs(sat - 1) > 1e-3 or abs(con - 1) > 1e-3:
        filters.append(f"eq=saturation={sat:.3f}:contrast={con:.3f}")
    warm = float(grade.get("warmth", 0))
    if abs(warm) > 1e-3:
        filters.append(f"colorbalance=rm={0.06 * warm:.4f}:bm={-0.06 * warm:.4f}:rh={0.03 * warm:.4f}:bh={-0.03 * warm:.4f}")
    return filters


def _chapter_fade(guide: dict | None, cfg: dict) -> tuple[str | None, float]:
    kind = (guide or {}).get("transitions", {}).get("chapter", "fade_black_0.5")
    m = re.match(r"(fade_black|fade_white|dissolve)_([\d.]+)", kind or "")
    if not m:
        return None, 0.0
    color = "white" if m.group(1) == "fade_white" else "black"
    dur = float(m.group(2)) if m.group(1) != "dissolve" else float(m.group(2)) / 2  # 디졸브는 짧은 검정 페이드로 근사
    return color, min(dur, float(cfg.get("max_fade_sec", 1.0)))


def _segment_cmd(src: Path, item: dict, w: int, h: int, fps: float, aspect: float, has_audio: bool,
                 grade: list[str], fades: dict, volume: float, out: Path, vcodec: list[str],
                 tonemap: str | None = None) -> list[str]:
    length = item["length"]
    v = ([tonemap] if tonemap else []) + [f"setpts=(PTS-STARTPTS)/{item['speed']:.4f}", f"fps={fps:g}"]
    if abs(aspect - w / h) > 0.01:
        # 화면비가 다르면(세로 영상 등) 같은 영상을 흐리게 깔고 가운데에 원본 비율로 얹는다
        blur = max(4, h // 27)
        chain = (",".join(v) + f",split=2[bga][fga];"
                 f"[bga]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur={blur}:2,eq=brightness=-0.06[bg];"
                 f"[fga]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        chain = ",".join(v + [f"scale={w}:{h}"])
    post = ["setsar=1", *grade, f"tpad=stop_mode=clone:stop_duration=1", f"trim=0:{length:.4f}", "setpts=PTS-STARTPTS"]
    if fades.get("in"):
        post.append(f"fade=t=in:st=0:d={fades['in'][1]:.3f}:color={fades['in'][0]}")
    if fades.get("out"):
        d = fades["out"][1]
        post.append(f"fade=t=out:st={max(0.0, length - d):.3f}:d={d:.3f}:color={fades['out'][0]}")
    post.append("format=yuv420p")
    vgraph = f"[0:v]{chain}," + ",".join(post) + "[v]"

    use_src_audio = has_audio and volume > 0 and abs(item["speed"] - 1) < 1e-3
    a = (["[0:a]asetpts=PTS-STARTPTS", "aresample=48000", "aformat=channel_layouts=stereo", f"volume={volume:.3f}"]
         if use_src_audio else ["[1:a]anull"])
    if fades.get("in"):
        a.append(f"afade=t=in:st=0:d={fades['in'][1]:.3f}")
    if fades.get("out"):
        a.append(f"afade=t=out:st={max(0.0, length - fades['out'][1]):.3f}:d={fades['out'][1]:.3f}")
    a += ["apad", f"atrim=0:{length:.4f}"]
    agraph = ",".join(a) + "[a]"

    span = item["out"] - item["in"]
    return ["ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-ss", f"{item['in']:.4f}", "-t", f"{span + 0.1:.4f}", "-i", str(src),
            "-f", "lavfi", "-t", f"{length + 0.1:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", vgraph + ";" + agraph, "-map", "[v]", "-map", "[a]",
            *vcodec, "-r", f"{fps:g}", "-c:a", "pcm_s16le", "-t", f"{length:.4f}", "-f", "mov", str(out)]


# ---------------------------------------------------------------- 자막 / 챕터
def _ass_color(hexv: str, alpha: int = 0) -> str:
    hexv = (hexv or "#FFFFFF").lstrip("#")
    r, g, b = hexv[0:2], hexv[2:4], hexv[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts_ass(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ts_srt(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _wrap(text: str, max_chars: int) -> list[str]:
    """글자 수 기준 줄바꿈 (띄어쓰기 우선)."""
    words, lines, cur = text.split(), [], ""
    for wd in words:
        while len(wd) > max_chars:  # 띄어쓰기 없는 긴 덩어리
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(wd[:max_chars])
            wd = wd[max_chars:]
        cand = f"{cur} {wd}".strip()
        if len(cand) <= max_chars:
            cur = cand
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def subtitle_events(tl: list[dict], transcript: dict, max_chars: int) -> list[tuple[float, float, str]]:
    """원본 소리를 살린 컷(talk 등)의 발화를 완성본 시간으로 옮겨 자막 이벤트를 만든다."""
    events = []
    for item in tl:
        if item.get("audio_mode") != "keep" or abs(item["speed"] - 1) > 1e-3:
            continue
        for seg in transcript.get(item["clip"], {}).get("segments", []):
            s, e = max(seg["start"], item["in"]), min(seg["end"], item["out"])
            if e - s < 0.3:
                continue
            t0, t1 = item["start"] + (s - item["in"]), item["start"] + (e - item["in"])
            pieces = _split_even(seg["text"].strip(), max_chars)
            total = sum(len(p) for p in pieces) or 1
            cur = t0
            for piece in pieces:  # 두 줄 넘게 길면 비슷한 길이로 나눠 글자 수 비율만큼 시간 배분
                dur = (t1 - t0) * len(piece) / total
                events.append((cur, cur + dur, "\n".join(_wrap(piece, max_chars))))
                cur += dur
    return events


def _split_even(text: str, max_chars: int) -> list[str]:
    """한 장(최대 두 줄)에 넘치는 문장을 비슷한 길이의 여러 장으로 나눈다 (끝에 짧은 꼬리가 남지 않게)."""
    lines = _wrap(text, max_chars)
    if len(lines) <= 2:
        return [text]
    n = -(-len(lines) // 2)
    target = len(text) / n
    pieces, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > target * 1.15 and len(pieces) < n - 1:
            pieces.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    pieces.append(cur)
    return pieces


def write_ass(path: Path, w: int, h: int, guide: dict | None, events, captions: list[tuple[float, float, str]]) -> None:
    cap = (guide or {}).get("captions", {})
    fonts = (guide or {}).get("fonts", {})
    k = h / 1080
    size = int(cap.get("size", 48) * k)
    outline = max(0, int(round(cap.get("outline", 3) * k)))
    align = {"bottom": 2, "bottom_left": 1, "top": 8}.get(cap.get("position", "bottom"), 2)
    margin_v, margin_lr = int(h * 0.075), int(w * 0.08)
    color, ocolor = _ass_color(cap.get("color", "#FFFFFF")), _ass_color(cap.get("outline_color", "#000000"))
    accent = _ass_color((guide or {}).get("palette", {}).get("accent", "#FFFFFF"))
    body_font = fonts.get("caption", "Pretendard SemiBold")
    title_font = fonts.get("title", "Pretendard Bold")

    def esc(text: str) -> str:
        return text.replace("\\", "＼").replace("{", "(").replace("}", ")").replace("\n", "\\N")

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Sub,{body_font},{size},{color},{color},{ocolor},&H64000000,0,0,0,0,100,100,0,0,1,{outline},"
        f"{max(1, int(2 * k))},{align},{margin_lr},{margin_lr},{margin_v},1",
        f"Style: Caption,{title_font},{int(size * 1.15)},{color},{color},{ocolor},&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{outline},{max(1, int(2 * k))},8,{margin_lr},{margin_lr},{int(h * 0.09)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines += [f"Dialogue: 0,{_ts_ass(s)},{_ts_ass(e)},Sub,,0,0,0,,{esc(t)}" for s, e, t in events]
    lines += [f"Dialogue: 1,{_ts_ass(s)},{_ts_ass(e)},Caption,,0,0,0,,{{\\fad(250,250)\\3c{accent}}}{esc(t)}"
              for s, e, t in captions]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_srt(path: Path, events) -> None:
    blocks = [f"{i}\n{_ts_srt(s)} --> {_ts_srt(e)}\n{t}\n" for i, (s, e, t) in enumerate(events, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def youtube_chapters(tl: list[dict], edl: dict, opening: str = "오프닝") -> tuple[list[tuple[float, str]], list[str]]:
    """유튜브 챕터 규칙: 0:00 시작, 3개 이상, 각 10초 이상."""
    titles = {c["id"]: c["title"] for c in edl.get("chapters", [])}
    entries: list[tuple[float, str]] = [(0.0, opening)]
    seen = set()
    for item in tl:
        ch = item.get("chapter")
        if ch and ch not in seen:
            seen.add(ch)
            entries.append((item["start"], titles.get(ch, ch)))
    ending = next((x["start"] for x in tl if x["role"] == "ending"), None)
    if ending is not None and ending > entries[-1][0]:
        entries.append((ending, "마무리"))
    total = tl[-1]["start"] + tl[-1]["length"] if tl else 0
    merged: list[tuple[float, str]] = []
    for t, title in entries:
        if merged and t - merged[-1][0] < 10:
            if merged[-1] == (0.0, opening):      # 오프닝이 10초도 안 되면 첫 챕터 이름을 0:00 으로
                merged[-1] = (0.0, title)
            continue                              # 그 밖의 10초 미만 챕터는 앞 챕터에 합침
        merged.append((t, title))
    if merged and total - merged[-1][0] < 10 and len(merged) > 1:
        merged.pop()
    warnings = [] if len(merged) >= 3 else [f"유튜브 챕터는 3개 이상이어야 표시됩니다 (현재 {len(merged)}개)"]
    return merged, warnings


def _fmt_chapter_time(t: float) -> str:
    s = int(t)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- 소리
def _mix_audio(workdir: Path, bgm: Path | None, total: float, cfg: dict, lufs: float) -> dict | None:
    """timeline.mov 의 원본 소리 + BGM(사이드체인 덕킹) → audio_mix.wav → 2-pass loudnorm → audio_norm.wav"""
    if bgm:
        fade_out = max(0.0, total - 3)
        graph = ";".join([
            "[0:a]asplit=2[voice][sc]",
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(cfg.get('bgm_volume_db', -16)):g}dB,"
            f"atrim=0:{total:.3f},afade=t=in:d=1.5,afade=t=out:st={fade_out:.3f}:d=3[bgm]",
            "[bgm][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[bgmd]",
            "[voice][bgmd]amix=inputs=2:duration=first:normalize=0[mix]",
        ])
        _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", "timeline.mov", "-stream_loop", "-1", "-i", str(bgm),
              "-filter_complex", graph, "-map", "[mix]", "-t", f"{total:.3f}", "-c:a", "pcm_s24le", "audio_mix.wav"],
             workdir, "BGM 믹스")
    else:
        _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", "timeline.mov", "-vn", "-t", f"{total:.3f}",
              "-c:a", "pcm_s24le", "audio_mix.wav"], workdir, "소리 추출")

    target = f"I={lufs:g}:TP=-1.5:LRA=11"
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", "audio_mix.wav",
                           "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
                          cwd=workdir, capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    measured = json.loads(m.group(0)) if m else None
    if measured and measured.get("input_i") not in ("-inf", "inf"):
        af = (f"loudnorm={target}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
              f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
              f"offset={measured['target_offset']}:linear=true,aresample=48000")
    else:  # 무음 등으로 측정 불가 → 그대로
        measured, af = None, "anull"
    _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", "audio_mix.wav", "-af", af,
          "-c:a", "pcm_s16le", "audio_norm.wav"], workdir, "음량 맞춤")
    return measured


# ---------------------------------------------------------------- 본체
def _bgm(project: Project) -> Path | None:
    for p in project.config.get("bgm") or []:
        if Path(p).expanduser().exists():
            return Path(p).expanduser()
    music = project.path("music")
    files = sorted(p for p in music.glob("*") if p.suffix.lower() in AUDIO_EXT) if music.exists() else []
    return files[0] if files else None


def _render(project: Project, preview: bool) -> None:
    require("ffmpeg", "brew install ffmpeg")
    cfg = _cfg(project)
    mode = "preview" if preview else "final"
    edl = read_json(project.path("edl.json"))
    guide = _optional(project, "design/design_guide.json")
    transcript = _optional(project, "transcript.json") or {}
    manifest = _optional(project, "motion/manifest.json")
    brand = read_json(ROOT / "brand/brand_guide.json") if (ROOT / "brand/brand_guide.json").exists() else {}
    clips = {c["id"]: c for c in project.media()["clips"]}
    w, h, fps = _dims(project, preview)
    workdir = project.path(f"render/{mode}")
    workdir.mkdir(parents=True, exist_ok=True)
    outdir = project.path("output")
    outdir.mkdir(exist_ok=True)
    warnings: list[str] = []

    tl = timeline(edl)
    for item in tl:
        item["audio_mode"] = item.get("audio") or ("keep" if item["role"] == "talk" else "duck")
    total = tl[-1]["start"] + tl[-1]["length"]

    # ---- 1) 세그먼트
    grade = _grade_filters(guide, workdir)
    fade_color, fade_d = _chapter_fade(guide, cfg)
    vcodec = _video_codec(project, preview, w)
    duck = float(cfg.get("duck_volume", 0.35))
    aspects: dict = {}
    seg_files = []
    project.log(f"  [{mode}] {w}x{h} {fps:g}fps, 컷 {len(tl)}개, 총 {total / 60:.1f}분")
    for i, item in enumerate(tl):
        clip = clips.get(item["clip"])
        if clip is None:
            raise RuntimeError(f"EDL 의 클립 {item['clip']} 이 media.json 에 없습니다")
        src = project.path(f"proxy/{clip['id']}.mp4") if preview else Path(clip["path"])
        if not src.exists():
            raise RuntimeError(f"원본/프록시 파일이 없습니다: {src}")
        fades: dict = {}
        if i == 0:
            fades["in"] = ("black", 0.4)
        if i == len(tl) - 1:
            fades["out"] = ("black", 1.0)
        if fade_color and i > 0 and item.get("chapter") and item.get("chapter") != tl[i - 1].get("chapter"):
            fades["in"] = (fade_color, fade_d)
        if fade_color and i < len(tl) - 1 and tl[i + 1].get("chapter") and tl[i + 1].get("chapter") != item.get("chapter"):
            fades["out"] = (fade_color, fade_d)
        volume = {"keep": 1.0, "duck": duck, "mute": 0.0}.get(item["audio_mode"], duck)
        has_audio = clip["has_audio"] and (not preview or project.path(f"proxy/{clip['id']}.mp4").exists())
        tonemap = None if preview else tonemap_filter(clip.get("hdr"), project.local.get("hdr"))  # 프록시는 이미 SDR
        key = hashlib.sha1(json.dumps([str(src), src.stat().st_mtime, item["in"], item["out"], item["speed"], volume,
                                       fades, grade, w, h, fps, vcodec, tonemap,
                                       (workdir / "grade.cube").read_text() if (workdir / "grade.cube").exists() else ""],
                                      sort_keys=True).encode()).hexdigest()[:12]
        seg = workdir / f"seg_{i:04d}_{key}.mov"
        if not seg.exists():
            project.log(f"  컷 {i + 1}/{len(tl)} {item['clip']} {item['in']:.1f}~{item['out']:.1f}s")
            part = seg.with_suffix(".part.mov")
            _run(_segment_cmd(src, item, w, h, fps, _display_aspect(src, aspects), has_audio, grade, fades, volume,
                              part, vcodec, tonemap), workdir, f"컷 {i + 1} 인코딩")
            part.replace(seg)
        seg_files.append(seg)
    for old in workdir.glob("seg_*.mov"):  # 지난 설정의 세그먼트 정리
        if old not in seg_files:
            old.unlink()

    # ---- 2) 이어 붙이기
    (workdir / "concat.txt").write_text("".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8")
    _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
          "-c", "copy", "timeline.mov"], workdir, "이어 붙이기")

    # ---- 3) 자막, 챕터
    max_chars = int(brand.get("fixed", {}).get("captions", {}).get("max_chars_per_line", 18))
    events = subtitle_events(tl, transcript, max_chars)
    captions = [(x["start"] + 0.3, x["start"] + min(x["length"], 4.0), x["caption"]) for x in tl if x.get("caption")]
    write_srt(outdir / "subtitles.srt", events)
    burn = bool(cfg.get("burn_subtitles", True)) and (events or captions)
    if burn:
        write_ass(workdir / "subs.ass", w, h, guide, events, captions)
        fonts_link = workdir / "fonts"
        if not fonts_link.exists():
            os.symlink(motion.MOTION_DIR / "fonts", fonts_link)
    chapters, ch_warn = youtube_chapters(tl, edl)
    warnings += ch_warn
    (outdir / "chapters.txt").write_text(
        "".join(f"{_fmt_chapter_time(t)} {title}\n" for t, title in chapters), encoding="utf-8")

    # ---- 4) 마무리 합성
    inputs = ["-i", "timeline.mov"]
    vchain, prev, n_in = [], "0:v", 1
    used_overlays = []
    for ov in (manifest or {}).get("overlays", []):
        if ov["at_index"] >= len(tl):
            warnings.append(f"모션 {ov['id']}: at_index {ov['at_index']} 가 타임라인 범위를 벗어나 제외")
            continue
        path = project.path(ov["path"])
        if not path.exists():
            warnings.append(f"모션 {ov['id']}: 파일 없음 ({ov['path']}) — motion 단계를 다시 실행하세요")
            continue
        at = tl[ov["at_index"]]["start"] + float(ov.get("offset") or 0)
        if at >= total:
            continue
        inputs += motion.input_args(path)
        vchain.append(f"[{n_in}:v]scale={w}:{h},setpts=PTS-STARTPTS+{at:.3f}/TB[m{n_in}]")
        vchain.append(f"[{prev}][m{n_in}]overlay=eof_action=pass:format=auto[o{n_in}]")
        prev = f"o{n_in}"
        used_overlays.append({"id": ov["id"], "template": ov.get("template"), "at": round(at, 2)})
        n_in += 1
    if burn:
        vchain.append(f"[{prev}]subtitles=subs.ass:fontsdir=fonts[subd]")
        prev = "subd"
    vchain.append(f"[{prev}]format=yuv420p[vout]")

    # 소리는 영상과 따로 처리: 섞기 → 음량 측정 → 정확히 맞추기(2-pass loudnorm). 소리만 다루므로 가볍다.
    bgm = _bgm(project)
    lufs = float(cfg.get("loudness_lufs", brand.get("fixed", {}).get("loudness_lufs", -14)))
    if not bgm:
        warnings.append("BGM 없음: projects/<이름>/music/ 에 음악 파일을 넣거나 project.yaml 의 bgm 에 경로 지정")
    measured = _mix_audio(workdir, bgm, total, cfg, lufs)

    name = "preview.mp4" if preview else f"{project.name}_final.mp4"
    out = outdir / name
    part = workdir / f"{out.stem}.part.mp4"
    abitrate = "160k" if preview else "256k"
    project.log(f"  마무리 합성: 모션 {len(used_overlays)}개, 자막 {len(events)}개, BGM {'있음' if bgm else '없음'}")
    _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *inputs, "-i", "audio_norm.wav",
          "-filter_complex", ";".join(vchain), "-map", "[vout]", "-map", f"{n_in}:a",
          *vcodec, "-r", f"{fps:g}", "-c:a", "aac", "-b:a", abitrate, "-t", f"{total:.3f}",
          "-movflags", "+faststart", str(part)], workdir, "마무리 합성")
    part.replace(out)

    report = {
        "mode": mode, "output": str(out), "resolution": f"{w}x{h}", "fps": fps,
        "duration_sec": round(total, 2), "cuts": len(tl), "overlays": used_overlays,
        "subtitles": len(events), "on_screen_captions": len(captions), "subtitles_burned": bool(burn),
        "bgm": str(bgm) if bgm else None, "loudness_target_lufs": lufs,
        "loudness_before_lufs": float(measured["input_i"]) if measured else None,
        "grade": (guide or {}).get("grade"), "chapters": [{"t": _fmt_chapter_time(t), "title": s} for t, s in chapters],
        "warnings": warnings,
    }
    write_json(outdir / f"render_report_{mode}.json", report)
    for wmsg in warnings:
        project.log(f"  ⚠ {wmsg}")
    project.log(f"  완성: {out} ({out.stat().st_size / 1e6:.1f}MB)")


def run_preview(project: Project, stage: dict) -> None:
    _render(project, preview=True)


def run_final(project: Project, stage: dict) -> None:
    _render(project, preview=False)


def package(project: Project, stage: dict) -> None:
    """유튜브 업로드용 텍스트 묶음 (output/youtube.txt)."""
    meta = read_json(project.path("youtube_meta.json"))
    chapters = project.path("output/chapters.txt").read_text(encoding="utf-8").strip()
    parts = [
        "■ 제목 후보", *[f"{i}. {t}" for i, t in enumerate(meta["titles"], 1)], "",
        "■ 설명란 (아래를 그대로 복사)", meta["description"].strip(), "", chapters, "",
        " ".join(meta.get("hashtags", [])), "",
        "■ 태그", ", ".join(meta.get("tags", [])),
    ]
    if meta.get("pinned_comment"):
        parts += ["", "■ 고정 댓글", meta["pinned_comment"]]
    if meta.get("thumbnail_texts"):
        parts += ["", "■ 썸네일 문구 후보", *meta["thumbnail_texts"]]
    out = project.path("output/youtube.txt")
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    project.log(f"  업로드 문구: {out}")
