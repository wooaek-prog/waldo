#!/usr/bin/env python3
"""화랑 A안 — 배치도(어린이집·성큰광장) + 주요 층 평면도(코어·E/V·PT실·세대).

  배치도       대지 남서부 빈 땅에 어린이집(유치원, 지상 2층)과 놀이마당,
               성큰광장(B1 −5.0m, 계단식 스탠드), 타워 서측 로비 광장,
               남서–북동 산책로, 지하주차장 진출입 램프, 조경·수목.
  평면도       2층      주민공동시설 — 피트니스 · PT실 3종 · GX · 락커
               기준층 Ⅰ  B블록(19–34층) — 4세대: 84 ×2 + 대형 ×2
               기준층 Ⅱ  A·C블록(3–17, 36–49층) — 6세대: 59 ×2 · 84 ×2 · 114 · 135
               18층     스카이가든 + 피난안전구역(초고층 30개층 이내) + 테라스 세대 2

코어 2개(각 E/V 3대: 승용 · 비상용 겸 · 피난용 겸, 특별피난계단 + 부속실,
PS · EPS/TPS · AD). 모든 코어는 3–51층을 관통하도록 위치를 고정했고, 층마다
±0.8m 엇갈리는 블록은 세대 쪽이 흡수한다.

면적은 유리선(연면적선) 기준으로 잰 개략값이다. 세대 면적 ≈ 전용면적(벽체
중심선 기준)으로 본다. 실시설계 전 검토용.

    python3 hwarang_plans_A.py --buildings <AL_D010.gpkg>
    python3 hwarang_plans_A.py --buildings <gpkg> --check-sun   # 어린이집 일조 재확인
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import unary_union

import hwarang_site_A as SA
import hwarang_tower_detail as T
from hwarang_plan_2026 import CHROME

OUT = Path("outputs/hwarang_plans_A_2026")
FONT = "WenQuanYi Zen Hei, Noto Sans CJK KR, sans-serif"
W = T.W_PLATE
HV = W / 2                       # 6.3

# 코어(장축 u) — 안쪽(가운데 세대 쪽) 모서리 ±MID, 폭 CORE_W
MID = 6.67                        # 가운데 세대 폭(84㎡ = 6.67 × 12.6)
CORE_W = 7.4
CORR = (4.2, HV)                  # 동측 공용 복도·전실 띠(v)
NEAR_W = 5.62                     # 59㎡ 세대 폭(깊이 10.5m)

COLORS = {
    "59": "#d5ece4", "84": "#d6e6f5", "114": "#e4dcf2", "135": "#efd9ea",
    "대형": "#f6e3cf", "테라스형": "#f6e3cf", "펜트": "#f2d6c4",
    "주민공동": "#e0efd4", "피난안전구역": "#fbe7c0", "코어": "#c9c9c4",
    "복도": "#efeee9",
}


# --------------------------------------------------------------------------- #
# 기하
# --------------------------------------------------------------------------- #
def core_parts(side: int) -> list[tuple[str, Polygon, str]]:
    """코어 한 개의 실 구성. side=−1 북측 코어(A), +1 남측 코어(B).

    cu = 안쪽(가운데 세대 쪽) 모서리에서 바깥쪽으로 잰 거리."""
    def R(c0, c1, v0, v1):
        u0 = side * (MID + c0)
        u1 = side * (MID + c1)
        return box(min(u0, u1), v0, max(u0, u1), v1)
    return [
        ("특별피난계단", R(0, CORE_W, -HV, -3.0), "stair"),
        ("PS", R(0, 2.1, -3.0, -1.0), "shaft"),
        ("부속실", R(2.1, 5.3, -3.0, -1.0), "lobby"),
        ("EPS·TPS", R(5.3, CORE_W, -3.0, -1.0), "shaft"),
        ("E/V-1\n승용", R(0, 2.4, -1.0, 1.6), "ev"),
        ("E/V-2\n비상용 겸", R(0, 2.4, 1.6, 4.2), "ev"),
        ("승강장", R(2.4, 5.0, -1.0, 4.2), "lobby"),
        ("AD", R(5.0, CORE_W, -1.0, 1.6), "shaft"),
        ("E/V-3\n피난용 겸", R(5.0, CORE_W, 1.6, 4.2), "ev"),
        ("전실·홀", R(0, CORE_W, CORR[0], CORR[1]), "lobby"),
    ]


def core_box(side: int) -> Polygon:
    u0, u1 = side * MID, side * (MID + CORE_W)
    return box(min(u0, u1), -HV, max(u0, u1), HV)


# 실 배치 틀: (이름, x0, x1, y0, y1). x = 출입 쪽(0) → 먼 쪽(1) 비율,
# y = 서측 유리선(0)에서 잰 m. 세대 다각형으로 잘라 쓴다.
ROOMS = {
    "59": [("거실", .42, 1, 0, 4.4), ("침실1", 0, .42, 0, 4.4),
           ("주방", .42, 1, 4.4, 7.2), ("욕실", 0, .42, 4.4, 6.6),
           ("침실2", .45, 1, 7.2, 10.5), ("현관", 0, .30, 8.7, 10.5)],
    "84": [("침실1", 0, .45, 0, 4.8), ("거실", .45, 1, 0, 4.8),
           ("드레스·욕실", 0, .45, 4.8, 7.4), ("주방·식당", .45, 1, 4.8, 8.4),
           ("욕실", 0, .30, 7.4, 10.2), ("침실2", .58, 1, 8.4, 12.6),
           ("알파룸", .30, .58, 9.6, 12.6), ("현관", 0, .28, 10.5, 12.6)],
    "114": [("침실1", 0, .40, 0, 4.8), ("거실", .40, 1, 0, 6.0),
            ("드레스·욕실", 0, .40, 4.8, 7.6), ("주방·식당", .55, 1, 6.0, 9.2),
            ("침실3", .55, 1, 9.2, 12.6), ("침실2", .22, .55, 9.0, 12.6),
            ("욕실", 0, .22, 7.6, 10.5), ("현관", 0, .22, 10.5, 12.6)],
    "135": [("침실1", 0, .36, 0, 5.0), ("거실", .36, 1, 0, 6.2),
            ("드레스·욕실", 0, .36, 5.0, 7.8), ("주방·식당", .52, 1, 6.2, 9.4),
            ("침실3", .60, 1, 9.4, 12.6), ("침실2", .30, .60, 9.2, 12.6),
            ("욕실", .14, .30, 9.2, 12.6), ("현관", 0, .14, 10.5, 12.6),
            ("팬트리", 0, .14, 7.8, 10.5)],
    "대형": [("침실1", 0, .30, 0, 5.2), ("서재", .30, .50, 0, 5.0),
            ("거실", .50, 1, 0, 6.0), ("드레스·욕실", 0, .30, 5.2, 8.4),
            ("주방·식당", .50, 1, 6.0, 9.4), ("침실2", .66, 1, 9.4, 12.6),
            ("침실3", .40, .66, 9.4, 12.6), ("팬트리", .28, .40, 9.4, 12.6),
            ("욕실", .14, .28, 9.6, 12.6), ("현관", 0, .14, 10.5, 12.6)],
}
# 18·35층 테라스형 — 먼 쪽 8m 는 한쪽 절반이 파여 테라스가 된다
_TERR_NEAR = [("침실1", 0, .26, 0, 5.0), ("주방·식당", .26, .49, 0, 5.0),
              ("드레스·욕실", 0, .26, 5.0, 7.6), ("욕실", .26, .49, 6.0, 8.6),
              ("침실2", .26, .49, 8.6, 12.6), ("팬트리", .14, .26, 9.6, 12.6),
              ("현관", 0, .14, 10.5, 12.6)]
ROOMS["테라스형E"] = _TERR_NEAR + [("거실", .49, 1, 0, 6.3)]      # 동측 절반 파임
ROOMS["테라스형W"] = _TERR_NEAR + [("거실", .49, 1, 6.3, 12.6)]   # 서측 절반 파임


@dataclass
class Unit:
    kind: str                  # 59 / 84 / 114 / 135 / 대형 / 테라스형
    poly: Polygon              # uv 유리선 안 세대 영역
    x_from: float              # 출입 쪽 u
    x_to: float                # 먼 쪽 u
    y0: float = -HV            # 서측 기준 v
    orient: str = ""           # 향
    tpl: str = ""              # 실 배치 틀(비우면 kind)
    rooms: list = field(default_factory=list)
    name: str = ""


def lay_rooms(un: Unit) -> None:
    span = un.x_to - un.x_from
    for nm, x0, x1, y0, y1 in ROOMS[un.tpl or un.kind]:
        ua, ub = un.x_from + x0 * span, un.x_from + x1 * span
        r = box(min(ua, ub), un.y0 + y0, max(ua, ub), un.y0 + y1).intersection(un.poly)
        if r.area > 1.0:
            un.rooms.append((nm, r))


def unit_bal_box(un: Unit) -> Polygon:
    """세대 앞 발코니 영역 — 세대 u 범위, 서측은 유리선 밖, 동측은 세대가
    동측 유리선에 닿을 때만(59㎡ 는 앞이 공용복도)."""
    x0, _, x1, _ = un.poly.bounds
    v1 = HV + 3 if un.poly.bounds[3] > HV - 0.5 else -HV
    return box(x0, -HV - 3, x1, v1).difference(box(x0, -HV, x1, HV))


def zone(plate: Polygon, u0, u1, v0=-HV, v1=HV) -> Polygon:
    return plate.intersection(box(min(u0, u1), v0, max(u0, u1), v1))


@dataclass
class FloorPlan:
    key: str
    title: str
    floors: list
    floor_i: int               # 그릴 대표 층
    units: list
    spaces: list               # (이름, poly, 색 키)
    corridor: list             # 공용 복도 다각형
    notes: list


def build_plans(L: float) -> list[FloorPlan]:
    plans = []
    ce = MID + CORE_W                      # 코어 바깥 모서리 |u|

    # ---- 기준층 Ⅱ (A·C, 6세대) -------------------------------------------
    i = 10
    pl = T.plate(i, L)
    s = T.floor_spec(i)[1]
    ends = {-1: -L / 2 + s, 1: L / 2 + s}
    units = [Unit("84", zone(pl, -MID, 0), -MID, 0, orient="동·서 맞통풍"),
             Unit("84", zone(pl, 0, MID), MID, 0, orient="동·서 맞통풍")]
    corr = []
    for side in (-1, 1):
        un = side * (ce + NEAR_W)
        units.append(Unit("59", zone(pl, side * ce, un, -HV, CORR[0]),
                          side * ce, un, orient="서향"))
        tip = zone(pl, un, ends[side] + side * 2)
        kind = "135" if tip.area > 125 else "114"
        units.append(Unit(kind, tip, un, ends[side], orient="3면 개방(단부)"))
        corr.append(zone(pl, side * ce, un, CORR[0], CORR[1]))
    plans.append(FloorPlan(
        "기준층2", "기준층 Ⅱ — A·C블록 6세대 (3–17 · 36–49층, 29개층)",
        list(range(3, 18)) + list(range(36, 50)), i, units, [], corr,
        ["블록 A·C 는 장축으로 −0.8m 엇갈림 → 북측 단부 135, 남측 단부 114",
         "59㎡: 코어 옆 서향 1면, 동측 공용복도(자연채광)로 출입",
         "단부 세대: 복도 끝에서 출입, 서·동·단부 3면 개방"]))

    # ---- 기준층 Ⅰ (B, 4세대) --------------------------------------------
    i = 25
    pl = T.plate(i, L)
    s = T.floor_spec(i)[1]
    ends = {-1: -L / 2 + s, 1: L / 2 + s}
    units = [Unit("84", zone(pl, -MID, 0), -MID, 0, orient="동·서 맞통풍"),
             Unit("84", zone(pl, 0, MID), MID, 0, orient="동·서 맞통풍")]
    for side in (-1, 1):
        units.append(Unit("대형", zone(pl, side * ce, ends[side] + side * 2),
                          side * ce, ends[side], orient="3면 개방(단부)"))
    plans.append(FloorPlan(
        "기준층1", "기준층 Ⅰ — B블록 4세대 (19–34층, 16개층)",
        list(range(19, 35)), i, units, [], [],
        ["블록 B 는 +0.8m 엇갈림 — 코어는 고정, 단부 대형 세대 폭이 달라진다",
         "대형: 코어 전실에서 바로 출입, 4베이 + 서재, 3면 개방",
         "84㎡: 2베이 맞통풍, 전실 공유(코어당 2세대)"]))

    # ---- 18층 스카이가든 + 피난안전구역 ---------------------------------
    i = 18
    pl = T.plate(i, L)
    full = T.rr(L, W, T.R_CORNER)
    units = []
    carved_west = {c[0]: c[1] == "W" for c in T.SKY[i]}      # N/S → 서측 파임?
    for side in (-1, 1):
        w_cut = carved_west["N" if side < 0 else "S"]
        units.append(Unit("테라스형", zone(pl, side * ce, side * (L / 2 + 2)),
                          side * ce, side * L / 2, orient="3면 개방 + 스카이테라스",
                          tpl="테라스형W" if w_cut else "테라스형E"))
    refuge = zone(pl, -MID, MID)
    terr = full.difference(pl)
    spaces = [("피난안전구역", refuge, "피난안전구역")]
    plans.append(FloorPlan(
        "18층", "18층 — 스카이가든 · 피난안전구역 (35층 동일 개념, 모서리 반대)",
        [18, 35], i, units, spaces, [],
        [f"피난안전구역 {refuge.area:,.0f}㎡ — 초고층(50층 이상)은 지상층에서 "
         "30개층 이내마다 설치(건축법 시행령 34조) → 18·35층",
         "파낸 모서리 8m = 스카이가든 테라스(식재·수목)",
         "피난안전구역은 용적률 산정 연면적에서 제외(시행령 119조)"], ))
    plans[-1].terraces = [g for g in getattr(terr, "geoms", [terr]) if g.area > 4]

    # ---- 2층 주민공동시설 -------------------------------------------------
    i = 2
    pl = T.plate(i, L)
    h = L / 2
    sp = [
        ("피트니스\n(웨이트·유산소)", zone(pl, -h - 2, -ce, -HV, CORR[0])),
        ("PT실 1\n1:1 퍼스널", zone(pl, -MID, -3.07, -HV, -1.0)),
        ("PT실 2\n1:1 퍼스널", zone(pl, -3.07, 1.13, -HV, -1.0)),
        ("PT실 3\n그룹·필라테스", zone(pl, 1.13, MID, -HV, -1.0)),
        ("락커·샤워(남)", zone(pl, -MID, 0, -1.0, CORR[0])),
        ("락커·샤워(여)", zone(pl, 0, MID, -1.0, CORR[0])),
        ("인포·라운지", zone(pl, ce, ce + 5.2, -HV, CORR[0])),
        ("GX룸", zone(pl, ce + 5.2, h + 2, -HV, -0.4)),
        ("골프연습장(GDR)", zone(pl, ce + 5.2, h + 2, -0.4, CORR[0])),
    ]
    corr = [zone(pl, -h - 2, -ce, CORR[0], CORR[1]),
            zone(pl, -MID, MID, CORR[0], CORR[1]),
            zone(pl, ce, h + 2, CORR[0], CORR[1])]
    plans.append(FloorPlan(
        "2층", "2층 — 주민공동시설 (피트니스 · PT실 · GX)", [2], i, [],
        [(n, g, "주민공동") for n, g in sp], corr,
        ["PT실 3종: 1:1 퍼스널 2실(약 19·22㎡) + 그룹·필라테스 1실(약 29㎡)",
         "동측 복도로 두 코어를 이어 층 전체를 한 동선으로 묶는다",
         "주민공동시설 면적은 용적률 산정 연면적에서 제외(시행령 119조)"]))
    for p in plans:
        for un in p.units:
            lay_rooms(un)
    return plans


# --------------------------------------------------------------------------- #
# SVG
# --------------------------------------------------------------------------- #
class Svg:
    def __init__(self, w, h, bg="#fcfcfa"):
        self.w, self.h = w, h
        self.s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" '
                  f'height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}" '
                  f'font-family="{FONT}">',
                  f'<rect width="100%" height="100%" fill="{bg}"/>']

    def add(self, x):
        self.s.append(x)

    def text(self, x, y, t, size=12, fill="#222", anchor="start", weight="normal",
             rot=0.0):
        lines = str(t).split("\n")
        tr = f' transform="rotate({rot:.1f} {x:.1f} {y:.1f})"' if rot else ""
        y0 = y - (len(lines) - 1) * size * 0.6
        for k, ln in enumerate(lines):
            self.s.append(f'<text x="{x:.1f}" y="{y0 + k * size * 1.2:.1f}" '
                          f'font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
                          f'font-weight="{weight}" dominant-baseline="middle"{tr}>'
                          f'{ln}</text>')

    def save(self, path: Path):
        self.s.append("</svg>")
        path.write_text("\n".join(self.s), encoding="utf-8")


def d_path(g, P) -> str:
    out = []
    for p in getattr(g, "geoms", [g]):
        if p.is_empty or p.geom_type != "Polygon":
            continue
        for ring in [p.exterior, *p.interiors]:
            pts = [P(x, y) for x, y in ring.coords]
            out.append("M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts) + " Z")
    return " ".join(out)


def poly(sv: Svg, g, P, fill="none", stroke="#333", sw=1.0, op=1.0, dash=None,
         extra=""):
    if g is None or g.is_empty:
        return
    da = f' stroke-dasharray="{dash}"' if dash else ""
    sv.add(f'<path d="{d_path(g, P)}" fill="{fill}" fill-opacity="{op}" '
           f'stroke="{stroke}" stroke-width="{sw}" fill-rule="evenodd"{da} {extra}/>')


def line(sv: Svg, ln, P, stroke="#333", sw=1.0, dash=None):
    pts = [P(x, y) for x, y in ln.coords]
    da = f' stroke-dasharray="{dash}"' if dash else ""
    sv.add('<polyline points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
           + f'" fill="none" stroke="{stroke}" stroke-width="{sw}"{da}/>')


# --------------------------------------------------------------------------- #
# 평면도
# --------------------------------------------------------------------------- #
def draw_plan(fp: FloorPlan, L: float, fr, balconies: dict, path: Path):
    k = 26.0                                     # px/m (약 1:150 @ 150dpi)
    u0, u1 = -L / 2 - 4.0, L / 2 + 4.0
    vt, vb = HV + 3.2, -HV - 4.2
    ox, oy = 60.0, 150.0
    Wd = (u1 - u0) * k + 2 * ox
    plan_h = (vt - vb) * k
    table_top = oy + plan_h + 40
    Hd = table_top + 30 + 22 * (len(fp.units) + 2) + 22 * len(fp.notes) + 40

    def P(u, v):
        return ox + (u - u0) * k, oy + (vt - v) * k

    sv = Svg(Wd, Hd)
    sv.text(ox, 40, f"화랑 A안 평면도 · {fp.title}", 22, "#111", weight="bold")
    fl = fp.floors
    sv.text(ox, 72, f"대표층 {fp.floor_i}층 · 층고 {T.FLOOR_H}m · 유리선(연면적선) "
            f"기준 개략 · 판 {L:.2f} × {W}m, 모서리 R{T.R_CORNER}", 13, "#555")
    sv.text(ox, 94, "아래 = 서측(픽셀 발코니) · 위 = 동측(연속 발코니·공용복도) · "
            "왼쪽 = 북 (방위 174°, 나침반 참조)", 13, "#555")

    plate = T.plate(fp.floor_i, L)
    # 발코니
    for bp in balconies.get(fp.floor_i, []):
        poly(sv, bp, P, "#f3f0e8", "#8d8a80", 0.8)
    # 테라스(스카이가든)
    for t in getattr(fp, "terraces", []):
        poly(sv, t, P, "#cfe5bf", "#6c8f55", 1.0)
        c = t.representative_point()
        sv.text(*P(c.x, c.y), "스카이가든\n테라스", 12, "#3d5c2c", "middle")
    # 슬래브 밴드
    poly(sv, plate.buffer(T.SLAB_EDGE, quad_segs=6), P, "none", "#9a978f", 0.8)
    # 공용 복도
    for c in fp.corridor:
        poly(sv, c, P, COLORS["복도"], "#8a8880", 0.6)
    # 공간(주민공동·피난안전)
    for nm, g, ck in fp.spaces:
        poly(sv, g, P, COLORS[ck], "#333", 2.2)
        c = g.representative_point()
        sv.text(*P(c.x, c.y), f"{nm}\n{g.area:,.0f}㎡", 13, "#222", "middle", "bold")
    # 세대
    for n, un in enumerate(fp.units, 1):
        un.name = f"{n:02d}"
        poly(sv, un.poly, P, COLORS[un.kind], "#222", 3.0)
        for nm, r in un.rooms:
            poly(sv, r, P, "none", "#666", 1.0)
            c = r.representative_point()
            sv.text(*P(c.x, c.y), f"{nm}\n{r.area:.0f}㎡" if r.area > 9 else nm,
                    10.5, "#444", "middle")
        # 현관 표시(출입 쪽 벽, 동측 띠)
        xa = un.x_from
        yc = (CORR[0] + CORR[1]) / 2 if un.kind != "59" else CORR[0]
        ex, ey = P(xa, yc)
        sv.add(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="6" fill="#c1121f"/>')
        # 세대 표찰
        c = un.poly.centroid
        cx, cy = P(c.x, -HV - 2.6)
        bw = 84 + 13 * len(un.kind)
        sv.add(f'<rect x="{cx - bw / 2:.1f}" y="{cy - 13:.1f}" width="{bw}" height="26" '
               f'rx="4" fill="{COLORS[un.kind]}" stroke="#222" stroke-width="1.2"/>')
        sv.text(cx, cy, f"{un.name}호 · {un.kind} · {un.poly.area:.0f}㎡", 12.5,
                "#111", "middle", "bold")
    # 코어
    for side in (-1, 1):
        cb = core_box(side).intersection(plate.buffer(0.01))
        poly(sv, cb, P, "#e3e2dd", "#111", 3.2)
        for nm, g, kd in core_parts(side):
            fill = {"stair": "#f4f3ef", "shaft": "#b9b7b0", "lobby": "#ecebe6",
                    "ev": "#ffffff"}[kd]
            poly(sv, g, P, fill, "#111", 1.6 if kd != "lobby" else 1.0)
            x0, y0, x1, y1 = g.bounds
            if kd == "ev":
                a, b = P(x0, y0), P(x1, y1)
                sv.add(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                       f'y2="{b[1]:.1f}" stroke="#999" stroke-width="0.8"/>')
                a, b = P(x0, y1), P(x1, y0)
                sv.add(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                       f'y2="{b[1]:.1f}" stroke="#999" stroke-width="0.8"/>')
                sv.text(*P((x0 + x1) / 2, (y0 + y1) / 2), nm, 9.5, "#c1121f",
                        "middle", "bold")
            elif kd == "stair":
                vm = (y0 + y1) / 2
                for t in range(1, 18):
                    uu = x0 + 0.9 + t * (x1 - x0 - 1.8) / 18
                    a, b = P(uu, y0 + 0.2), P(uu, y1 - 0.2)
                    sv.add(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                           f'y2="{b[1]:.1f}" stroke="#aaa" stroke-width="0.7"/>')
                a, b = P(x0 + 0.9, vm), P(x1 - 0.9, vm)
                sv.add(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                       f'y2="{b[1]:.1f}" stroke="#555" stroke-width="1.2"/>')
                c = P((x0 + x1) / 2, vm - 0.9)
                sv.add(f'<rect x="{c[0] - 44:.1f}" y="{c[1] - 9:.1f}" width="88" '
                       f'height="18" fill="#f4f3ef"/>')
                sv.text(c[0], c[1], nm, 10.5, "#111", "middle", "bold")
            else:
                sv.text(*P((x0 + x1) / 2, (y0 + y1) / 2), nm, 9.5, "#222",
                        "middle", "bold" if kd == "shaft" else "normal")
        c = core_box(side).centroid
        sv.text(*P(c.x, HV + 1.9), "코어 " + ("A(북)" if side < 0 else "B(남)"),
                12, "#111", "middle", "bold")
    # 유리선(외곽)
    poly(sv, plate, P, "none", "#1d3557", 2.0)
    # 치수선(상단)
    ce = MID + CORE_W
    s = T.floor_spec(fp.floor_i)[1]
    ticks = [-L / 2 + s, -ce, -MID, MID, ce, L / 2 + s]
    yd = oy - 22
    for a, b in zip(ticks, ticks[1:]):
        xa, _ = P(a, 0)
        xb, _ = P(b, 0)
        sv.add(f'<line x1="{xa:.1f}" y1="{yd}" x2="{xb:.1f}" y2="{yd}" '
               f'stroke="#555" stroke-width="0.9"/>')
        for xx in (xa, xb):
            sv.add(f'<line x1="{xx:.1f}" y1="{yd - 6}" x2="{xx:.1f}" y2="{yd + 6}" '
                   f'stroke="#555" stroke-width="0.9"/>')
        sv.text((xa + xb) / 2, yd - 11, f"{b - a:.2f}", 11, "#333", "middle")
    # 방위 — 북쪽 단위벡터의 u, v 성분을 화면 방향으로
    nu, nv = fr.u[1], fr.v[1]
    ang = math.degrees(math.atan2(nu, nv))            # 화면 위(+v)에서 시계방향
    cxn, cyn = Wd - 90, 100
    sv.add(f'<g transform="translate({cxn},{cyn}) rotate({ang:.1f})">'
           '<circle r="36" fill="#fff" stroke="#999"/>'
           '<polygon points="0,-30 -9,8 0,2 9,8" fill="#c1121f"/></g>')
    ra = math.radians(ang)
    sv.text(cxn + 48 * math.sin(ra), cyn - 48 * math.cos(ra), "N", 14, "#c1121f",
            "middle", "bold")
    # 축척
    bx, by = ox, oy + plan_h + 8
    sv.add(f'<line x1="{bx}" y1="{by}" x2="{bx + 10 * k}" y2="{by}" stroke="#111" '
           f'stroke-width="2"/>')
    sv.text(bx + 10 * k + 8, by, "10 m", 11, "#111")

    # 표
    y = table_top
    if fp.units:
        sv.text(ox, y, "호  타입  면적(㎡)  향 · 구성", 13, "#111", weight="bold")
        y += 24
        for un in fp.units:
            rooms = [n for n, _ in un.rooms if n.startswith("침실") or n in ("서재", "알파룸")]
            baths = sum(1 for n, _ in un.rooms if "욕실" in n)
            ub = unit_bal_box(un)
            bal = sum(b.intersection(ub).area
                      for b in balconies.get(fp.floor_i, []))
            sv.add(f'<rect x="{ox}" y="{y - 8}" width="16" height="16" '
                   f'fill="{COLORS[un.kind]}" stroke="#333"/>')
            sv.text(ox + 26, y, f"{un.name}호  {un.kind:4s}  {un.poly.area:6.1f}㎡  "
                    f"· 발코니 {bal:4.1f}㎡ · {un.orient} · 방 {len(rooms)} · 욕실 {baths}",
                    12.5, "#222")
            y += 22
    ncore = 2
    sv.text(ox, y + 6, f"코어 {ncore}개 × E/V 3대(승용 · 비상용 겸 · 피난용 겸) · "
            "특별피난계단 + 부속실 · PS · EPS/TPS · AD", 12.5, "#111", weight="bold")
    y += 30
    for n in fp.notes:
        sv.text(ox, y, "· " + n, 12.5, "#444")
        y += 22
    sv.save(path)
    return int(Wd), int(Hd)


# --------------------------------------------------------------------------- #
# 배치도
# --------------------------------------------------------------------------- #
def draw_site(PR: dict, L: float, fr, context, path: Path):
    site = PR["site"]
    F = PR["frame"]
    minx, miny, maxx, maxy = site.buffer(38).bounds
    k = 5.2
    head, pad, side_w = 120.0, 30.0, 380.0
    Wd = (maxx - minx) * k + 2 * pad + side_w
    Hd = (maxy - miny) * k + head + pad + 30

    def P(x, y):
        return pad + (x - minx) * k, head + (maxy - y) * k

    sv = Svg(Wd, Hd)
    sv.text(pad, 42, "화랑 A안 배치도 — 어린이집 · 성큰광장 · 산책로", 23, "#111",
            weight="bold")
    sv.text(pad, 74, "51층 1개동(172.3m) · 대지 9,395㎡ · 위가 북 · 타워 중심 "
            "E194,327.70 N546,974.70 (EPSG:5186)", 13, "#555")
    sv.text(pad, 96, "주변 건물 = 수치지도 건물(AL_D010) · 학교 운동장 = 배치도 해치 "
            "경계 · 대지 둘레 도로는 확인 전(진출입 위치 조정 가능)", 13, "#555")
    clip = box(minx, miny, maxx, maxy)
    sv.add(f'<defs><clipPath id="c"><rect x="{pad}" y="{head}" '
           f'width="{(maxx - minx) * k:.0f}" height="{(maxy - miny) * k:.0f}"/>'
           '</clipPath>'
           '<pattern id="hs" width="8" height="8" patternUnits="userSpaceOnUse" '
           'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" '
           'stroke="#8a7f6c" stroke-width="1"/></pattern></defs>')
    sv.add('<g clip-path="url(#c)">')
    sv.add(f'<rect x="{pad}" y="{head}" width="{(maxx - minx) * k:.0f}" '
           f'height="{(maxy - miny) * k:.0f}" fill="#f1f0ec"/>')
    # 학교 운동장
    if SA_PLAYGROUNDS.exists():
        for f in json.loads(SA_PLAYGROUNDS.read_text(encoding="utf-8"))["features"]:
            g = affinity.translate(shape(f["geometry"]), -SA.ORIGIN[0], -SA.ORIGIN[1])
            poly(sv, g, P, "#cfe0c3", "#a86a55", 2.0)
    for kind, g, top, label in context:
        if not g.intersects(clip):
            continue
        fill = {"school": "#e9d6d3", "daegyo": "#d9dde6", "sibeom": "#d9dde6"}.get(
            kind, "#dedcd6")
        poly(sv, g, P, fill, "#a5a39d", 0.8)
        if top >= 30 or kind == "school":
            c = g.representative_point()
            if clip.contains(c):
                sv.text(*P(c.x, c.y), f"{label}\n{top:.0f}m" if label else f"{top:.0f}m",
                        9.5, "#666", "middle")
    # 대지
    poly(sv, site, P, "#f7f6f2", "none", 0)
    poly(sv, PR["plaza"], P, "#e7e2d6", "#c9c1ae", 0.6)
    poly(sv, PR["lawn"], P, "#cfe3b8", "#a3bd88", 0.6)
    poly(sv, PR["path_poly"], P, "#efe4cf", "#c7b48f", 0.8)
    # 어린이집·놀이마당
    poly(sv, PR["kg_yard"], P, "#f6d7a9", "#c98a2e", 1.4)
    yard_fence = PR["kg_yard"].exterior
    line(sv, yard_fence, P, "#9a5b12", 1.2, "3 3")
    for kx, ky, rr in ((33, 14, 2.6), (40, 24, 3.0), (36, 28, 1.6)):
        x, y = F.xy(kx, ky)
        sv.add(f'<circle cx="{P(x, y)[0]:.1f}" cy="{P(x, y)[1]:.1f}" r="{rr * k:.1f}" '
               f'fill="#fff4e0" stroke="#c98a2e" stroke-width="1"/>')
    poly(sv, PR["kg"], P, "#ffd166", "#7a5200", 2.4)
    poly(sv, PR["kg_entry"], P, "#e7e2d6", "#7a5200", 1.0)
    # 성큰광장
    poly(sv, PR["sunken"].buffer(0.4), P, "none", "#444", 2.4)
    poly(sv, PR["sunken"], P, "#d8d3c6", "#555", 1.0)
    for st in PR["stand"]:
        poly(sv, st, P, "#e9e4d8", "#8a8373", 0.8)
    poly(sv, PR["stair"], P, "url(#hs)", "#555", 1.0)
    line(sv, PR["b1_front"], P, "#1d65b5", 4.0)
    # 램프
    poly(sv, PR["ramp"], P, "#9b9b98", "#333", 1.4)
    rc = PR["ramp"].centroid
    # 타워: 기준층 투영 + 1층 로비 + 외형선
    plates = unary_union([fr.poly(T.plate(i, L)) for i in range(2, T.FLOORS + 1)])
    lobby = fr.poly(T.plate(1, L))
    poly(sv, PR["outline"], P, "none", "#1d3557", 1.0, dash="5 4")
    poly(sv, plates, P, "#2b4c7e", "#0b1d33", 1.8)
    poly(sv, lobby, P, "none", "#ffffff", 1.4, dash="6 4")
    for side in (-1, 1):
        poly(sv, fr.poly(core_box(side)), P, "#1b2f4f", "#0b1d33", 1.0)
    # 수목
    for x, y, h in PR["trees"]:
        a, b = P(x, y)
        r = (1.4 + h * 0.28) * k
        sv.add(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="{r:.1f}" fill="#7fae6a" '
               f'fill-opacity="0.75" stroke="#4d7a3c" stroke-width="0.8"/>')
    # 대지경계
    poly(sv, site, P, "none", "#c1121f", 2.2, dash="12 5 3 5")
    sv.add("</g>")

    # 이름표(지시선)
    def callout(x, y, dx, dy, txt, color="#111"):
        a, b = P(x, y)
        c, d = a + dx, b + dy
        sv.add(f'<line x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{d:.1f}" '
               f'stroke="{color}" stroke-width="1"/>')
        sv.add(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.5" fill="{color}"/>')
        lines = txt.split("\n")
        wbox = max(len(s) for s in lines) * 12.5 + 14
        hb = 18 * len(lines) + 8
        bx = c if dx >= 0 else c - wbox
        sv.add(f'<rect x="{bx:.1f}" y="{d - hb / 2:.1f}" width="{wbox:.1f}" '
               f'height="{hb:.1f}" rx="4" fill="#fff" fill-opacity="0.93" '
               f'stroke="{color}" stroke-width="1"/>')
        sv.text(bx + 7, d, txt, 12.5, color)

    c = PR["kg"].centroid
    ag = PR["kg"].area
    callout(c.x, c.y, -120, 150,
            f"어린이집·유치원\n지상 2층 · 높이 {SA.KG_HEIGHT:.1f}m\n"
            f"바닥 {ag:,.0f}㎡ × 2 = {ag * 2:,.0f}㎡", "#7a5200")
    c = PR["kg_yard"].centroid
    callout(c.x, c.y, 140, 150, f"전용 놀이마당(남동향)\n{PR['kg_yard'].area:,.0f}㎡ · "
            "울타리·그늘목", "#9a5b12")
    c = PR["sunken"].centroid
    callout(c.x, c.y, 190, 60,
            f"성큰광장 B1 FL −{SA.SUNKEN_DEPTH:.1f}m\n{PR['sunken'].area:,.0f}㎡ · "
            "계단식 스탠드 10단\nB1 카페·작은도서관(파란 선)", "#333")
    x, y = PR["stair"].centroid.coords[0]
    callout(x, y, 120, 70, "직통계단", "#333")
    callout(rc.x, rc.y, 10, 215, "지하주차장 진출입 램프\n(폭 7m, 도로 확인 후 조정)", "#333")
    x, y = F.xy(69.0, 60.0)
    callout(x, y, -150, -40, "남서–북동 산책로(폭 3m)\n대교 쪽 → 여의도초 쪽", "#8a6d3b")
    tc = plates.centroid
    callout(tc.x, tc.y + 10, -60, -215,
            "화랑 A안 51층 · 172.3m\n기준층 747㎡ · 코어 2개(E/V 6대)\n"
            "흰 파선 = 1층 로비(서측 아케이드)", "#0b1d33")
    x, y = F.xy(50, 88)
    callout(x, y, -160, -70, "로비 광장(서측 아케이드 앞)", "#6b5d3e")

    # 방위·축척
    nx, ny = pad + (maxx - minx) * k - 48, head + 60
    sv.add(f'<g transform="translate({nx:.0f},{ny:.0f})"><circle r="34" fill="#fff" '
           'fill-opacity="0.85" stroke="#999"/><polygon points="0,-28 -9,6 0,0 9,6" '
           'fill="#c1121f"/><text y="22" text-anchor="middle" font-size="13" '
           'font-weight="bold" fill="#111">N</text></g>')
    bx, by = pad + 18, head + (maxy - miny) * k - 22
    for t in range(5):
        sv.add(f'<rect x="{bx + t * 10 * k:.1f}" y="{by}" width="{10 * k:.1f}" '
               f'height="7" fill="{"#111" if t % 2 == 0 else "#fff"}" stroke="#111"/>')
    sv.text(bx, by - 10, "0", 11, "#111")
    sv.text(bx + 50 * k, by - 10, "50 m", 11, "#111", "middle")

    # 우측 개요표
    tx = pad + (maxx - minx) * k + 24
    ty = head + 10
    tower_fp = plates.area
    kg_a = PR["kg"].area
    bcr_a = tower_fp + kg_a
    gfa = T.GFA_TARGET
    comm = T.plate(2, L).area
    refuge = 2 * zone(T.plate(18, L), -MID, MID).area
    far_base = gfa - comm - refuge
    rows = [
        ("대지면적", "9,395㎡"),
        ("건축면적", f"{bcr_a:,.0f}㎡"),
        ("  타워(기준층 투영)", f"{tower_fp:,.0f}㎡"),
        ("  어린이집", f"{kg_a:,.0f}㎡"),
        ("건폐율", f"{bcr_a / 9395 * 100:.2f}%"),
        ("연면적(지상)", f"{gfa + 2 * kg_a:,.0f}㎡"),
        ("  타워", f"{gfa:,.0f}㎡"),
        ("  어린이집(2층)", f"{2 * kg_a:,.0f}㎡"),
        ("용적률 산정 연면적", f"{far_base:,.0f}㎡"),
        ("용적률", f"{far_base / 9395 * 100:.1f}% (≤400%)"),
        ("  제외: 주민공동시설", f"2층 {comm:,.0f} · 어린이집 {2 * kg_a:,.0f}㎡"),
        ("  제외: 피난안전구역", f"18·35층 {refuge:,.0f}㎡"),
        ("세대수", "244세대"),
        ("최고 높이", "51층 · 172.3m"),
        ("성큰광장", f"{PR['sunken'].area:,.0f}㎡ (B1 −5.0m)"),
        ("놀이마당", f"{PR['kg_yard'].area:,.0f}㎡"),
        ("조경(잔디·식재)", f"{PR['lawn'].area:,.0f}㎡ · 수목 {len(PR['trees'])}"),
    ]
    sv.add(f'<rect x="{tx - 12}" y="{ty - 22}" width="{side_w - 30}" '
           f'height="{len(rows) * 25 + 40}" fill="#fff" stroke="#bbb"/>')
    sv.text(tx, ty, "개요", 15, "#111", weight="bold")
    ty += 28
    for a, b in rows:
        bold = "bold" if not a.startswith("  ") else "normal"
        sv.text(tx, ty, a, 12.5, "#333", weight=bold)
        sv.text(tx + side_w - 56, ty, b, 12.5, "#111", "end", bold)
        ty += 25
    ty += 30
    legend = [("#2b4c7e", "타워 기준층 투영 (진한 = 코어)"), ("#ffd166", "어린이집·유치원"),
              ("#f6d7a9", "놀이마당"), ("#d8d3c6", "성큰광장"), ("#e7e2d6", "포장 광장"),
              ("#cfe3b8", "잔디·식재"), ("#efe4cf", "산책로"), ("#9b9b98", "주차 램프"),
              ("#e9d6d3", "학교"), ("#cfe0c3", "학교 운동장")]
    for col, t in legend:
        sv.add(f'<rect x="{tx}" y="{ty - 8}" width="18" height="16" fill="{col}" '
               'stroke="#555"/>')
        sv.text(tx + 28, ty, t, 12.5, "#333")
        ty += 24
    sv.add(f'<line x1="{tx}" y1="{ty}" x2="{tx + 18}" y2="{ty}" stroke="#c1121f" '
           'stroke-width="2" stroke-dasharray="6 3 2 3"/>')
    sv.text(tx + 28, ty, "대지경계", 12.5, "#333")
    ty += 24
    sv.add(f'<line x1="{tx}" y1="{ty}" x2="{tx + 18}" y2="{ty}" stroke="#1d3557" '
           'stroke-dasharray="5 4"/>')
    sv.text(tx + 28, ty, "일조 분석 외형선", 12.5, "#333")
    ty += 36
    for t in ["어린이집을 남서부 30개 위치에 두어",
              "검사 — 어느 위치든 학교 신규 불충족",
              "추가 0 (건물 33 · 전체 73 그대로)"]:
        sv.text(tx, ty, t, 12, "#555")
        ty += 20
    sv.save(path)
    return int(Wd), int(Hd)


def to_png(svg: Path, png: Path, w: int, h: int) -> bool:
    """SVG → PNG (헤드리스 크로미움). 스크롤바가 끼지 않게 HTML 로 감싸 자른다."""
    import shutil
    import subprocess
    import urllib.parse
    from PIL import Image
    exe = next((c for c in CHROME if Path(c).exists()), None) or shutil.which("chromium")
    if not exe:
        return False
    html = svg.with_suffix(".tmp.html")
    html.write_text(f'<html><body style="margin:0;overflow:hidden;background:#fff">'
                    f'<img src="{urllib.parse.quote(svg.name)}" width="{w}" '
                    f'height="{h}"></body></html>', encoding="utf-8")
    url = "file://" + urllib.parse.quote(str(html.resolve()))
    subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu",
                    "--hide-scrollbars", f"--screenshot={png}",
                    f"--window-size={w},{h + 120}", url],
                   capture_output=True, timeout=120)
    html.unlink(missing_ok=True)
    if not png.exists():
        return False
    Image.open(png).crop((0, 0, w, h)).save(png, optimize=True)
    return True


SA_PLAYGROUNDS = Path("outputs/school_compliance/playground/운동장_배치도경계_epsg5186.geojson")


def load_context(gpkg: Path | None):
    if gpkg is None:
        return []
    import hwarang_birdseye_2026 as BE
    ns = argparse.Namespace(buildings=gpkg, hwarang=T.ENVELOPE,
                            daegyo=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"),
                            sibeom=Path("outputs/sibeom/sibeom_epsg5186.geojson"),
                            radius=300.0, school_radius=350.0)
    solids, _rows, _c = BE.build_scene(ns)
    out = []
    for s in solids:
        if s.kind == "hwarang":
            continue
        g = affinity.translate(s.footprint, -SA.ORIGIN[0], -SA.ORIGIN[1])
        lab = s.label if s.kind == "school" else ""
        lab = lab.replace("등학교", "").replace("학교", "") if lab else lab
        out.append((s.kind, g, s.top_m, lab))
    return out


def load_balconies(fr) -> dict:
    p = Path("outputs/hwarang_detail_A_2026/화랑A_발코니_epsg5186.geojson")
    out = {}
    if not p.exists():
        return out
    (ux, uy), (vx, vy) = fr.u, fr.v
    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
        g = affinity.translate(shape(f["geometry"]), -fr.cx, -fr.cy)
        g = affinity.affine_transform(g, [ux, uy, vx, vy, 0, 0])
        out.setdefault(f["properties"]["floor"], []).append(g)
    return out


def check_sun(gpkg: Path, PR: dict) -> tuple:
    import hwarang_design_2026 as HD
    import hwarang_massing_study as H
    from school_receptor_compliance import pass_a
    a = HD.parse_args(["--buildings", str(gpkg),
                       "--sibeom", "outputs/sibeom/sibeom_epsg5186.geojson"])
    ctx = HD.setup(a)
    feats = json.loads(T.ENVELOPE.read_text(encoding="utf-8"))["features"]
    env = [H.Prism(shape(f["geometry"]), float(f["properties"]["height_m"]), "A")
           for f in feats if f["properties"].get("height_m")]
    kg = affinity.translate(PR["kg"], SA.ORIGIN[0], SA.ORIGIN[1])
    res = []
    for pr in (env, env + [H.Prism(kg, SA.KG_HEIGHT, "어린이집")]):
        rr = H.evaluate(ctx["receptors"], pr, ctx["times"], ctx["masks"], 10)
        nf = [i for i, (r, ok) in enumerate(zip(rr, ctx["base_pass"]))
              if ok and not pass_a(r)]
        nb = sum(1 for i in nf if ctx["points"][i].kind != "운동장 지반")
        res.append((len(nf), nb))
    return tuple(res)


def export_geojson(plans, fr, PR, L, path: Path):
    feats = []

    def W_(g):
        return affinity.translate(fr.poly(g), fr.cx, fr.cy)

    for fp in plans:
        for un in fp.units:
            feats.append({"geometry": W_(un.poly), "properties": {
                "plan": fp.key, "floors": f"{fp.floors[0]}-{fp.floors[-1]}",
                "layer": "세대", "type": un.kind, "no": un.name,
                "area_m2": round(un.poly.area, 1), "orient": un.orient}})
            for nm, r in un.rooms:
                feats.append({"geometry": W_(r), "properties": {
                    "plan": fp.key, "layer": "실", "type": un.kind, "no": un.name,
                    "name": nm, "area_m2": round(r.area, 1)}})
        for nm, g, ck in fp.spaces:
            feats.append({"geometry": W_(g), "properties": {
                "plan": fp.key, "layer": ck, "name": nm.replace("\n", " "),
                "area_m2": round(g.area, 1)}})
        for side in (-1, 1):
            for nm, g, kd in core_parts(side):
                feats.append({"geometry": W_(g), "properties": {
                    "plan": fp.key, "layer": "코어", "core": "A" if side < 0 else "B",
                    "name": nm.replace("\n", " "), "kind": kd,
                    "area_m2": round(g.area, 1)}})
    for key, lab in (("kg", "어린이집"), ("kg_yard", "놀이마당"), ("sunken", "성큰광장"),
                     ("ramp", "주차램프"), ("plaza", "광장"), ("lawn", "잔디·식재"),
                     ("path_poly", "산책로")):
        g = affinity.translate(PR[key], SA.ORIGIN[0], SA.ORIGIN[1])
        feats.append({"geometry": g, "properties": {
            "plan": "배치도", "layer": lab, "area_m2": round(PR[key].area, 1)}})
    doc = {"type": "FeatureCollection",
           "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
           "features": [{"type": "Feature", "geometry": mapping(f["geometry"]),
                         "properties": f["properties"]} for f in feats]}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def unit_schedule(plans) -> list:
    """동 전체 세대 구성(층 수 × 층당 세대)."""
    rows = {}
    for fp in plans:
        for un in fp.units:
            key = (un.kind, round(un.poly.area))
            r = rows.setdefault(key, [0, set()])
            r[0] += len(fp.floors)
            r[1].add(fp.key)
    core2 = 2 * core_box(1).area
    ph = (T.plate(50, L_GLOBAL[0]).area + T.plate(51, L_GLOBAL[0]).area - 2 * core2) / 2
    rows[("펜트(50–51층 복층)", round(ph))] = [2, {"50–51층"}]
    return sorted(rows.items(), key=lambda kv: kv[0][1])


L_GLOBAL = [0.0]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--buildings", type=Path, default=None)
    ap.add_argument("--check-sun", action="store_true")
    ap.add_argument("--outdir", type=Path, default=OUT)
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)

    fr, _outline, _site = T.load_frame(T.ENVELOPE)
    L, _ = T.solve_length()
    L_GLOBAL[0] = L
    PR = SA.program()
    plans = build_plans(L)
    bal = load_balconies(fr)

    made = []
    for fp in plans:
        svg = a.outdir / f"평면도_{fp.key}.svg"
        w, h = draw_plan(fp, L, fr, bal, svg)
        made.append((svg, w, h))
    ctx = load_context(a.buildings)
    svg = a.outdir / "배치도.svg"
    w, h = draw_site(PR, L, fr, ctx, svg)
    made.append((svg, w, h))
    for svg, w, h in made:
        ok = to_png(svg, svg.with_suffix(".png"), w, h)
        print(f"  {svg.name} {w}×{h} {'PNG' if ok else '(PNG 실패)'}")

    export_geojson(plans, fr, PR, L, a.outdir / "평면·배치_epsg5186.geojson")
    sched = unit_schedule(plans)
    total = 0
    with (a.outdir / "세대구성표.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        w_ = csv.writer(fp)
        w_.writerow(["타입", "세대면적_㎡(유리선)", "세대수", "평면"])
        for (kind, area), (n, keys) in sched:
            w_.writerow([kind, area, n, " ".join(sorted(keys))])
            total += n
            print(f"  {kind:14s} {area:5d}㎡ × {n:3d}세대  ({' '.join(sorted(keys))})")
        w_.writerow(["합계", "", total, ""])
    print(f"  합계 {total}세대")
    if a.check_sun:
        if not a.buildings:
            raise SystemExit("--check-sun 은 --buildings 가 필요하다")
        (n0, b0), (n1, b1) = check_sun(a.buildings, PR)
        print(f"  일조: A안만 신규 불충족 {n0}(건물 {b0}) → +어린이집 {n1}(건물 {b1})")
    print(f"→ {a.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
