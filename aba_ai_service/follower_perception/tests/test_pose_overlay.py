"""자세를 화면에 그린다 — 스켈레톤 + 서있음/누워있음 판정.

판정만 글자로 띄우면 "왜 Unknown 인지"를 알 수 없다. 골격을 같이 그리면 점이 몇 개나
잡혔는지 눈에 보여, 모델이 못 본 건지 임계가 센 건지 화면에서 갈린다.

여기서 지키는 것은 **좌표계 하나**다. `PoseEstimator` 는 owner bbox crop 에만 pose 를
돌리므로 키포인트가 crop 기준이다(판정은 상대 기하만 봐서 되돌릴 필요가 없었다).
그리려면 bbox 원점을 더해야 하는데, 안 더해도 **에러가 안 난다** — 골격이 화면 왼쪽 위에
얌전히 그려질 뿐이다. 그 조용한 어긋남을 잡으려고 이 파일이 있다.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.perception_server import _draw_skeleton  # noqa: E402

L_SH, R_SH = 5, 6


def _canvas():
    return np.zeros((480, 640, 3), dtype=np.uint8)


def _two_shoulders(conf_l=1.0, conf_r=1.0):
    """crop 좌표로 어깨 두 점만. (10,10) 과 (60,10)."""
    xy = np.zeros((17, 2), dtype=float)
    xy[L_SH] = (10.0, 10.0)
    xy[R_SH] = (60.0, 10.0)
    conf = np.zeros(17, dtype=float)
    conf[L_SH], conf[R_SH] = conf_l, conf_r
    return xy, conf


GREEN = (0, 255, 0)


def _painted(img, x, y):
    return tuple(int(v) for v in img[y, x]) != (0, 0, 0)


def test_keypoints_land_at_the_bbox_origin_not_the_frame_origin():
    """이 파일의 존재 이유. 원점을 안 더하면 골격이 화면 왼쪽 위에 그려진다."""
    vis = _canvas()
    xy, conf = _two_shoulders()
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)

    assert _painted(vis, 210, 110), "왼쪽 어깨가 bbox 원점만큼 안 옮겨졌다"
    assert _painted(vis, 260, 110), "오른쪽 어깨가 bbox 원점만큼 안 옮겨졌다"
    assert not _painted(vis, 10, 10), "crop 좌표 그대로 그렸다 — 원점을 안 더했다"


def test_low_confidence_points_are_not_drawn():
    """판정이 안 쓰는 점을 화면에만 그리면, Unknown 인데 골격은 멀쩡해 보인다."""
    vis = _canvas()
    xy, conf = _two_shoulders(conf_l=1.0, conf_r=0.1)
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)

    assert _painted(vis, 210, 110), "믿을 만한 점은 그려야 한다"
    assert not _painted(vis, 260, 110), "신뢰도가 낮은 점을 그렸다"


def test_an_edge_needs_both_ends():
    """한쪽 끝이 없는 선을 그리면 없는 관절이 있는 것처럼 보인다."""
    vis = _canvas()
    xy, conf = _two_shoulders(conf_l=1.0, conf_r=0.1)
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)

    # 두 어깨의 중간점 — 선을 그렸다면 여기가 칠해진다.
    assert not _painted(vis, 235, 110), "끝점 하나가 없는데 어깨선을 그렸다"


def test_drawing_nothing_is_not_an_error():
    """전부 신뢰도 미달이어도 죽지 않아야 한다 — 영상이 통째로 멈추는 것보다 낫다."""
    vis = _canvas()
    xy, conf = _two_shoulders(conf_l=0.0, conf_r=0.0)
    _draw_skeleton(vis, (xy, conf, (200, 100)), 0.5, GREEN)
    assert vis.sum() == 0
