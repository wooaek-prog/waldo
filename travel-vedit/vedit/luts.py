"""기본 색보정 LUT(.cube) 생성기.

brand_guide.json 의 available_luts 이름과 같은 파일을 luts/ 에 만든다. 외부 LUT 를 쓰려면 같은 폴더에
.cube 파일을 넣고 brand_guide.json 의 available_luts 에 이름을 추가하면 된다 (직접 넣은 파일은 덮어쓰지 않음).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from vedit.project import ROOT

LUT_DIR = ROOT / "luts"
SIZE = 33


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def _saturate(rgb: np.ndarray, amount: float) -> np.ndarray:
    y = _luma(rgb)[..., None]
    return y + (rgb - y) * amount


def _s_curve(x: np.ndarray, strength: float) -> np.ndarray:
    """부드러운 S 커브 (strength 0 = 변화 없음)."""
    return x + strength * (x - 0.5) * (1 - np.abs(2 * x - 1))


def neutral(rgb):
    return rgb


def warm_film(rgb):
    # 따뜻한 필름톤: 하이라이트 살짝 노랗게, 섀도 살짝 들어 올림(필름 페이드), 채도 약간 낮춤
    out = _s_curve(rgb, 0.12)
    out = out * 0.94 + 0.04
    out = _saturate(out, 0.9)
    y = _luma(out)[..., None]
    out = out + np.array([0.035, 0.012, -0.03]) * y
    return out


def cool_city(rgb):
    # 도시 야경/청량: 섀도 청록, 하이라이트 중립, 대비 업
    out = _s_curve(rgb, 0.2)
    y = _luma(out)[..., None]
    out = out + np.array([-0.025, 0.005, 0.035]) * (1 - y)
    return _saturate(out, 0.95)


def vivid_nature(rgb):
    # 자연 풍경: 채도·대비 업, 녹색/하늘색 살짝 강조
    out = _s_curve(rgb, 0.15)
    out = _saturate(out, 1.18)
    return out + np.array([-0.005, 0.01, 0.008])


BUILTIN = {
    "neutral_rec709.cube": neutral,
    "warm_film.cube": warm_film,
    "cool_city.cube": cool_city,
    "vivid_nature.cube": vivid_nature,
}


def write_cube(path: Path, fn, size: int = SIZE) -> Path:
    grid = np.linspace(0.0, 1.0, size)
    # .cube 규격: R 이 가장 빠르게 변한다
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    out = np.clip(fn(rgb), 0.0, 1.0)
    lines = [f'TITLE "{path.stem}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def ensure(name: str | None) -> Path | None:
    """LUT 파일 경로를 돌려준다. 기본 제공 이름인데 파일이 없으면 생성한다."""
    if not name:
        return None
    path = LUT_DIR / name
    if path.exists():
        return path
    if name in BUILTIN:
        return write_cube(path, BUILTIN[name])
    raise FileNotFoundError(f"LUT 파일이 없습니다: {path} (기본 제공: {', '.join(BUILTIN)})")
