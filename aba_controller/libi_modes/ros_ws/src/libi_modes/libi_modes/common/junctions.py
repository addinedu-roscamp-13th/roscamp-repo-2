"""navgraph 에서 **갈림길 정점**만 뽑아 좌표로 들고 있는다. ROS 를 모른다.

## 왜 갈림길만인가

길잡이가 "여기서 꺾습니다" 하고 잠깐 서서 사람을 확인하면 좋다. 그런데 **모든 노드에서
서면 안 된다** — 실제 운용 맵(arte2)의 레인 길이가 0.151~0.601m 라, 로봇 속도 0.12 m/s
기준 **1~5초마다 한 번씩** 서게 된다. 안내가 계속 끊긴다.

갈림길(레인이 셋 이상 붙은 정점)만 고르면 실제로 방향이 바뀌는 지점에서만 선다.
그 지점이 사람에게도 로봇을 놓치기 쉬운 곳이다.

## 왜 로봇이 navgraph 를 읽나

BT 는 목적지를 **좌표**로 받는다(`nav_target`). 이름이 없으므로 "지금 갈림길인가"를
알려면 그래프를 봐야 한다. 파일은 이미 로봇 체크아웃에 있다(`pi.sh` 가 같은 파일을
다른 스크립트에 넘긴다).

파일이 없으면 갈림길 목록이 비고, 확인 동작이 그냥 꺼진다 — 안내는 계속된다.
"""
import math


def load_junctions(path, min_degree: int = 3):
    """navgraph YAML → 갈림길 정점 좌표 목록 `[(x, y), ...]`.

    읽지 못하면 빈 목록이다. 확인 동작이 꺼질 뿐 안내가 죽지는 않는다.
    """
    try:
        import yaml
        with open(path) as f:
            doc = yaml.safe_load(f)
    except Exception:       # noqa: BLE001 — 깨진 navgraph 로 미션 노드가 죽으면 안 된다.
        # yaml.YAMLError 는 ValueError 가 아니라서 좁게 잡으면 그대로 새어 나간다.
        # 여기서 죽으면 부팅이 실패하고, 길잡이뿐 아니라 순회·복귀까지 같이 멈춘다.
        return []
    if not isinstance(doc, dict):
        return []
    out = []
    for level in (doc.get("levels") or {}).values():
        verts = level.get("vertices") or []
        degree = [0] * len(verts)
        for lane in level.get("lanes") or []:
            if len(lane) < 2:
                continue
            a, b = lane[0], lane[1]
            if 0 <= a < len(verts):
                degree[a] += 1
            if 0 <= b < len(verts):
                degree[b] += 1
        for i, v in enumerate(verts):
            if degree[i] >= min_degree and len(v) >= 2:
                out.append((float(v[0]), float(v[1])))
    return out


class JunctionSet:
    """좌표가 갈림길에 해당하는지 판정한다."""

    def __init__(self, points, tolerance: float = 0.05):
        self.points = list(points or [])
        self.tolerance = float(tolerance)

    def __len__(self):
        return len(self.points)

    def contains(self, target) -> bool:
        """`target` 은 `{"x":…, "y":…}` 또는 `(x, y)`. 허용오차 안이면 True."""
        if not self.points or target is None:
            return False
        try:
            x = target["x"] if isinstance(target, dict) else target[0]
            y = target["y"] if isinstance(target, dict) else target[1]
        except (KeyError, IndexError, TypeError):
            return False
        return any(math.hypot(px - x, py - y) <= self.tolerance
                   for px, py in self.points)
