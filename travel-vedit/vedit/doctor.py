"""설치 상태 점검: python -m vedit doctor"""

from __future__ import annotations

import importlib
import platform
import shutil
import sys

from vedit.project import CONFIG_DIR, PROJECTS_DIR, ROOT, load_yaml

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


def _line(mark: str, name: str, detail: str = "") -> None:
    print(f" {mark} {name:<28} {detail}")


def run_doctor() -> bool:
    ok = True
    print("== 시스템 ==")
    mac = platform.mac_ver()[0]
    _line(OK, "OS", f"macOS {mac} ({platform.machine()})" if mac else platform.platform())
    py_ok = sys.version_info >= (3, 10)
    ok &= py_ok
    _line(OK if py_ok else FAIL, "Python", sys.version.split()[0] + ("" if py_ok else " (3.10 이상 필요)"))
    du = shutil.disk_usage(PROJECTS_DIR if PROJECTS_DIR.exists() else ROOT)
    free_gb = du.free / 1e9
    _line(OK if free_gb > 100 else WARN, "projects/ 여유 공간", f"{free_gb:.0f} GB (프록시+렌더용 100GB 이상 권장)")

    print("\n== 파이썬 패키지 ==")
    for mod in ["yaml", "jsonschema", "numpy", "cv2", "scenedetect"]:
        try:
            m = importlib.import_module(mod)
            _line(OK, mod, getattr(m, "__version__", ""))
        except ImportError:
            ok = False
            _line(FAIL, mod, "pip install -r requirements.txt")

    print("\n== 영상 도구 ==")
    if shutil.which("ffmpeg"):
        from vedit.media import has_encoder

        _line(OK, "ffmpeg", shutil.which("ffmpeg"))
        for enc in ["h264_videotoolbox", "hevc_videotoolbox"]:
            _line(OK if has_encoder(enc) else WARN, f"  인코더 {enc}", "" if has_encoder(enc) else "없음 → CPU(libx264) 사용")
    else:
        ok = False
        _line(FAIL, "ffmpeg", "brew install ffmpeg")
    _line(OK if shutil.which("ffprobe") else FAIL, "ffprobe")
    _line(OK if shutil.which("exiftool") else WARN, "exiftool (선택)", "" if shutil.which("exiftool") else "brew install exiftool")

    print("\n== 음성 인식 (whisper.cpp) ==")
    local = load_yaml(CONFIG_DIR / "local.yaml")
    wbin = local["whisper"]["bin"]
    found = shutil.which(wbin)
    _line(OK if found else FAIL, wbin, found or "brew install whisper-cpp")
    model = ROOT / local["whisper"]["model"]
    _line(OK if model.exists() else FAIL, "모델 파일", str(model) if model.exists() else f"{model} 없음 → setup/install_mac.sh")
    ok &= bool(found) and model.exists()

    print("\n== AI CLI (구독) ==")
    engines = load_yaml(CONFIG_DIR / "engines.yaml")
    for name in ["claude", "antigravity"]:
        exe = engines.get(name, {}).get("cmd", [name])[0]
        path = shutil.which(exe)
        _line(OK if path else WARN, f"{name} ({exe})", path or "미설치 → 해당 단계는 manual 대체 가능")

    print("\n결과:", "기본 준비 완료" if ok else "❌ 항목을 해결하세요")
    return ok
