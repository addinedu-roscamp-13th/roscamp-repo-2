"""로봇의 `waypoint.yaml` → fleet_node 가 읽는 navgraph 생성.

## 왜 필요한가
fleet_node 는 자기 navgraph 의 **정점 이름**으로 목적지를 찾는다. 주문의 pickup/dropoff 가
`문학-1`·`테이블-1번-상` 같은 waypoint 이름이므로, fleet 쪽에도 같은 이름의 정점이 있어야
한다. 예전 `new_map.navgraph.yaml` 은 정점 8개짜리 포팅 placeholder(`아일L_상` 등)라
실제 지점이 하나도 없었다 — 그래서 주문이 통과하지 못한다.

## 실행
    .venv/bin/python scripts/gen_arte2_navgraph.py

waypoint.yaml 을 고쳤으면 이걸 다시 돌리고 fleet_node 를 재기동한다.
프론트 지도용 좌표(`aba_service/frontend/src/lib/map-waypoints.ts`)와 원본이 같으므로,
둘 다 다시 생성해야 화면과 로봇이 어긋나지 않는다.
"""

from __future__ import annotations

import math
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAYPOINTS = os.path.join(
    ROOT,
    "aba_controller/libi_drive_controller/ros_ws/src/pinky_pro",
    "pinky_navigation/params/waypoint.yaml",
)
OUT = os.path.join(ROOT, "aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml")


# 로봇 지름(nav2 inscribed 0.088 m × 2). 이 안에 들어오면 두 대가 물리적으로 겹친다.
ROBOT_DIA_M = 0.176


def split_through_lanes(
    pairs: set[tuple[int, int]], pos: list[tuple[float, float]]
) -> set[tuple[int, int]]:
    """다른 정점을 **관통하는** 레인을 그 정점에서 두 토막으로 나눈다.

    ## 왜
    교통관제는 "정점을 예약"해서 충돌을 막는다. 그 전제는 *레인을 지나는 것과 정점을
    점유하는 것이 별개* 라는 것인데, 레인이 제3의 정점 위를 지나가면 그 전제가 깨진다 —
    9→13 을 달리는 로봇은 10 을 예약하지 않고도 10 위를 **쓸고 지나간다.** 10 에 서 있던
    로봇은 예약 규칙을 다 지켰는데도 받힌다.

    arte2 에서 실제로 그런 레인이 하나 있다(9-13 이 10 을 거리 0.0 으로 관통). 나누고 나면
    같은 통로가 9-10-13 이 되어, 지나가려면 10 을 반드시 예약하게 된다.

    ⚠️ 이건 **정점 위**만 본다. 두 레인의 중심선이 서로 가까운 경우(arte2 에 13쌍)는
       여기서 못 잡는다 — 그건 정점을 옮겨야 하는 문제라 현장 실측이 필요하다.
    """
    out = set()
    for a, b in pairs:
        (ax, ay), (bx, by) = pos[a], pos[b]
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        cut = None
        for c, (cx, cy) in enumerate(pos):
            if c in (a, b) or span == 0.0:
                continue
            t = ((cx - ax) * dx + (cy - ay) * dy) / span
            if not 0.05 < t < 0.95:            # 끝점 근처는 관통이 아니다
                continue
            if math.hypot(cx - (ax + t * dx), cy - (ay + t * dy)) < ROBOT_DIA_M:
                if cut is None or t < cut[0]:
                    cut = (t, c)
        if cut is None:
            out.add((a, b))
        else:
            # 토막 자체가 또 관통할 수 있어(정점 3개가 한 줄) 재귀로 끝까지 나눈다.
            out |= split_through_lanes({(a, cut[1]), (cut[1], b)}, pos)
    return out


def main() -> None:
    data = yaml.safe_load(open(WAYPOINTS))
    verts: dict = data["vertices"]
    lanes: list = data.get("lanes", [])

    names = list(verts.keys())  # 순서가 곧 인덱스다 — lanes 가 이 인덱스를 참조한다
    idx = {n: i for i, n in enumerate(names)}

    # fleet navgraph 의 lane 은 단방향이다. waypoint.yaml 의 bidirectional 은 양쪽으로 편다.
    pairs: set[tuple[int, int]] = set()
    for lane in lanes:
        a, b = lane.get("from"), lane.get("to")
        if a not in idx or b not in idx:
            continue
        pairs.add((idx[a], idx[b]))
        if lane.get("bidirectional", True):
            pairs.add((idx[b], idx[a]))

    pairs = split_through_lanes(pairs, [(verts[n]["x"], verts[n]["y"]) for n in names])

    lines = [
        "# 자동 생성 — 원본: pinky_navigation/params/waypoint.yaml (arte2 맵)",
        "# fleet_node 가 읽는 navgraph. 정점 이름이 주문의 pickup/dropoff 와 같아야 한다.",
        "# 재생성: scripts/gen_arte2_navgraph.py",
        "building_name: arte2",
        "doors: {}",
        "levels:",
        "  L1:",
        "    lanes:",
    ]
    for a, b in sorted(pairs):
        lines += [f"    - - {a}", f"      - {b}", "      - {}"]

    lines.append("    vertices:")
    for n in names:
        v = verts[n]
        # yaw 는 그 지점에서 로봇이 바라볼 방향이다(서가 정면, 도킹 자세).
        # 예전엔 버렸는데, 그러면 fleet_node 가 **진행 방향**만 쓰게 되어 로봇이 서가를
        # 옆으로 보고 서고 주차장에도 아무 방향으로 들어간다.
        # 없는 정점은 키를 아예 빼서 "방향 지정 없음"과 "동쪽(0)을 보라"를 구분한다.
        meta = f"name: '{n}'"
        if v.get("yaw") is not None:
            meta += f", yaw: {v['yaw']}"
        lines += [
            f"    # {idx[n]} {n}",
            f"    - - {v['x']}",
            f"      - {v['y']}",
            f"      - {{{meta}}}",
        ]
    lines += ["lifts: {}", ""]

    with open(OUT, "w") as f:
        f.write("\n".join(lines))

    print(f"[gen_arte2_navgraph] {OUT}")
    print(f"[gen_arte2_navgraph] 정점 {len(names)}개 / 단방향 레인 {len(pairs)}개")


if __name__ == "__main__":
    main()
