"""라이다 도킹 설정.

`clamped()` 가 하는 일은 하나다: **현장에서 잘못 적은 값이 로봇을 도크에 박게 두지
않는 것.** params.yaml 은 사람이 손으로 고치는 파일이고, 여기 들어오는 값 중 몇 개는
물리적으로 하한이 있다(모터 데드밴드 등).
"""
from libi_modes.lidar.config import LidarDockConfig


def test_defaults_are_physically_sane():
    c = LidarDockConfig()
    assert c.v_near_mps == 0.05, "실측 모터 데드밴드. 이 아래로는 바퀴가 안 돈다"
    assert c.v_far_mps > c.v_near_mps
    assert c.pulse_dist_m < c.v_far_dist_m
    assert c.notch_depth_min_m < 0.025 < c.notch_depth_max_m
    # SEARCH 가 ACQUIRE 로 넘긴 바로 다음 tick 에 좁은 창의 detect() 도 곧장
    # 성공해야 한다 — 안 그러면 넘기자마자 다시 실패로 튕긴다.
    assert c.search_align_tol_rad < c.wall_yaw_max_rad
    # ★ 현장 실측(2026-08-04) — 노치 y 오차가 벽 yaw 에 거의 선형 비례했다
    #   (상관계수 0.93, 1도당 약 2.4mm). 0.4(22.9도)는 너무 느슨했다.
    assert c.search_align_tol_rad == 0.15
    # `fit_wall_near_bearing` 전용 — bearing 은 좁게, 거리는 최소<최대가 성립해야 한다.
    assert c.search_wall_dist_min_m < c.search_wall_dist_max_m
    assert 0.0 < c.search_bearing_tol_rad < c.search_wall_yaw_max_rad, (
        "bearing 문턱은 좁게, 벽 자체 기울기 허용치(search_wall_yaw_max_rad)는 "
        "여전히 넓게 — 이 둘이 뒤바뀌면 안 된다")
    # ★ 현장 실측(2026-08-04, 두 번째 결함) — 넓은 섹터(175도)라 n 이 커져서
    #   ransac_min_inlier_ratio×n 문턱을 실제 도크 벽(87점)이 못 넘었다. 섹터
    #   폭에 안 흔들리는 절대 개수를 쓴다.
    assert c.search_wall_min_inliers > 0
    assert c.search_wall_min_inliers < c.min_points * 5, (
        "너무 크면 좁은 섹터에서도 못 넘는다 — min_points 대비 상식적인 범위인지만 본다")
    # ★ 현장 실측(2026-08-04) — y 오차가 yaw 에 비례하므로 FINAL 진입 게이트도
    #   yaw 를 봐야 한다(y 만 보고 통과시키면 안 된다). 처음엔 SEARCH 와 같은
    #   값(8.6도)을 썼는데, 실기에서 FINAL 진입 뒤엔 yaw 를 못 고친다는 게
    #   드러났다(k_yaw_final=0, 벽이 사라져 애초에 못 잰다 — 6.2°로 들어가서
    #   16.4°까지 더 벌어진 채 도착) — SEARCH 의 대략적 정렬(제자리 회전용)
    #   보다 훨씬 좁혀야 한다.
    assert c.yaw_max_enter_final_rad < c.search_align_tol_rad, (
        "FINAL 은 진입 뒤 교정 수단이 없다 — SEARCH 의 대략적 정렬보다 훨씬 좁아야 한다")


def test_clamped_raises_search_rotation_up_to_the_rotation_deadband():
    """정지 상태 제자리 회전은 0.16 rad/s 아래로 안 돈다(approach.py SEARCH 참고)."""
    c = LidarDockConfig(search_rot_rad_s=0.01).clamped()
    assert c.search_rot_rad_s == 0.16


def test_clamped_raises_speed_up_to_the_motor_deadband():
    """0.05 아래로 적으면 명령은 20Hz 로 나가는데 바퀴가 안 돈다 — 조용히 안 움직인다."""
    c = LidarDockConfig(v_near_mps=0.01, v_far_mps=0.02).clamped()
    assert c.v_near_mps == 0.05
    assert c.v_far_mps >= c.v_near_mps


def test_clamped_keeps_pulse_length_above_the_minimum_step():
    c = LidarDockConfig(pulse_min_s=0.0).clamped()
    assert c.pulse_min_s >= 0.10


def test_clamped_forces_final_yaw_gain_to_zero():
    """최종 국면에는 벽이 min range 아래로 사라져 yaw 를 잴 수 없다.
    죽은 값을 믿는 것은 안 보는 것보다 나쁘다."""
    c = LidarDockConfig(k_yaw_final=5.0).clamped()
    assert c.k_yaw_final == 0.0


def test_from_params_ignores_none_and_unknown_keys():
    c = LidarDockConfig.from_params({"stop_m": 0.07, "k_y": None, "무슨키": 1})
    assert c.stop_m == 0.07
    assert c.k_y == LidarDockConfig().k_y


def test_shipped_params_yaml_builds_a_valid_config():
    """params.yaml 에 오타가 있으면 기본값이 조용히 쓰인다 — 여기서 드러낸다."""
    import pathlib
    import yaml
    from libi_modes.lidar.config import LidarDockConfig

    text = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "config/params.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    # ⚠️ 이 파일의 최상위 키는 `libi_modes:` 다(`/**`/`ros__parameters` 아님) —
    #    main.py._load_params 가 `yaml.safe_load(f)["libi_modes"]` 로 읽는 것과 같다.
    node = doc.get("libi_modes", doc)
    node = node.get("/**", node)
    params = node.get("ros__parameters", node)
    ret = params["returning"]

    assert ret["dock_sensor"] in ("aruco", "lidar")
    # ⑤는 ArUco 용 값 그대로 둔다 — 라이다일 때 0 은 main.py 가 강제한다(codex P0 #7).
    assert ret["nudge_distance_m"] > 0.0

    # ⚠️ **오타를 실제로 잡는다** (codex P1 #6). `from_params` 는 모르는 키를 조용히
    #    버리므로, 키 이름을 한 자만 틀려도 기본값이 쓰이고 아무 증상이 안 난다.
    seen = []
    cfg = LidarDockConfig.from_params(ret["lidar_dock"], on_unknown=seen.append)
    assert seen == [], f"params.yaml 에 모르는 키가 있다(오타?): {seen}"
    # ⚠️ `>` 가 아니라 `>=` 다 — 현장 실측값이 정확히 C1 min range(5cm)와 같다
    #    (라이다 원점↔로봇 뒷면 4.5cm + 여유 0.5cm = 5.0cm, 2026-08-03 pinky-3 실측).
    #    엄격한 `>` 였으면 이 값 자체가 시험을 통과 못 했을 것이다.
    assert cfg.stop_m >= 0.05, "C1 min range 아래는 측정 불가다"
    assert cfg.v_near_mps == 0.05
    assert cfg.ema_coef == 0.5


def test_typo_in_params_is_reported_not_swallowed():
    from libi_modes.lidar.config import LidarDockConfig
    seen = []
    cfg = LidarDockConfig.from_params({"stop_mm": 0.07}, on_unknown=seen.append)
    # from_params 는 unknown 키를 모아 on_unknown 을 **한 번**, 리스트로 부른다
    # (config.py 자체 docstring: "노드는 로거를, 시험은 리스트를 넘긴다"). 그래서
    # seen.append 를 쓰면 seen 은 [["stop_mm"]] 이지 ["stop_mm"] 이 아니다 — brief
    # 원문은 키마다 개별 호출된다고 가정했으나 실제 구현(Task 2, 이미 커밋됨)과
    # 안 맞아 이 형태로 고쳤다(task-8-report.md 참고).
    assert seen == [["stop_mm"]]
    assert cfg.stop_m == LidarDockConfig().stop_m     # 기본값이 쓰였다는 사실도 확인


def test_shipped_params_yaml_top_level_key_is_libi_modes():
    """위 시험의 경로 탐색이 실제로 옳은 키를 짚는지 확인한다.

    brief 원문의 탐색 로직(`doc.get("/**", doc)` → `.get("ros__parameters", node)`)은
    `/**`/`ros__parameters` 감싸기가 있는 launch-time 파라미터 파일을 가정한다. 이
    저장소의 params.yaml 은 최상위 키가 그냥 `libi_modes:` 라(main.py:433
    `yaml.safe_load(f)["libi_modes"]`), 그 로직 그대로 쓰면 `params["returning"]` 에서
    KeyError 가 난다 — brief Step 3 가 "확인 명령이 KeyError 나면 파일의 실제 구조에
    맞춰 경로를 고친다"고 명시한 바로 그 상황. 이 시험은 그 구조 가정 자체가 맞는지
    (그리고 앞으로도 깨지면 바로 드러나는지) 고정한다.
    """
    import pathlib
    import yaml

    text = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "config/params.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert "/**" not in doc, "이 파일이 /** 래핑으로 바뀌면 위 시험의 경로 탐색도 고쳐야 한다"
    assert "libi_modes" in doc
    assert "returning" in doc["libi_modes"]
