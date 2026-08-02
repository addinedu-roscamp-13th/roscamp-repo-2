"""fleet_link 의 순수 함수 테스트 — rclpy 없이 돈다.

ROS 콜백은 이 함수들을 감싸기만 하므로, 캐시에 무엇이 어떻게 접히는지는 여기서 다 잡힌다.
"""
import time

from app import fleet_link


def test_guide_target_is_a_separate_snapshot_field_not_a_fleet_route(monkeypatch):
    """길잡이 Nav2 목표를 순회/배차 routes로 위장하면 안 된다.

    ⚠️ [2026-08-02] 스냅샷 노출 조건이 **살아서 WORKING 인 로봇** 으로 좁혀졌다
    (`_live_guide_targets` — 유령 다이아몬드 방지). 그래서 이 시험도 그 상태를
    만들어 준다. 조건 없이 그냥 실어 보내던 옛 계약으로 되돌리면 아래
    `test_guide_target_vanishes_when_*` 들이 빨개진다.
    """
    monkeypatch.setattr(fleet_link, "_notify", lambda: None)
    with fleet_link._lock:
        old = dict(fleet_link._guide_targets)
        old_robots = dict(fleet_link._robots)
        old_echo = dict(fleet_link._echo)
        fleet_link._guide_targets.clear()
    try:
        fleet_link.apply_robot_state(
            fleet_link._robots,
            {"name": "pinky-3", "location": {"x": 0.0, "y": 0.0, "yaw": 0.0}})
        fleet_link._echo["pinky-3"] = {"state": "WORKING"}
        fleet_link.set_guide_target("pinky-3", {"x": 1.2, "y": -0.4, "name": "화장실"})
        snap = fleet_link.snapshot()
        assert snap["guide_targets"]["pinky-3"] == {"x": 1.2, "y": -0.4, "name": "화장실"}
        assert "pinky-3" not in snap["routes"], "Nav2 목표를 fleet_node 경로로 위장했다"
    finally:
        with fleet_link._lock:
            fleet_link._guide_targets.clear()
            fleet_link._guide_targets.update(old)
            fleet_link._robots.clear(); fleet_link._robots.update(old_robots)
            fleet_link._echo.clear(); fleet_link._echo.update(old_echo)


def test_guide_ownership_ends_when_fsm_leaves_working(monkeypatch):
    """종료 뒤 남은 지도용 target이 순찰 재개를 영구 차단하면 안 된다."""
    monkeypatch.setattr(fleet_link, "_notify", lambda: None)
    monkeypatch.setattr(fleet_link.fsm_link, "snapshot",
                        lambda robot: {"current_state": "WORKING"})
    with fleet_link._lock:
        old = dict(fleet_link._guide_targets)
        fleet_link._guide_targets.clear()
    try:
        fleet_link.set_guide_target("pinky-3", {"x": 1.0, "y": 2.0})
        assert fleet_link.has_guide_target("pinky-3")
        monkeypatch.setattr(fleet_link.fsm_link, "snapshot",
                            lambda robot: {"current_state": "PATROL"})
        assert not fleet_link.has_guide_target("pinky-3")
    finally:
        with fleet_link._lock:
            fleet_link._guide_targets.clear()
            fleet_link._guide_targets.update(old)


def test_task_state_marks_robot_busy_while_running():
    robots, tasks = {}, []
    for state in ("ASSIGNED", "EXECUTING"):
        fleet_link.apply_task_state(robots, tasks, {
            "task_id": "t1", "state": state, "robot_id": "pinky1", "progress": 0.4,
        })
        assert robots["pinky1"]["busy"] is True, state


def test_task_state_clears_busy_when_finished():
    """COMPLETED/FAILED/REJECTED 는 끝난 것이므로 다시 배차 후보가 되어야 한다."""
    robots, tasks = {}, []
    fleet_link.apply_task_state(robots, tasks, {
        "task_id": "t1", "state": "EXECUTING", "robot_id": "pinky1", "progress": 0.5,
    })
    for state in ("COMPLETED", "FAILED", "REJECTED"):
        fleet_link.apply_task_state(robots, tasks, {
            "task_id": "t1", "state": state, "robot_id": "pinky1", "progress": 1.0,
        })
        assert robots["pinky1"]["busy"] is False, state


def test_task_state_without_robot_is_ignored():
    robots, tasks = {}, []
    assert fleet_link.apply_task_state(robots, tasks, {"task_id": "t", "state": "ASSIGNED"}) is None
    assert robots == {} and tasks == []


def test_task_history_is_capped():
    """이력이 무한히 자라면 스냅샷이 매번 커진다 — WebSocket push 마다 실려 나가므로."""
    robots, tasks = {}, []
    for i in range(fleet_link.TASK_HISTORY_MAX + 20):
        fleet_link.apply_task_state(robots, tasks, {
            "task_id": f"t{i}", "state": "ASSIGNED", "robot_id": "pinky1", "progress": 0.0,
        })
    assert len(tasks) == fleet_link.TASK_HISTORY_MAX
    assert tasks[-1]["task_id"] == f"t{fleet_link.TASK_HISTORY_MAX + 19}"


def test_robot_state_records_location():
    robots = {}
    assert fleet_link.apply_robot_state(robots, {
        "name": "pinky2", "location": {"x": 1.5, "y": -2.0},
    }) == "pinky2"
    assert robots["pinky2"]["x"] == 1.5
    assert robots["pinky2"]["y"] == -2.0


def test_robot_state_does_not_import_sim_battery():
    """fleet_node 도 sim 배터리를 무시하고 내부값을 쓴다. 여기서 sim 값을 들이면
    화면 숫자와 배차 판정에 쓰이는 값이 달라진다."""
    robots = {}
    fleet_link.apply_robot_state(robots, {
        "name": "pinky1", "location": {"x": 0, "y": 0}, "battery_percent": 42.0,
    })
    assert "battery" not in robots["pinky1"]


def test_parse_json_map_survives_broken_frames():
    assert fleet_link.parse_json_map('{"3":"pinky1"}') == {"3": "pinky1"}
    assert fleet_link.parse_json_map("not json") == {}
    assert fleet_link.parse_json_map("[1,2]") == {}, "리스트는 맵이 아니다"


def test_rows_mark_unset_state_and_battery_as_unknown():
    """fleet_node 는 상태·배터리를 발행하지 않는다. 설정 전에는 '모른다'로 보여야지,
    기본값을 읽어온 값처럼 보여주면 안 된다."""
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    rows = fleet_link.build_rows(robots, {}, {}, {})
    assert rows[0]["state"] is None
    assert rows[0]["state_source"] == "unknown"
    assert rows[0]["battery_source"] == "unknown"


def test_rows_label_panel_set_values_as_panel_sourced():
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    echo = {"pinky1": {"state": "PATROL", "battery": 55.0}}
    rows = fleet_link.build_rows(robots, echo, {}, {})
    assert rows[0]["state"] == "PATROL"
    assert rows[0]["state_source"] == "panel"
    assert rows[0]["battery"] == 55.0
    assert rows[0]["battery_source"] == "panel"


def test_rows_prefer_live_fsm_state_over_panel():
    """로봇이 실제로 발행한 상태가 정본이다.

    fleet_node 도 on_fsm_state 에서 robot_mode_ 를 무조건 덮어쓴다. 패널 값을 우선하면
    화면엔 PATROL 인데 fleet_node 는 RETURNING 으로 알고 배차를 안 하는, 추적 불가능한
    불일치가 생긴다.
    """
    robots = {"Pinkysim": dict(fleet_link._empty_robot(), name="Pinkysim")}
    echo = {"Pinkysim": {"state": "PATROL", "battery": 55.0}}
    fsm = {"Pinkysim": {"current_state": "RETURNING", "battery_percent": 88.0}}
    rows = fleet_link.build_rows(robots, echo, {}, {}, fsm)
    assert rows[0]["state"] == "RETURNING"
    assert rows[0]["state_source"] == "fsm"
    assert rows[0]["battery"] == 88.0
    assert rows[0]["battery_source"] == "fsm"


def test_rows_fill_state_from_fsm_without_any_panel_setting():
    """회귀 방지 — 예전엔 패널에서 손으로 설정해야만 state 가 찼다. 그래서 로봇이 멀쩡히
    상태를 발행해도 표는 늘 비었고 '배차 가능 0 대'로 보였다."""
    robots = {"Pinkysim": dict(fleet_link._empty_robot(), name="Pinkysim")}
    fsm = {"Pinkysim": {"current_state": "PATROL", "battery_percent": None}}
    rows = fleet_link.build_rows(robots, {}, {}, {}, fsm)
    assert rows[0]["state"] == "PATROL"
    assert rows[0]["state_source"] == "fsm"
    assert rows[0]["battery_source"] == "unknown", "배터리는 안 왔으면 여전히 모른다"


def test_rows_ignore_stale_fsm_and_fall_back_to_panel():
    """수신이 끊긴 FSM 캐시를 현재 상태처럼 보여주면 안 된다."""
    robots = {"Pinkysim": dict(fleet_link._empty_robot(), name="Pinkysim")}
    echo = {"Pinkysim": {"state": "IDLE"}}
    fsm = {"Pinkysim": {"current_state": "WORKING", "stale": True}}
    rows = fleet_link.build_rows(robots, echo, {}, {}, fsm)
    assert rows[0]["state"] == "IDLE"
    assert rows[0]["state_source"] == "panel"


def test_rows_without_fsm_argument_behave_as_before():
    """fsm 인자는 선택이다 — 안 주면 예전 동작 그대로."""
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    rows = fleet_link.build_rows(robots, {"pinky1": {"state": "IDLE"}}, {}, {})
    assert rows[0]["state_source"] == "panel"


def test_rows_include_robots_known_only_from_echo():
    """관측 전에 모드를 밀어 넣은 로봇도 표에 나와야 한다 — 안 그러면 '설정했는데 사라짐'."""
    rows = fleet_link.build_rows({}, {"pinky3": {"state": "IDLE"}}, {}, {})
    assert [r["name"] for r in rows] == ["pinky3"]


def test_rows_attach_goal_and_held_nodes():
    robots = {"pinky1": dict(fleet_link._empty_robot(), name="pinky1")}
    rows = fleet_link.build_rows(
        robots, {}, {"pinky1": 7}, {"3": "pinky1", "4": "pinky2", "5": "pinky1"},
    )
    assert rows[0]["goal_vertex"] == 7
    assert rows[0]["held_nodes"] == [3, 5], "다른 로봇 점유는 섞이지 않는다"


def test_rows_flag_stale_feed():
    # ⚠️ [2026-08-02] 기준이 `_last_ros_at` → **`_last_pose_at`** 으로 바뀌었다.
    #    `_last_ros_at` 은 fleet_node 의 TaskState 발행으로도 갱신돼, 죽은 로봇을
    #    영원히 살아 있게 만들었다(아래 test_fleet_node_task_echo_... 참고).
    fresh = {"a": dict(fleet_link._empty_robot(), name="a", _last_pose_at=time.time())}
    old = {"b": dict(fleet_link._empty_robot(), name="b", _last_pose_at=1.0)}
    assert fleet_link.build_rows(fresh, {}, {}, {})[0]["stale"] is False
    assert fleet_link.build_rows(old, {}, {}, {})[0]["stale"] is True


def test_state_validation_uses_libi_modes_vocabulary():
    for state in ("IDLE", "PATROL", "WORKING", "ERROR", "RETURNING",
                  "CHARGING", "INTERACTING", "SECURITY_PATROL"):
        assert fleet_link.is_valid_state(state), state
    for bad in ("STOP", "CHARGE", "idle", ""):
        assert not fleet_link.is_valid_state(bad), bad


def test_service_calls_fail_closed_without_a_link():
    """링크가 없을 때 조용히 성공한 척하면 안 된다 — 사유를 달고 실패해야 한다."""
    assert fleet_link.submit_task("3")["accepted"] is False
    assert fleet_link.set_robot_mode("pinky1", "IDLE")["ok"] is False
    assert fleet_link.set_battery("pinky1", 50.0)["ok"] is False
    assert fleet_link.set_plugins("", "")["ok"] is False
    assert fleet_link.reload_navgraph()["ok"] is False


def test_bad_mode_is_rejected_before_reaching_the_fleet():
    assert fleet_link.set_robot_mode("pinky1", "STOP")["reason"] == "bad_mode"


# ── 이름 표기가 다를 때 (실측 사고) ──────────────────────────────────────────
#
# FSM 캐시 키는 로봇이 스스로 발행한 robot_id 이고, 배차 표의 이름은 DB(rc_robots.name)
# 에서 온다. 로봇을 `pinky3` 로 띄우고 DB 엔 `Pinky-3` 로 등록해 두면 정확히 일치하지
# 않아 상태가 통째로 비었다 — 관제에 "상태 미상 / 배차 가능 0대".
# 정작 /api/fsm/state 로는 잘 보였다(거기만 표기 차이를 흡수하고 있었다).

def test_rows_find_fsm_entry_despite_notation():
    robots = {"Pinky-3": dict(fleet_link._empty_robot(), name="Pinky-3")}
    fsm = {"pinky3": {"current_state": "IDLE", "battery_percent": 77.0}}
    rows = fleet_link.build_rows(robots, {}, {}, {}, fsm)
    assert rows[0]["state"] == "IDLE"
    assert rows[0]["state_source"] == "fsm"
    assert rows[0]["battery"] == 77.0


def test_rows_prefer_exact_name_over_loose_match():
    """표기 흡수가 **정확히 일치하는 로봇**을 가로채면 안 된다."""
    robots = {"Pinky-3": dict(fleet_link._empty_robot(), name="Pinky-3")}
    fsm = {"pinky3": {"current_state": "ERROR"}, "Pinky-3": {"current_state": "IDLE"}}
    rows = fleet_link.build_rows(robots, {}, {}, {}, fsm)
    assert rows[0]["state"] == "IDLE"


def test_rows_unknown_robot_has_no_fsm_entry():
    robots = {"Pinky-9": dict(fleet_link._empty_robot(), name="Pinky-9")}
    fsm = {"pinky3": {"current_state": "IDLE"}}
    rows = fleet_link.build_rows(robots, {}, {}, {}, fsm)
    assert rows[0]["state"] is None
    assert rows[0]["state_source"] == "unknown"


# ── stale 은 **로봇이 보낸 위치** 기준이다 (2026-08-02) ───────────────────────
#
# `_last_ros_at` 은 `apply_task_state` 도 찍는다. 그런데 TaskState 를 발행하는 것은
# **fleet_node 자신**이다. 로봇이 죽어 위치 보고가 끊겨도 fleet_node 가 그 로봇의
# 순회 task(`P-pinky-3`)를 계속 내보내는 한 도장이 갱신되어 `stale` 이 영원히 False 였다.
#
# 실측 2026-08-02: fleet_node 는 `[WARN] 로봇 상태 끊김(10s 이상): pinky-3` 을 계속 찍는데
# 백엔드 스냅샷은 `stale: false` 였다. 문턱은 양쪽 다 10초로 같았다 —
# 다른 것은 **무엇을 세느냐**였다. 그 사이 프론트는 stale 을 믿고 꺼진 로봇을 계속 그렸다.

def _row(robots, name="pinky-3"):
    return [r for r in fleet_link.build_rows(robots, {}, {}, {}) if r["name"] == name][0]


def _pose(robots, name="pinky-3", x=1.0, y=2.0):
    fleet_link.apply_robot_state(
        robots, {"name": name, "location": {"x": x, "y": y, "yaw": 0.0}})


def test_fresh_pose_is_not_stale():
    robots = {}
    _pose(robots)
    assert _row(robots)["stale"] is False


def test_missing_pose_goes_stale():
    robots = {}
    _pose(robots)
    old = time.time() - (fleet_link.FRESH_SEC + 5)
    robots["pinky-3"]["_last_pose_at"] = old
    robots["pinky-3"]["_last_ros_at"] = old
    assert _row(robots)["stale"] is True


def test_fleet_node_task_echo_does_not_resurrect_a_dead_robot():
    """⚠️ 오늘의 버그. **관제 자신의 발행은 로봇 생존의 증거가 아니다.**"""
    robots = {}
    _pose(robots)
    old = time.time() - (fleet_link.FRESH_SEC + 5)
    robots["pinky-3"]["_last_pose_at"] = old
    robots["pinky-3"]["_last_ros_at"] = old

    # fleet_node 가 이 로봇의 순회 task 를 계속 발행한다 — 로봇은 죽어 있는데.
    fleet_link.apply_task_state(robots, [], {
        "robot_id": "pinky-3", "task_id": "P-pinky-3",
        "state": "EXECUTING", "progress": 0.0})

    row = _row(robots)
    assert row["stale"] is True, "fleet_node 의 task 발행을 로봇 생존으로 오인했다"
    # task 정보 자체는 살아 있어야 한다 — 지우는 게 아니라 **생존 판정에서만** 뺀다.
    assert row["task_state"] == "EXECUTING"


def test_pose_coming_back_clears_stale():
    robots = {}
    _pose(robots)
    robots["pinky-3"]["_last_pose_at"] = time.time() - (fleet_link.FRESH_SEC + 5)
    assert _row(robots)["stale"] is True
    _pose(robots, x=1.1)
    assert _row(robots)["stale"] is False


# ── 길잡이 목표는 **살아서 일하는 로봇의 것만** 보인다 (2026-08-02) ──────────
#
# `set_guide_target` 은 승인 때 넣고 `/release` 때만 뺀다. 안내는 그 길로만 끝나지
# 않는다 — 회복 실패(FAILURE), 로봇 전원 차단, FMS 재기동, 패널 종료. 그때 목표가
# 영원히 남아 지도에 유령 다이아몬드가 뜬다. 해제 훅을 여기저기 다는 대신 표시
# 조건을 사실(살아 있나 · WORKING 인가)에 매단다.

def _guide_snapshot(state, *, stale=False):
    robots = {}
    _pose(robots)
    if stale:
        robots["pinky-3"]["_last_pose_at"] = time.time() - (fleet_link.FRESH_SEC + 5)
    rows = fleet_link.build_rows(robots, {"pinky-3": {"state": state}}, {}, {})
    fleet_link._guide_targets["pinky-3"] = {"x": 1.0, "y": 2.0, "name": "화장실"}
    try:
        return fleet_link._live_guide_targets(rows, {})
    finally:
        fleet_link._guide_targets.clear()


def test_guide_target_shows_while_working_and_alive():
    assert "pinky-3" in _guide_snapshot("WORKING")


def test_guide_target_vanishes_when_the_robot_leaves_working():
    """안내가 어떻게 끝나든 상태가 WORKING 을 벗어난다 — 성공·실패·취소 모두."""
    for state in ("PATROL", "IDLE", "ERROR", "RETURNING", "CHARGING"):
        assert _guide_snapshot(state) == {}, f"{state} 인데 목표가 남았다"


def test_guide_target_vanishes_when_the_robot_dies():
    """전원이 나가면 상태는 WORKING 으로 굳은 채 남는다 — 그때도 지워야 한다."""
    assert _guide_snapshot("WORKING", stale=True) == {}, \
        "꺼진 로봇의 안내 목표가 지도에 남았다"


def test_guide_target_of_an_unknown_robot_is_dropped():
    fleet_link._guide_targets["ghost-9"] = {"x": 0.0, "y": 0.0, "name": "어딘가"}
    try:
        assert fleet_link._live_guide_targets([], {}) == {}
    finally:
        fleet_link._guide_targets.clear()


# ── nav2 실주행 경로 (2026-08-02) ────────────────────────────────────────────
#
# 길잡이는 GuideExec 이 nav2 로 **직접** 몬다. fleet_node 는 그 목적지를 모르므로
# `/fms/routes` 에 안 실린다 — 그래서 관제 지도에 안내 경로가 안 그려지고 **순찰 경로**만
# 떠서 화면이 거짓말을 했다(실측: 화장실로 가는 중인데 순회 경로만 보였다).
# nav2 `/plan` 은 이미 도메인 브릿지에 있고, 어댑터가 이름을 붙여 넘긴다.

def _nav_rows(stale=False):
    robots = {}
    _pose(robots)
    if stale:
        robots["pinky-3"]["_last_pose_at"] = time.time() - (fleet_link.FRESH_SEC + 5)
    return fleet_link.build_rows(robots, {}, {}, {})


def _with_nav_path(pts, *, age=0.0, stale=False, name="pinky-3"):
    fleet_link._nav_paths[name] = pts
    fleet_link._nav_paths_at[name] = time.time() - age
    try:
        return fleet_link._live_nav_paths(_nav_rows(stale=stale))
    finally:
        fleet_link._nav_paths.clear()
        fleet_link._nav_paths_at.clear()


def test_fresh_nav_path_is_published():
    out = _with_nav_path([[0.0, 0.0], [1.0, 1.0]])
    assert out["pinky-3"] == [[0.0, 0.0], [1.0, 1.0]]


def test_stale_nav_path_is_dropped():
    """nav2 는 주행 중 계속 재발행한다 — 오래됐으면 주행이 끝난 것이다."""
    assert _with_nav_path([[0.0, 0.0], [1.0, 1.0]],
                          age=fleet_link.NAV_PATH_FRESH_SEC + 1) == {}


def test_nav_path_of_a_dead_robot_is_dropped():
    assert _with_nav_path([[0.0, 0.0], [1.0, 1.0]], stale=True) == {}, \
        "꺼진 로봇의 주행 경로가 지도에 남았다"


def test_empty_nav_path_is_kept_as_empty():
    """nav2 가 목표를 취소하면 빈 Path 를 낸다 — 그것이 '가는 곳 없음' 이라는 사실이다.
    여기서 빠뜨리면 화면이 **옛 선을 계속 그린다.**"""
    assert _with_nav_path([]) == {"pinky-3": []}


def test_nav_path_is_not_mixed_into_fleet_routes():
    """CBS 순회 경로와 **다른 필드**여야 한다. 섞으면 교통관제 예약처럼 보인다."""
    fleet_link._nav_paths["pinky-3"] = [[0.0, 0.0], [1.0, 1.0]]
    fleet_link._nav_paths_at["pinky-3"] = time.time()
    try:
        assert "pinky-3" not in fleet_link._routes
    finally:
        fleet_link._nav_paths.clear()
        fleet_link._nav_paths_at.clear()


# ── has_guide_target 은 표시 판정과 **같은 정본**을 쓴다 (2026-08-02) ─────────
#
# 예전에는 배제 목록(`state != "PATROL" and ...` 6개)이었다. 상태가 하나 늘면 그것이
# 조용히 "안내 중"으로 분류돼 순회 중계가 영구 차단되고, 화면 표시(_live_guide_targets)는
# 포함 목록을 써서 두 판정이 갈렸다 — 지도엔 목표가 없는데 명령만 막히는 상태.

def _guide_owned(state, *, registered=True, monkeypatch=None):
    if registered:
        fleet_link._guide_targets["pinky-3"] = {"x": 0.0, "y": 0.0, "name": "화장실"}
    try:
        fleet_link.fsm_link.snapshot = lambda r: ({"current_state": state} if state else None)
        return fleet_link.has_guide_target("pinky-3")
    finally:
        fleet_link._guide_targets.clear()


def test_guide_owned_only_while_working():
    assert _guide_owned("WORKING") is True
    for s in ("PATROL", "IDLE", "INTERACTING", "RETURNING", "CHARGING", "ERROR",
              "SECURITY_PATROL"):
        assert _guide_owned(s) is False, f"{s} 인데 안내 소유로 봤다 — 순회가 영구 차단된다"


def test_guide_not_owned_without_a_target():
    assert _guide_owned("WORKING", registered=False) is False


def test_unknown_state_blocks_relay_fail_safe():
    """상태를 못 읽으면 차단 쪽. 그 창의 순회 navigate 가 GuideExec 을 죽이는 게 더 위험하다."""
    assert _guide_owned(None) is True
