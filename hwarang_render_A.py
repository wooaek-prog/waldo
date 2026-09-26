#!/usr/bin/env python3
"""화랑 A안 상세 모형 미리보기 렌더 — Embree 광선추적(투시·입면).

`hwarang_tower_detail.py` 가 낸 `화랑A_상세+주변.obj` 를 읽어 조감도 밑그림용
이미지를 만든다. 햇빛 방향은 **보기 좋게 고른 연출값**이며 일조 분석과는
무관하다(일조 분석은 hwarang_design_2026.py).

  · 광선추적: 1차 광선 + 그림자 + 주변폐색(AO) + 유리 반사(1회)·난간 투과
  · 2×2 초표본 → 계단 현상 제거, 면 법선·깊이 불연속으로 얇은 윤곽선
  · 투시(2점 투시 보정 포함)와 직교 입면

    python3 hwarang_render_A.py                 # 전 시점
    python3 hwarang_render_A.py --views 운동장 --scale 0.5    # 빠른 확인
"""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from embreex import rtcore_scene as rtcs
from embreex.mesh_construction import TriangleMesh
from PIL import Image, ImageDraw, ImageFont

OUT = Path("outputs/hwarang_detail_A_2026")
OBJ = OUT / "화랑A_상세+주변.obj"
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
ORIGIN = (194327.70, 546974.70)

# 재질: sRGB 바탕색, 반사율 F0(유리류), 투과(난간)
ALBEDO = {
    "glass":    (0.40, 0.49, 0.56),
    "slab":     (0.89, 0.85, 0.78),
    "balcony":  (0.92, 0.89, 0.83),
    "soffit":   (0.69, 0.51, 0.35),
    "railing":  (0.70, 0.78, 0.82),
    "fin":      (0.82, 0.74, 0.60),
    "mullion":  (0.74, 0.70, 0.63),
    "frame":    (0.86, 0.80, 0.70),
    "column":   (0.88, 0.86, 0.82),
    "planter":  (0.62, 0.59, 0.54),
    "green":    (0.30, 0.46, 0.24),
    "trunk":    (0.40, 0.30, 0.22),
    "paving":   (0.80, 0.78, 0.74),
    "lawn":     (0.46, 0.60, 0.34),
    "context":  (0.93, 0.93, 0.91),
    "context_new": (0.90, 0.91, 0.93),
    "school":   (0.94, 0.90, 0.87),
    "ground":   (0.80, 0.80, 0.77),
    "field":    (0.45, 0.58, 0.38),
    "track":    (0.66, 0.40, 0.32),
}
GLASS, RAILING = "glass", "railing"
TOWER_MATS = {"glass", "slab", "balcony", "soffit", "railing", "fin", "mullion",
              "frame", "column", "planter", "green", "trunk"}


# --------------------------------------------------------------------------- #
@dataclass
class View:
    key: str
    title: str
    eye: tuple            # 타워 중심 기준 x=동 y=북 z=위(m)
    target: tuple
    size: tuple           # 최종 픽셀(가로, 세로)
    sun: tuple            # (방위°, 고도°) — 연출값
    fov: float = 40.0     # 세로 화각(°) — 2점 투시면 half_tan 사용
    two_point: bool = False
    half_tan: float = 0.5
    ortho: float = 0.0    # >0 이면 직교 — 세로 폭(m)
    fog: float = 2600.0


def views() -> list[View]:
    pg = (194262.0 - ORIGIN[0], 547160.0 - ORIGIN[1])     # 여의도중 운동장
    return [
        View("운동장", "여의도중 운동장에서 본 A안 (눈높이 1.6m, 약 200m)",
             (pg[0], pg[1], 1.6), (0, 0, 100), (1200, 1680), (238, 30),
             two_point=True, half_tan=0.64),
        View("남서조감", "남서 조감 — 서측 픽셀 발코니 입면",
             (-290, -310, 235), (-5, -5, 70), (1800, 1200), (250, 36), fov=32),
        View("남동조감", "남동 조감 — 동측 연속 발코니·핀 입면",
             (300, -290, 225), (5, -5, 70), (1800, 1200), (135, 34), fov=32),
        View("서측광장", "서측 광장 눈높이 — 아케이드 로비와 저층 발코니",
             (-52, -28, 1.6), (0, 0, 30), (1200, 1680), (245, 28),
             two_point=True, half_tan=0.95, fog=6000),
        View("크라운", "크라운 근경 — 50·51층 테라스와 열린 프레임",
             (-92, -78, 196), (0, 0, 150), (1600, 1200), (235, 32), fov=38,
             fog=6000),
        View("북서조감", "북서 조감 — 여의도중·여고 쪽에서",
             (-330, 400, 250), (0, 0, 60), (1800, 1200), (235, 34), fov=34),
    ]


def elevations() -> list[tuple]:
    """(key, 제목, 보는 방향(가로축 단위벡터), 연출 햇빛)."""
    a = math.radians(174.0)
    u = np.array([math.sin(a), math.cos(a), 0.0])        # 장축(남쪽)
    v = np.array([-math.cos(a), math.sin(a), 0.0])       # 동쪽
    # 광선 출발 = 타워 중심면에서 앞쪽으로 off m (외형선 밖, 대지 수목 안쪽)
    return [("서측", "서측 입면", v, u, (230, 35), 18.0),   # 동쪽을 보며(서측면)
            ("북측", "북측(단부) 입면", u, -v, (300, 35), 40.0),
            ("동측", "동측 입면", -v, -u, (120, 35), 18.0)]


# --------------------------------------------------------------------------- #
def load_obj(path: Path):
    V, F, M, names = [], [], [], {}
    cur = -1
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            c = line[:2]
            if c == "v ":
                V.append(line[2:].split())
            elif c == "f ":
                F.append(line[2:].split())
                M.append(cur)
            elif line.startswith("usemtl"):
                nm = line.split()[1]
                cur = names.setdefault(nm, len(names))
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64) - 1
    return V, F, np.asarray(M, dtype=np.int32), {v: k for k, v in names.items()}


def srgb2lin(c):
    return np.power(np.asarray(c, dtype=np.float64), 2.2)


def lin2srgb(c):
    c = np.clip(c, 0, None)
    # 필믹(ACES 근사) 톤매핑
    a, b, cc, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    c = np.clip((c * (a * c + b)) / (c * (cc * c + d) + e), 0, 1)
    return np.power(c, 1 / 2.2)


class Scene:
    def __init__(self, V, F, M, names):
        self.V = V
        tri = V[F]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-9
        self.F, self.M = F[keep], M[keep]
        self.N = n[keep] / ln[keep, None]
        self.names = names
        self.sc = rtcs.EmbreeScene()
        TriangleMesh(scene=self.sc, vertices=V.astype(np.float32),
                     indices=self.F.astype(np.int32))
        nm = len(names)
        self.alb = np.zeros((nm, 3))
        for i, k in names.items():
            self.alb[i] = srgb2lin(ALBEDO.get(k, (0.8, 0.8, 0.8)))
        self.mid = {k: i for i, k in names.items()}
        self.is_tower = np.array([names[i] in TOWER_MATS for i in range(nm)])

    def cast(self, o, d, tmax=None):
        o = np.ascontiguousarray(o, dtype=np.float32)
        d = np.ascontiguousarray(d, dtype=np.float32)
        out = np.full(len(o), -1, dtype=np.int64)
        t = np.full(len(o), np.inf)
        step = 2_000_000
        for s in range(0, len(o), step):
            kw = {}
            if tmax is not None:
                kw["dists"] = np.ascontiguousarray(
                    np.broadcast_to(tmax, (len(o),))[s:s + step], dtype=np.float32)
            r = self.sc.run(o[s:s + step], d[s:s + step], output=1, **kw)
            p = r["primID"].astype(np.int64)
            g = r["geomID"]
            p[g < 0] = -1
            out[s:s + step] = p
            tt = r["tfar"].astype(np.float64)
            tt[p < 0] = np.inf
            t[s:s + step] = tt
        return out, t

    def occluded(self, o, d, tmax=None):
        p, _ = self.cast(o, d, tmax)
        return p >= 0


# --------------------------------------------------------------------------- #
SKY_Z = srgb2lin((0.36, 0.55, 0.82))
SKY_H = srgb2lin((0.80, 0.86, 0.92))
HAZE = srgb2lin((0.84, 0.88, 0.92))


def sky(d):
    h = np.clip(d[:, 2], 0, 1)[:, None]
    c = SKY_H * (1 - np.sqrt(h)) + SKY_Z * np.sqrt(h)
    below = d[:, 2] < 0
    c[below] = HAZE * 0.9
    return c * 1.25


def sun_vec(az, alt):
    a, e = math.radians(az), math.radians(alt)
    return np.array([math.sin(a) * math.cos(e), math.cos(a) * math.cos(e),
                     math.sin(e)])


def hash01(*ks):
    h = np.zeros_like(ks[0], dtype=np.uint64)
    for k in ks:
        h = (h ^ (k.astype(np.int64).astype(np.uint64) + np.uint64(0x9E3779B97F4A7C15)
                  + (h << np.uint64(6)) + (h >> np.uint64(2))))
        h = h * np.uint64(0xBF58476D1CE4E5B9)
        h ^= h >> np.uint64(31)
    return (h % np.uint64(10_000)).astype(np.float64) / 10_000


def shade(S: Scene, o, d, sun, depth=0, fog=2600.0, rng=None, gbuf=None):
    n_ray = len(o)
    col = sky(d)
    prim, t = S.cast(o, d)
    hit = prim >= 0
    if gbuf is not None:
        gbuf["t"] = np.where(hit, t, np.inf)
        gbuf["n"] = np.zeros((n_ray, 3))
        gbuf["m"] = np.full(n_ray, -1)
    if not hit.any():
        return col
    idx = np.nonzero(hit)[0]
    pr = prim[idx]
    dd = d[idx]
    tt = t[idx]
    p = o[idx] + dd * tt[:, None]
    n = S.N[pr].copy()
    flip = (n * dd).sum(1) > 0
    n[flip] *= -1
    m = S.M[pr]
    if gbuf is not None:
        gbuf["n"][idx] = n
        gbuf["m"][idx] = m
    alb = S.alb[m].copy()
    eps = 0.03

    # 직사광 + 그림자
    ndl = np.clip(n @ sun, 0, None)
    lit = ndl > 0
    vis = np.zeros(len(idx))
    if lit.any():
        li = np.nonzero(lit)[0]
        occ = S.occluded(p[li] + n[li] * eps, np.broadcast_to(sun, (len(li), 3)))
        vis[li] = ~occ
    sun_c = srgb2lin((1.0, 0.95, 0.86)) * 3.2
    # 하늘빛(반구) + AO
    amb = (srgb2lin((0.62, 0.72, 0.86)) * (0.55 + 0.45 * n[:, 2:3])
           + srgb2lin((0.55, 0.52, 0.48)) * (0.45 - 0.45 * n[:, 2:3]) * 0.6)
    ao = np.ones(len(idx))
    if depth == 0 and rng is not None:
        k_ao = 4
        occ_sum = np.zeros(len(idx))
        # 법선 기준 접평면
        a = np.where(np.abs(n[:, 2:3]) < 0.9, np.array([[0, 0, 1.0]]),
                     np.array([[1.0, 0, 0]]))
        tu = np.cross(n, a)
        tu /= np.linalg.norm(tu, axis=1)[:, None]
        tv = np.cross(n, tu)
        for _ in range(k_ao):
            r1, r2 = rng.random(len(idx)), rng.random(len(idx))
            r = np.sqrt(r1)
            phi = 2 * np.pi * r2
            dirs = (tu * (r * np.cos(phi))[:, None] + tv * (r * np.sin(phi))[:, None]
                    + n * np.sqrt(1 - r1)[:, None])
            occ_sum += S.occluded(p + n * eps, dirs, tmax=7.0)
        ao = 1 - 0.75 * occ_sum / k_ao
    ambc = alb * amb * 1.1
    c = alb * sun_c * (ndl * vis)[:, None] + ambc * ao[:, None]

    # 유리 — 프레넬 반사 + 실내 톤(세대·층마다 다르게)
    gi = np.nonzero(m == S.mid.get(GLASS, -99))[0]
    if len(gi):
        cosi = np.clip(-(dd[gi] * n[gi]).sum(1), 0, 1)
        F0 = 0.10
        Fr = F0 + (1 - F0) * (1 - cosi) ** 5
        Fr = np.clip(Fr * 1.4, 0, 0.9)
        r = dd[gi] - 2 * (dd[gi] * n[gi]).sum(1)[:, None] * n[gi]
        if depth == 0:
            rc = shade(S, p[gi] + n[gi] * eps, r, sun, depth + 1, fog)
        else:
            rc = sky(r)
        fl = np.floor(p[gi, 2] / 3.3)
        a174 = math.radians(174.0)
        uu = p[gi, 0] * math.sin(a174) + p[gi, 1] * math.cos(a174)
        side = np.sign(p[gi, 0] * -math.cos(a174) + p[gi, 1] * math.sin(a174))
        h = hash01(fl, np.floor(uu / 3.5), side + 2)
        inner = (srgb2lin((0.10, 0.12, 0.13))
                 + srgb2lin((0.30, 0.26, 0.20)) * (h ** 3)[:, None]
                 + srgb2lin((0.12, 0.14, 0.15)) * ((h > 0.55) & (h < 0.7))[:, None])
        inner = inner * (0.6 + 0.8 * ao[gi, None])
        hl = np.clip(r @ sun, 0, 1) ** 200 * 12 * vis[gi]
        c[gi] = inner * (1 - Fr[:, None]) + rc * Fr[:, None] + hl[:, None]

    # 유리 난간 — 뒤를 비춘다
    ri = np.nonzero(m == S.mid.get(RAILING, -99))[0]
    if len(ri) and depth < 2:
        behind = shade(S, p[ri] + dd[ri] * eps, dd[ri], sun, depth + 1, fog)
        tint = srgb2lin((0.78, 0.86, 0.88))
        c[ri] = behind * tint * 0.72 + c[ri] * 0.35

    # 대기 원근
    f = np.exp(-tt / fog)[:, None] if fog else np.ones((len(idx), 1))
    c = c * f + HAZE * 1.2 * (1 - f)
    col[idx] = c
    if gbuf is not None:          # AO 는 나중에 경계 보존 흐림으로 다시 입힌다
        ambc = ambc * f
        ambc[gi] = 0
        ambc[ri] = 0
        gbuf["amb"] = np.zeros((n_ray, 3))
        gbuf["amb"][idx] = ambc
        gbuf["ao"] = np.ones(n_ray)
        gbuf["ao"][idx] = ao
    return col


# --------------------------------------------------------------------------- #
def camera_rays(v: View, W, H):
    eye = np.array(v.eye, dtype=float)
    tgt = np.array(v.target, dtype=float)
    ys, xs = np.mgrid[0:H, 0:W]
    sx = (xs + 0.5) / W * 2 - 1
    sy = 1 - (ys + 0.5) / H * 2
    aspect = W / H
    fwd = tgt - eye
    if v.two_point:
        fh = fwd.copy()
        fh[2] = 0
        dist = np.linalg.norm(fh)
        fh /= dist
        right = np.cross(fh, [0, 0, 1.0])
        up = np.array([0, 0, 1.0])
        yc = (tgt[2] - eye[2]) / dist
        ht = v.half_tan
        d = (fh[None, None] + (sx * ht * aspect)[..., None] * right
             + (yc + sy * ht)[..., None] * up)
    else:
        fwd /= np.linalg.norm(fwd)
        right = np.cross(fwd, [0, 0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        ht = math.tan(math.radians(v.fov / 2))
        d = (fwd[None, None] + (sx * ht * aspect)[..., None] * right
             + (sy * ht)[..., None] * up)
    d = d.reshape(-1, 3)
    d /= np.linalg.norm(d, axis=1)[:, None]
    o = np.broadcast_to(eye, d.shape).copy()
    return o, d


def ortho_rays(look, right, center, width, height, W, H, off=400.0):
    ys, xs = np.mgrid[0:H, 0:W]
    sx = ((xs + 0.5) / W - 0.5) * width
    sy = (0.5 - (ys + 0.5) / H) * height
    up = np.array([0, 0, 1.0])
    o = (center[None, None] - look[None, None] * off
         + sx[..., None] * right + sy[..., None] * up).reshape(-1, 3)
    d = np.broadcast_to(look, o.shape).copy()
    return o, d


def edges(g, W, H):
    t = g["t"].reshape(H, W)
    n = g["n"].reshape(H, W, 3)
    e = np.zeros((H, W), dtype=bool)
    fin = np.isfinite(t)
    for dy, dx in ((0, 1), (1, 0)):
        t2 = np.roll(t, (-dy, -dx), (0, 1))
        n2 = np.roll(n, (-dy, -dx), (0, 1))
        f2 = np.roll(fin, (-dy, -dx), (0, 1))
        both = fin & f2
        with np.errstate(invalid="ignore"):
            jump = both & (np.abs(t - t2) > 0.004 * np.minimum(t, t2) + 0.05)
        crease = both & ((n * n2).sum(2) < 0.85)
        sil = fin ^ f2
        e |= jump | crease | sil
    return e


def blur_ao(g, W, H, rad=4, step=2):
    """AO 잡음 제거 — 법선·깊이가 같은 이웃끼리만 평균(경계 보존)."""
    ao = g["ao"].reshape(H, W)
    t = g["t"].reshape(H, W)
    n = g["n"].reshape(H, W, 3)
    acc = np.zeros((H, W))
    wsum = np.zeros((H, W))
    for dy in range(-rad, rad + 1, step):
        for dx in range(-rad, rad + 1, step):
            a2 = np.roll(ao, (dy, dx), (0, 1))
            t2 = np.roll(t, (dy, dx), (0, 1))
            n2 = np.roll(n, (dy, dx), (0, 1))
            with np.errstate(invalid="ignore"):
                w = (((n * n2).sum(2) > 0.95)
                     & (np.abs(t - t2) < 0.01 * t + 0.1)).astype(float)
            acc += a2 * w
            wsum += w
    return np.where(wsum > 0, acc / np.maximum(wsum, 1e-9), ao)


def render(S: Scene, rays, W, H, sun, fog, ss=2, seed=1, edge_k=0.28):
    rng = np.random.default_rng(seed)
    o, d = rays
    g = {}
    col = shade(S, o, d, sun, 0, fog, rng, g)
    ao_b = blur_ao(g, W, H)
    col += g["amb"] * (ao_b.reshape(-1) - g["ao"])[:, None]
    img = col.reshape(H * 1, W * 1, 3)
    e = edges(g, W, H)
    mid = g["m"].reshape(H, W)
    far = np.clip(g["t"].reshape(H, W) / 900, 0, 1)
    k = edge_k * (1 - 0.7 * far)
    img = img * (1 - (e * k)[..., None])
    rgb = lin2srgb(img)
    if ss > 1:
        rgb = rgb.reshape(H // ss, ss, W // ss, ss, 3).mean((1, 3))
    return (rgb * 255 + 0.5).astype(np.uint8)


def caption(im: Image.Image, title: str, sub: str) -> Image.Image:
    W, H = im.size
    bar = 64
    out = Image.new("RGB", (W, H + bar), (250, 250, 248))
    out.paste(im, (0, 0))
    dr = ImageDraw.Draw(out)
    f1 = ImageFont.truetype(FONT, 24)
    f2 = ImageFont.truetype(FONT, 15)
    dr.text((20, H + 8), title, font=f1, fill=(30, 30, 30))
    dr.text((20, H + 38), sub, font=f2, fill=(110, 110, 110))
    return out


SUB = ("화랑 A안 51층 1개동(172.3m) · 상세 폴리곤 미리보기 — 햇빛은 연출값이며 "
       "일조 분석과 무관 · 주변은 화이트 모델")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--views", nargs="*", default=None)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--ss", type=int, default=2)
    ap.add_argument("--no-elev", action="store_true")
    a = ap.parse_args(argv)

    t0 = time.time()
    V, F, M, names = load_obj(OBJ)
    S = Scene(V, F, M, names)
    print(f"삼각형 {len(S.F):,}개 · 재질 {len(names)} · 읽기 {time.time() - t0:.1f}s")
    outdir = OUT / "렌더"
    outdir.mkdir(exist_ok=True)

    for v in views():
        if a.views and v.key not in a.views:
            continue
        t1 = time.time()
        W = int(v.size[0] * a.scale) * a.ss
        H = int(v.size[1] * a.scale) * a.ss
        rgb = render(S, camera_rays(v, W, H), W, H, sun_vec(*v.sun), v.fog, a.ss)
        im = caption(Image.fromarray(rgb), v.title, SUB)
        im.save(outdir / f"렌더_{v.key}.png", optimize=True)
        Image.fromarray(rgb).save(outdir / f"렌더_{v.key}_원판.png", optimize=True)
        print(f"  {v.key}: {W // a.ss}×{H // a.ss} · {time.time() - t1:.1f}s")

    if not a.no_elev and (not a.views or "입면" in a.views):
        t1 = time.time()
        res = 0.1 / a.scale / a.ss            # m/픽셀
        tiles = []
        # 입면은 타워+대지만(주변 건물이 가리지 않게)
        Vt, Ft, Mt, nt = load_obj(OUT / "화랑A_상세모형.obj")
        St = Scene(Vt, Ft, Mt, nt)
        for key, title, look, right, sun, off in elevations():
            width = 68.0 if key != "북측" else 22.0
            height = 184.0
            W = int(round(width / res))
            H = int(round(height / res))
            W -= W % a.ss
            H -= H % a.ss
            center = np.array([0.0, 0.0, height / 2 - 4])
            rays = ortho_rays(look, right, center, width, height, W, H, off)
            rgb = render(St, rays, W, H, sun_vec(*sun), 0.0, a.ss, edge_k=0.35)
            tiles.append((title, Image.fromarray(rgb)))
        gap, top, bot = 40, 20, 70
        Wt = sum(t.size[0] for _, t in tiles) + gap * (len(tiles) + 1)
        Ht = max(t.size[1] for _, t in tiles) + top + bot
        sheet = Image.new("RGB", (Wt, Ht), (250, 250, 248))
        dr = ImageDraw.Draw(sheet)
        f1 = ImageFont.truetype(FONT, int(22 * a.scale) + 8)
        x = gap
        for title, t in tiles:
            sheet.paste(t, (x, top))
            dr.text((x, top + t.size[1] + 10), title, font=f1, fill=(30, 30, 30))
            x += t.size[0] + gap
        sheet.save(outdir / "렌더_입면.png", optimize=True)
        print(f"  입면: {Wt}×{Ht} · {time.time() - t1:.1f}s")
    print(f"→ {outdir}  ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
