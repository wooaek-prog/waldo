#!/usr/bin/env python3
"""여의도 시범아파트 일조평가 모델링 배치도 → 동별 매스 외곽선(EPSG:5186).

모델링 배치도(13.png)는 단지를 주황 한 덩어리로 칠하고 건물만 흰색·빨강으로
남겨 둔 깨끗한 판이다. 그래서 '구역 안인데 주황이 아닌 화소'가 곧 건물이다.
이 스크립트는 그 마스크를 뽑아 실좌표로 옮기고, 동번호·층수 판독표
(`data/sibeom_model_dongs.json`)를 붙여 `data/sibeom_site_plan.json` 의
`buildings` 를 채운다.

좌표등록은 도면 세 장을 사슬로 엮었다. 모델링 배치도에는 치수선이 없어서
자기 힘으로는 축척을 못 잡기 때문이다.

    13.png  →  15.webp  →  7.png  →  EPSG:5186
    (모델링)    (배치도)    (배치도 page scan)

  13→15  건물 외곽선을 15.webp 선화에 맞춘다(등방 닮음, 일치도 0.699).
  15→7   같은 도면이라 짙은 선화끼리 맞춘다(90° 회전 + 축척, 일치도 0.632).
  7→실좌표  `data/sibeom_site_plan.json` 의 georeference (기존 확정분).

등방 닮음으로 0.699 가 나온다는 것 자체가 검증이다. 모델링 배치도가 늘어나
있었다면 이 점수가 안 나온다. 결과 축척 0.9049 m/화소로 주황 영역을 재면
107,370㎡ 로 인가 정비구역면적 109,307.80㎡ 와 1.2% 차이다.

    python3 sibeom_model_plan.py --model 13.png --plan15 15.webp --plan7 7.png
    python3 sibeom_model_plan.py --model 13.png --write   # site_plan.json 갱신
"""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

DEFAULT_PLAN = Path(__file__).with_name("data") / "sibeom_site_plan.json"
DEFAULT_DONGS = Path(__file__).with_name("data") / "sibeom_model_dongs.json"
DEFAULT_OUTDIR = Path(__file__).with_name("outputs") / "sibeom"

# 모델링 배치도 대표색. 주황이 단지, tan 은 주황과 검은 선 사이의 경계 화소다.
PALETTE: dict[str, tuple[int, int, int]] = {
    "orange": (255, 204, 156),   # 단지(정비구역) 바탕
    "white": (255, 255, 255),
    "blue": (105, 105, 255),
    "grey": (237, 237, 237),
    "black": (0, 0, 0),
    "red": (253, 1, 2),
    "pink": (252, 211, 207),
    "yellow": (255, 242, 0),      # 여의도유치원(존치)
    "darkred": (160, 0, 0),
    "tan": (200, 170, 140),       # 외곽선 번짐
}
SITE_FILL = ("orange", "tan")
MIN_BLOCK_PX = 25       # 이보다 작은 성분은 주기 글자 번짐으로 본다
SNAP_PX = 2.0           # 판독표 at_px 와 성분 중심의 허용 거리. at_px 는 중심을
                        # 반올림한 값이라 0.71화소 안에 들어온다. 가장 가까운
                        # 두 성분이 6화소 떨어져 있어 2.0 이면 섞이지 않는다.

# 사슬 정합 계수. 짙은 선화를 서로 맞춰 얻은 값이다.
FIT_13_TO_15 = (1.5700, -58.00, -29.00)     # 축척, 평행이동 x, y
FIT_15_TO_7 = (0.7200, 123.45, 15.25)       # 15.webp 를 90° 회전한 뒤
FIT_14_TO_13 = (0.9375, -6.38, 6.00)        # 주기 판 → 주기 없는 판 (일치도 0.587)
PLAN15_WIDTH = 853

# 감층 지시 타원. 주기 판(14.png)에서 빨간 점선 타원은 반투명 분홍으로 안을
# 칠하므로 밑에 깔린 색이 물든다. 그 색조를 잡으면 타원 안팎이 정확히 갈린다.
TINT = ((255, 166, 124), (252, 211, 207))   # 물든 주황, 물든 흰색
TINT_TOL = 16
MIN_ELLIPSE_PX = 400
IN_ELLIPSE_FRAC = 0.5       # 매스의 이만큼이 타원 안이면 '안'으로 본다


# ── 화소 연산 (scipy 없이) ────────────────────────────────────────────────
def dilate(m: np.ndarray, k: int = 1) -> np.ndarray:
    out = m.copy()
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def erode(m: np.ndarray, k: int = 1) -> np.ndarray:
    out = m.copy()
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            out &= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def fill_holes(m: np.ndarray) -> np.ndarray:
    """테두리에서 못 닿는 빈 화소를 메운다."""
    h, w = m.shape
    outside = np.zeros_like(m)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not m[y, x] and not outside[y, x]:
                outside[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if not m[y, x] and not outside[y, x]:
                outside[y, x] = True
                q.append((y, x))
    while q:
        cy, cx = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < h and 0 <= nx < w and not m[ny, nx] and not outside[ny, nx]:
                outside[ny, nx] = True
                q.append((ny, nx))
    return ~outside


def components(m: np.ndarray) -> list[np.ndarray]:
    h, w = m.shape
    seen = np.zeros_like(m)
    out: list[np.ndarray] = []
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        pix = []
        while q:
            cy, cx = q.popleft()
            pix.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if (0 <= ny < h and 0 <= nx < w and m[ny, nx]
                            and not seen[ny, nx]):
                        seen[ny, nx] = True
                        q.append((ny, nx))
        out.append(np.array(pix))
    out.sort(key=len, reverse=True)
    return out


def outline(mask: np.ndarray) -> list[tuple[int, int]]:
    """마스크 화소 사각형 합집합의 바깥 경계를 한 바퀴 잇는다."""
    h, w = mask.shape
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for y, x in zip(*np.nonzero(mask)):
        if y == 0 or not mask[y - 1, x]:
            edges.setdefault((x, y), []).append((x + 1, y))
        if y == h - 1 or not mask[y + 1, x]:
            edges.setdefault((x + 1, y + 1), []).append((x, y + 1))
        if x == 0 or not mask[y, x - 1]:
            edges.setdefault((x, y + 1), []).append((x, y))
        if x == w - 1 or not mask[y, x + 1]:
            edges.setdefault((x + 1, y), []).append((x + 1, y + 1))
    rings = []
    while edges:
        start = next(iter(edges))
        ring, cur = [start], start
        while True:
            nxt = edges[cur].pop()
            if not edges[cur]:
                del edges[cur]
            if nxt == start:
                break
            ring.append(nxt)
            cur = nxt
            if cur not in edges:
                break
        rings.append(ring)
    rings.sort(key=len, reverse=True)
    return rings[0]


def simplify(ring: Sequence[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas–Peucker. 계단 모양 화소 경계를 곧게 편다."""
    pts = list(ring)
    if len(pts) < 4:
        return pts

    def dp(a: int, b: int) -> list[int]:
        if b <= a + 1:
            return [a]
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1.0
        far, fd = a, -1.0
        for i in range(a + 1, b):
            x, y = pts[i]
            d = abs(dy * (x - x1) - dx * (y - y1)) / n
            if d > fd:
                far, fd = i, d
        if fd <= tol:
            return [a]
        return dp(a, far) + dp(far, b)

    half = len(pts) // 2
    return [pts[i] for i in dp(0, half) + dp(half, len(pts) - 1)] + [pts[-1]]


# ── 좌표등록 사슬 ─────────────────────────────────────────────────────────
class ModelTransform:
    """모델링 배치도 화소 → EPSG:5186."""

    def __init__(self, plan: dict[str, Any],
                 f13: Sequence[float] = FIT_13_TO_15,
                 f15: Sequence[float] = FIT_15_TO_7,
                 plan15_width: int = PLAN15_WIDTH) -> None:
        from sibeom_site_polygons import Transform
        self.t7 = Transform(plan["georeference"])
        self.s13, self.tx13, self.ty13 = f13
        self.s15, self.tx15, self.ty15 = f15
        self.w15 = plan15_width

    def __call__(self, px: float, py: float) -> tuple[float, float]:
        a = px * self.s13 + self.tx13            # 13.png → 15.webp
        b = py * self.s13 + self.ty13
        rx, ry = b, (self.w15 - 1) - a           # 15.webp 를 90° 회전
        return self.t7(rx * self.s15 + self.tx15, ry * self.s15 + self.ty15)

    def ring(self, pts) -> list[tuple[float, float]]:
        return [self(x, y) for x, y in pts]

    @property
    def scale_m_per_px(self) -> float:
        a, b = self(0.0, 0.0), self(1.0, 0.0)
        return math.hypot(b[0] - a[0], b[1] - a[1])

    @property
    def up_azimuth_deg(self) -> float:
        a, b = self(0.0, 0.0), self(1.0, 0.0)
        return math.degrees(math.atan2(-(b[1] - a[1]), b[0] - a[0])) % 360.0


def classify(im: Image.Image) -> tuple[np.ndarray, list[str]]:
    a = np.array(im.convert("RGB")).astype(int)
    names = list(PALETTE)
    ref = np.array([PALETTE[k] for k in names])
    d = ((a[:, :, None, :] - ref[None, None, :, :]) ** 2).sum(3)
    return d.argmin(2), names


def extract(im: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """(정비구역 마스크, 건물 마스크)."""
    idx, names = classify(im)
    orange = idx == names.index("orange")
    district = fill_holes(erode(dilate(orange, 2), 2))
    big = max(components(district), key=len)
    m = np.zeros_like(district)
    m[big[:, 0], big[:, 1]] = True
    district = fill_holes(m)

    fill = np.isin(idx, [names.index(n) for n in SITE_FILL])
    raw = erode(district, 2) & ~fill
    keep = np.zeros_like(raw)
    for c in components(raw):
        if len(c) >= MIN_BLOCK_PX:
            keep[c[:, 0], c[:, 1]] = True
    return district, fill_holes(keep)


def ellipses(notes: Path) -> list[Any]:
    """주기 판에서 감층 지시 타원을 찾아 폴리곤으로 돌려준다(14.png 화소).

    타원은 볼록하므로 물든 화소 덩어리의 볼록껍질을 그대로 쓴다. 타원 안의
    빨간 매스는 물들지 않아 구멍으로 남는데(빨강 위에는 분홍이 안 보인다),
    그 구멍이 타원 테두리에 닿으면 구멍 메우기로는 못 채운다 — 볼록껍질은
    그 문제를 통째로 피한다.
    """
    from shapely.geometry import MultiPoint
    a = np.array(Image.open(notes).convert("RGB")).astype(int)
    tint = np.zeros(a.shape[:2], bool)
    for c in TINT:
        tint |= np.abs(a - np.array(c)).max(2) <= TINT_TOL
    m = erode(dilate(tint, 3), 3)
    out = []
    for c in components(m):
        if len(c) >= MIN_ELLIPSE_PX:
            out.append(MultiPoint([(int(x), int(y))
                                   for y, x in c]).convex_hull)
    return out


def mark_ellipse(rows: list[dict[str, Any]], notes: Path) -> int:
    """매스마다 감층 타원 안인지 표시하고, 타원 개수를 돌려준다."""
    from shapely.geometry import Point
    ells = ellipses(notes)
    s, tx, ty = FIT_14_TO_13
    for r in rows:
        # 외곽선 화소가 아니라 링 꼭짓점으로 판정한다. 매스가 작아도 꼭짓점은
        # 늘 여러 개라 과반 판정이 안정적이다.
        pts = [Point((x - tx) / s, (y - ty) / s) for x, y in r["ring_px"]]
        r["in_ellipse"] = any(
            sum(1 for p in pts if e.contains(p)) >= len(pts) * IN_ELLIPSE_FRAC
            for e in ells)
    return len(ells)


def apply_adjust(rows: list[dict[str, Any]], adj: dict[str, Any]) -> None:
    """감층 지시를 층수에 반영한다. 지시와 도면이 어긋나면 중단한다."""
    by_dong = adj.get("by_dong", {})
    no_adjust = set(adj.get("no_adjust", []))
    seen: dict[str, int] = {}
    for r in rows:
        r["floors_drawn"] = r["floors"]
        if not r.get("in_ellipse"):
            continue
        if r["dong"] in no_adjust:
            continue
        if r["dong"] not in by_dong:
            raise SystemExit(
                f"{r['dong']} 매스가 감층 타원 안인데 _floor_adjust 에 없다. "
                "by_dong 에 감층 층수를 적거나 no_adjust 에 넣어야 한다.")
        d = int(by_dong[r["dong"]])
        r["floors"] = r["floors"] + d
        if "labels" in r:
            r["labels"] = [n + d for n in r["labels"]]
        seen[r["dong"]] = seen.get(r["dong"], 0) + 1
    miss = sorted(set(by_dong) - set(seen))
    if miss:
        raise SystemExit(f"_floor_adjust 에 있으나 타원 안 매스가 없는 동: {miss}")


def snap(blocks: list[dict[str, Any]], table: Sequence[dict[str, Any]],
         ) -> list[dict[str, Any]]:
    """판독표의 at_px 에 성분을 붙인다. 애매하면 즉시 중단한다."""
    out = []
    used: set[int] = set()
    for row in table:
        ax, ay = row["at_px"]
        near = [i for i, b in enumerate(blocks)
                if math.hypot(b["cx"] - ax, b["cy"] - ay) <= SNAP_PX]
        if len(near) != 1:
            raise SystemExit(
                f"판독표 {row['dong']} {row['floors']}F at_px={row['at_px']} 에 "
                f"붙는 성분이 {len(near)}개다(1개여야 한다). 추출 파라미터나 "
                f"판독표를 맞춰야 한다.")
        i = near[0]
        if i in used:
            raise SystemExit(f"성분 {i} 가 판독표 두 줄에 걸렸다: {row['dong']}")
        used.add(i)
        out.append({**row, **blocks[i]})
    if len(used) != len(blocks):
        miss = [blocks[i] for i in range(len(blocks)) if i not in used]
        raise SystemExit(
            "판독표에 없는 성분 %d개: %s"
            % (len(miss), [(round(b["cx"]), round(b["cy"])) for b in miss]))
    return out


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True,
                   help="일조평가 모델링 배치도(주기 없는 판, 13.png)")
    p.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    p.add_argument("--dongs", type=Path, default=DEFAULT_DONGS)
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument("--simplify-px", type=float, default=0.9,
                   help="외곽선 단순화 허용오차(화소). 0.9 ≈ 0.8m")
    p.add_argument("--write", action="store_true",
                   help="sibeom_site_plan.json 의 buildings 를 갱신한다")
    p.add_argument("--check", type=Path, default=None,
                   help="배치도(15.webp). 주면 그 위에 추출 외곽선을 얹은 "
                        "검수도를 낸다 — 정합이 맞는지 눈으로 볼 수 있다")
    p.add_argument("--notes", type=Path, default=None,
                   help="주기 판(14.png). 주면 감층 지시 타원을 검출해 "
                        "해당 매스의 층수를 내린다")
    return p.parse_args(argv)


def check_image(rows: list[dict[str, Any]], plan15: Path, out: Path,
                zoom: int = 2) -> None:
    """배치도 위에 추출 외곽선과 동번호를 얹는다."""
    from PIL import ImageDraw
    s, tx, ty = FIT_13_TO_15
    base = Image.open(plan15).convert("RGB")
    base = base.resize((base.width * zoom, base.height * zoom), Image.LANCZOS)
    d = ImageDraw.Draw(base)
    seen: set[str] = set()
    for r in rows:
        pts = [((x * s + tx) * zoom, (y * s + ty) * zoom) for x, y in r["ring_px"]]
        d.line(pts + [pts[0]], fill=(0, 170, 0), width=2)
        if r["dong"] not in seen:
            seen.add(r["dong"])
            d.text(((r["cx"] * s + tx) * zoom - 12, (r["cy"] * s + ty) * zoom - 5),
                   r["dong"], fill=(200, 0, 0))
    base.save(out)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    dongs = json.loads(args.dongs.read_text(encoding="utf-8"))
    im = Image.open(args.model)
    T = ModelTransform(plan)

    district, bld = extract(im)
    blocks = []
    for c in components(bld):
        m = np.zeros_like(bld)
        m[c[:, 0], c[:, 1]] = True
        ring = simplify(outline(fill_holes(m)), args.simplify_px)
        blocks.append({"ring_px": [[int(x), int(y)] for x, y in ring],
                       "cx": float(c[:, 1].mean()), "cy": float(c[:, 0].mean())})
    rows = snap(blocks, dongs["blocks"])

    adj = dongs.get("_floor_adjust", {})
    if args.notes:
        n_ell = mark_ellipse(rows, args.notes)
        apply_adjust(rows, adj)
        cut = [r for r in rows if r["floors"] != r["floors_drawn"]]
        print(f"   감층 타원 {n_ell}개 검출 · 층수를 내린 매스 {len(cut)}개")
        for d in sorted({r["dong"] for r in cut}):
            rr = [r for r in cut if r["dong"] == d]
            print(f"      {d} {adj['by_dong'][d]:+d}F  "
                  + " · ".join(f"{r['floors_drawn']}F→{r['floors']}F" for r in rr))
    elif adj.get("applied"):
        raise SystemExit(
            "_floor_adjust.applied 가 켜져 있는데 --notes 를 안 줬다. "
            "감층 지시를 반영하려면 주기 판(14.png)이 있어야 한다.")

    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    site = Polygon(T.ring(simplify(outline(district), 1.2))).buffer(0)
    polys = {}
    for r in rows:
        r["poly"] = Polygon(T.ring(r["ring_px"])).buffer(0)
        polys.setdefault(r["dong"], []).append(r["poly"])
    total = unary_union([r["poly"] for r in rows])

    ap = plan["approval"]
    print("■ 시범아파트 일조평가 모델링 배치도 – 동별 매스")
    print(f"   축척 {T.scale_m_per_px:.4f} m/화소 · 도면 위쪽 방위 "
          f"{T.up_azimuth_deg:.1f}°")
    print(f"   주황(단지) 영역 {site.area:,.0f} ㎡ "
          f"(인가 정비구역 {ap['district_area_m2']:,.2f} ㎡, 오차 "
          f"{abs(site.area - ap['district_area_m2']) / ap['district_area_m2'] * 100:.1f}%)")
    print(f"   매스 {len(rows)}개 / 동 {len(polys)}개 · 수평투영면적 합계 "
          f"{total.area:,.0f} ㎡")
    print(f"   인가 건축면적 {ap['building_area_m2']:,.2f} ㎡ 대비 "
          f"{total.area / ap['building_area_m2'] * 100:.0f}%"
          "  ← 모델링 배치도는 주동 매스만 그린다")

    if args.check:
        args.outdir.mkdir(parents=True, exist_ok=True)
        out = args.outdir / "모델링배치도_검수도.png"
        check_image(rows, args.check, out)
        print(f"   검수도 → {out}")

    if args.write:
        plan["buildings"] = [
            {"dong": r["dong"], "floors": r["floors"], "ring_px": r["ring_px"],
             **({"floors_drawn": r["floors_drawn"]}
                if r.get("floors_drawn", r["floors"]) != r["floors"] else {}),
             **({"labels": r["labels"]} if "labels" in r else {}),
             **({"merged": r["merged"]} if "merged" in r else {})}
            for r in rows]
        plan["model_georeference"] = {
            "source": "일조평가 모델링 배치도(13.png, 588x349)",
            "scale_m_per_px": round(T.scale_m_per_px, 5),
            "up_azimuth_deg": round(T.up_azimuth_deg, 2),
            "chain": "13.png →(0.699) 15.webp →(0.632) 7.png → EPSG:5186",
            "fit_13_to_15": list(FIT_13_TO_15),
            "fit_15_to_7": list(FIT_15_TO_7),
            "plan15_width_px": PLAN15_WIDTH,
            "tool": "sibeom_model_plan.py",
        }
        plan.pop("_buildings_todo", None)
        args.plan.write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"   → {args.plan} 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
