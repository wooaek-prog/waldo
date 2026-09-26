#!/usr/bin/env python3
"""hwarang_unit_blocks.py 사용 예시 – 초보자용 완성 스크립트.

이 파일을 그대로 실행하면 84B 세대 1개를 기준층에 배치해보고,
전체 평형의 수용 세대수를 출력합니다.

실행 방법:
    python3 example_usage.py
"""

import json
import hwarang_unit_blocks as U

# ── 1단계: 설정 파일 읽기 ──────────────────────────────────────────
# data/hwarang_unit_blocks.json 에는 세 가지 정보가 들어 있습니다.
#   - cfg["plate"] : 기준층 판(크기, 위치, 방위각 등)
#   - cfg["core"]  : 코어(승강기·계단) 가정 크기
#   - cfg["units"] : 84A~116A 등 7개 평형의 원본 설계도(베이 폭 등)
cfg = json.loads(open("data/hwarang_unit_blocks.json", encoding="utf-8").read())
plate = cfg["plate"]
core = cfg["core"]

# ── 2단계: 평형별 "블럭 객체" 만들기 ───────────────────────────────
# cfg["units"]는 아직 숫자 뭉치(딕셔너리)일 뿐입니다.
# build_block()이 이걸 실제 도형(Polygon)을 가진 UnitBlock 객체로 바꿔줍니다.
# blocks 는 {"84A": UnitBlock(...), "84B": UnitBlock(...), ...} 형태의 딕셔너리가 됩니다.
blocks = {
    spec["code"]: U.build_block(
        spec,                          # 이 평형의 설계도 (84A, 84B, ... 중 하나)
        cfg["frame_factor"],           # 골조 보정계수 (1.06)
        cfg["balcony_depth_mm"],       # 발코니 깊이 (1500mm)
        cfg["calibrate"],              # 깊이를 전용면적에 맞춰 보정할지 (true)
    )
    for spec in cfg["units"]
}
print("만들어진 블럭:", list(blocks.keys()))
# → ['84A', '84B', '88A', '93A', '102A', '105A', '116A']

# ── 3단계: 블럭 하나를 기준층 위에 배치해보기 ──────────────────────
# place_on_plate(블럭, 판, x좌표, y좌표, 회전각, 좌우반전)
#   x_mm, y_mm : 기준층 로컬좌표(왼쪽 아래가 원점, 단위 mm)의 어느 위치에 놓을지
#   rotation_deg : 이 블럭을 몇 도 회전시킬지 (0 = 도면 그대로)
#   mirror : True 면 좌우로 뒤집힌 세대(반대편 열에 쓸 때 필요)
poly = U.place_on_plate(
    blocks["84B"],      # 84B 평형 블럭
    plate,
    x_mm=0, y_mm=0,     # 기준층 왼쪽 아래 모서리에 붙여서 배치
    rotation_deg=0,
    mirror=False,
)
print(f"\n84B 배치 결과: 골조 면적 {poly.area / 1e6:.1f}㎡")
print(f"배치된 도형의 좌표 범위(mm): {poly.bounds}")

# ── 4단계: 로컬좌표 → 실제 지도 좌표(EPSG:5186)로 변환 ─────────────
# 위 poly는 "기준층 안에서의 상대 위치"일 뿐, 실제 지구상 좌표가 아닙니다.
# plate_to_world()가 이를 QGIS 등에서 쓸 수 있는 실좌표로 바꿔줍니다.
world = U.plate_to_world(poly, plate)
print(f"\n실좌표(EPSG:5186) 중심점: E={world.centroid.x:.2f}, N={world.centroid.y:.2f}")

# ── 5단계: 평형별로 기준층에 몇 세대씩 들어가는지 확인 ─────────────
# plate_capacity()는 배치를 직접 해보지 않고도 "대략 몇 세대 들어가는지" 계산합니다.
caps = U.plate_capacity(plate, core, list(blocks.values()))
print(f"\n{'평형':<7}{'수용 세대수':>10}{'배치 방식':<16}")
print("-" * 35)
for cap in caps:
    print(f"{cap['code']:<7}{cap['units']:>10}{cap['placement']:<16}")
