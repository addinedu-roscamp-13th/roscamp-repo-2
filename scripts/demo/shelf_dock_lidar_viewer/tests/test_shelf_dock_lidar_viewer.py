"""실물 ROS 그래프 없이 도킹 설명 화면의 핵심 오버레이를 검증한다."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[4]
VIEWER = ROOT / "scripts/demo/shelf_dock_lidar_viewer/shelf_dock_lidar_viewer.py"
SPEC = importlib.util.spec_from_file_location("shelf_dock_lidar_viewer", VIEWER)
assert SPEC and SPEC.loader
viewer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = viewer
SPEC.loader.exec_module(viewer)


def test_render_draws_pgm_ray_stop_point_and_live_lidar() -> None:
    cells = np.zeros((20, 20), dtype=np.int16)
    cells[:, 14:] = 100
    fake = SimpleNamespace(
        map_data=viewer.MapData(cells=cells, resolution=0.05, origin_x=0.0,
                                origin_y=0.0, received_at=__import__("time").monotonic()),
        pose=(0.20, 0.50, 0.0),
        scan_data=viewer.ScanData(
            ranges=np.array([0.20, 0.35, 0.50], dtype=np.float32),
            angles=np.array([-0.20, 0.0, 0.20], dtype=np.float32),
            range_min=0.02, range_max=5.0, received_at=__import__("time").monotonic()),
        status={"event": "shelf_dock", "phase": "final_progress",
                "pgm_distance_m": 0.50, "clearance_m": 0.02,
                "remaining_to_clearance_m": 0.48, "ray_yaw_rad": 0.0},
    )
    args = SimpleNamespace(robot="pinky-3", range_m=1.20, front_half_angle_deg=15.0)

    image = viewer.render(fake, args)

    assert image.shape == (720, 1280, 3)
    assert np.any(np.all(image == viewer.RAY_CYAN, axis=2))
    assert np.any(np.all(image == viewer.STOP_YELLOW, axis=2))
    assert np.any(np.all(image == viewer.LIDAR_GREEN, axis=2))


def test_age_label_marks_missing_data_as_waiting() -> None:
    assert viewer.age_label(None) == "waiting"
