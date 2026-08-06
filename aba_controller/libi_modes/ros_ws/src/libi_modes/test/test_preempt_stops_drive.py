"""상태가 바뀌면 **진행 중이던 주행이 멈춰야** 한다.

실측 신고(2026-07-28): "응대중인데 계속 바퀴가 움직임". 순회 중 방문객이 패널을
만져 INTERACTING 으로 넘어갔는데, 순회가 내보낸 주행 목표를 아무도 안 끊어서
화면은 '응대중'인데 로봇은 다음 순회 정점으로 계속 갔다.

여기서 보는 것은 **BT 층까지**다 — 선점된 액션 leaf 가 `driver.stop()` 을 부르는가.
그 stop 이 실제로 nav2 목표를 취소하는지는 실행 층(robot_agent)의 몫이고 별개다.
"""
import py_trees
import pytest
from py_trees.common import Status

from libi_modes import tree
from libi_modes.blackboard import Keys
from test.fakes import PARAMS, FakeDriver, all_drivers, all_providers

# 순회도 배달과 같은 실행 경로다 — fleet_node 가 허가한 노드가 `navigate` 로 내려와야
# `PatrolNavigation` 이 주행을 낸다(navigation_actions.py 머리말). 목적지 없이 PATROL
# 이기만 하면 그 리프는 idle 로 FAILURE 다.
DRIVING = {Keys.ACTIVE_COMMAND: "navigate",
           Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
           Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}}


@pytest.fixture
def built():
    """(bt, drivers) — 트리를 세우고 드라이버를 들여다볼 수 있게 돌려준다."""
    drivers = all_drivers()
    root = tree.build_root(PARAMS, drivers, all_providers())
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    return bt, drivers, root


def _running_names(node, out=None):
    out = [] if out is None else out
    if node.status == Status.RUNNING:
        out.append(node.name)
    for c in getattr(node, "children", []):
        _running_names(c, out)
    return out


def test_patrol_starts_the_drive(built, seed):
    """전제 확인 — 이게 깨지면 아래 테스트가 다른 이유로 통과한다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert drivers["patrol"].started


def test_interacting_preempts_and_stops_patrol_drive(built, seed):
    """이 파일의 존재 이유. 선점만 하고 안 멈추면 로봇이 계속 굴러간다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert drivers["patrol"].started, "전제: 순회가 주행을 냈다"

    seed(**DRIVING, **{Keys.CURRENT_MODE: "INTERACTING"})   # 방문객이 패널을 만졌다
    bt.tick()

    assert drivers["patrol"].stopped, (
        "INTERACTING 이 순회를 선점했는데 driver.stop() 이 안 불렸다 — "
        "화면은 '응대중'인데 바퀴는 계속 돈다")


def test_interacting_branch_actually_runs(built, seed):
    """선점이 진짜로 일어났는지."""
    bt, _, root = built
    seed(**{Keys.CURRENT_MODE: "INTERACTING"})
    bt.tick()
    names = _running_names(root)
    assert "InteractingBranch" in names, f"RUNNING: {names}"


def test_working_preempts_and_stops_patrol_drive(built, seed):
    """작업 배차도 같다 — 순회 목표가 살아 있으면 배달 목표와 다툰다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert drivers["patrol"].started

    seed(**DRIVING, **{Keys.CURRENT_MODE: "WORKING"})
    bt.tick()
    assert drivers["patrol"].stopped


def test_leaving_working_stops_the_follow_session(built, seed):
    """추종 종료 버튼이 실제로 세션을 끄는가 — 화면에서 의심이 나온 자리다.

    패널 「해제」는 로봇을 직접 안 멈춘다. FMS 에 해제를 보고하고, FMS 가 로봇을 IDLE 로
    옮긴다(admin_follow.py RELEASE_STATE). 세션을 실제로 끊는 건 그 전이에 선점된
    `FollowExec.terminate(INVALID)` 뿐이다 — 여기가 끊기면 미션 BT 만 빠져나오고
    libi_perception 의 제어 루프는 계속 20Hz 로 `/cmd_vel` 을 민다.

    실측 2026-07-28: 관제 BT 화면에서 `FollowExec` 은 회색인데 그 밑 `Following[TRACKING]`
    이 파란색으로 남아 있었다.
    """
    bt, drivers, _ = built
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()
    assert drivers["follow"].started, "전제: 추종 세션이 열렸다"

    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()

    assert drivers["follow"].stopped, (
        "WORKING 을 벗어났는데 추종 driver.stop() 이 안 불렸다 — "
        "화면은 대기인데 제어 루프는 계속 돈다")


def test_a_new_command_stops_the_follow_session_it_replaces(built, seed):
    """명령이 바뀌어도 옛 세션은 닫혀야 한다 — 상태 전이만 챙기면 이게 새어 나간다.

    실측 2026-07-28, `/fleet_cmd` echo:

        follow_admin-1-...        ← 추종 시작
        (20초 뒤) navigate ...    ← 관제가 다음 주행을 배차
        follow_admin-2-...        ← 두 번째 추종
        ... `stop` 은 단 한 번도 없다

    `navigate` 가 `active_command` 를 덮으면 `FollowExec` 은 그저 FAILURE 를 돌려주고
    빠진다(`CommandDrivenAction.update` 의 첫 분기). 그건 상태 전이가 아니라 **명령
    교체**라 `terminate(INVALID)` 도 안 탄다 — 아무도 `driver.stop()` 을 안 부른다.
    libi_perception 의 제어 루프는 계속 20Hz 로 `/cmd_vel` 을 밀고,
    `/libi/follow_bt_snapshot` 은 `Following[TRACKING]` 을 영원히 내보낸다.
    """
    bt, drivers, _ = built
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()
    assert drivers["follow"].started, "전제: 추종 세션이 열렸다"

    seed(**DRIVING, **{Keys.CURRENT_MODE: "WORKING"})   # 관제가 주행을 배차했다
    bt.tick()

    assert drivers["follow"].stopped, (
        "새 명령이 추종을 밀어냈는데 driver.stop() 이 안 불렸다 — "
        "옛 추종 루프가 nav2 와 같이 /cmd_vel 을 민다")


def test_follow_stops_even_when_nobody_claims_the_new_command(built, seed):
    """가로채는 사람이 없어도 옛 세션은 닫혀야 한다 — 실측 로그가 바로 이 경우다.

    앞 시험이 통과하는 건 `NavigationExec` 이 Selector 에서 앞에 있어 py_trees 가 뒤의
    `FollowExec` 을 `INVALID` 로 무효화해 주기 때문이다. 그 보호는 **누군가 가로챌 때만**
    작동한다. 좌표 없는 `navigate` 처럼 아무도 못 집는 명령이 오면 `FollowExec` 은 제
    "내 명령 아님" 분기로 조용히 빠지고, 세션은 그대로 남는다.
    """
    bt, drivers, _ = built
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()
    assert drivers["follow"].started

    # 좌표가 없다 — NavigationExec 도 못 집는다.
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    bt.tick()

    assert drivers["follow"].stopped, (
        "아무도 안 집는 명령이 왔는데 추종이 손만 놨다 — 제어 루프가 계속 돈다")


def test_a_new_command_stops_the_drive_it_replaces(built, seed):
    """주행도 같다 — 남으면 옛 nav2 목표가 새 명령과 다툰다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "WORKING"})
    bt.tick()
    assert drivers["nav"].started

    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()

    assert drivers["nav"].stopped


def test_stop_not_called_while_patrol_keeps_running(built, seed):
    """멀쩡히 도는 중에 stop 을 부르면 순회가 매 tick 끊긴다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    for _ in range(5):
        bt.tick()
    assert not drivers["patrol"].stopped


def test_arriving_then_going_idle_still_cancels_the_nav_goal(built, seed):
    """도착한 뒤 IDLE 로 가면 nav2 목표를 끊어야 한다.

    실측 신고 2026-07-28: "대기상태인데 움직이는 경우도 있었어".

    도착 판정은 **내 pose 가 5cm 안에 들어왔나**만 본다(arrive_tolerance_m). nav2 는
    같은 순간 아직 자기 goal 을 들고 최종 접근·목표 회전 중일 수 있다. 그런데 도착이
    `_started` 를 내려버려서, 뒤이은 `terminate(INVALID)` 가 `_started` 가드에 걸려
    **stop 을 안 보냈다.** 화면은 대기인데 바퀴는 돌았다.
    """
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "WORKING"})
    bt.tick()
    assert drivers["nav"].started, "전제: 주행을 냈다"

    # 목적지에 도착했다 — pose 가 목표와 같아진다.
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate",
            Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
            Keys.ROBOT_POSE: {"x": 1.0, "y": 0.0}})
    bt.tick()
    assert not drivers["nav"].stopped, (
        "도착만으로 끊으면 순회가 노드마다 죽은 듯 선다 — 끊는 건 진짜 끝날 때다")

    seed(**{Keys.CURRENT_MODE: "IDLE"})     # 관제가 세웠다
    bt.tick()
    assert drivers["nav"].stopped, (
        "IDLE 로 갔는데 nav2 목표를 안 끊었다 — 화면은 대기인데 바퀴가 돈다")


def test_patrol_arrival_does_not_cancel_the_drive(built, seed):
    """순회는 도착해도 안 끊는다 — 끊으면 다음 노드 허가까지 서 있는다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert drivers["patrol"].started

    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.ACTIVE_COMMAND: "navigate",
            Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
            Keys.ROBOT_POSE: {"x": 1.0, "y": 0.0}})
    bt.tick()
    assert not drivers["patrol"].stopped


def test_a_failed_drive_is_cancelled_right_away(seed):
    """실패한 주행은 **즉시** 끊는다 — 도착과 다르다.

    codex 감사(2026-07-28)에서 나온 결함: `_goal_outstanding` 이 실패 경로에서 안
    내려가 leaf 재진입 뒤까지 살아남는다. 그러면 나중의 `terminate(INVALID)` 가
    **형제가 방금 낸 목표**를 대신 취소한다 — `stop` 은 id 별이 아니라 실행 층의
    현재 활성 goal 을 통째로 취소하기 때문이다(fleet_link 의 `stop` 분기).

    도착은 nav2 도 같은 목표에 닿았으니 최종 접근을 마치도록 두지만(순회가 노드마다
    서는 것을 막는다), 실패는 "못 갔다"는 뜻이라 목표가 확실히 stale 하다.
    """
    from libi_modes.common.working_actions import NavigationExec

    # 실행 층이 거부한다 — 첫 poll 에서 failure.
    drv = FakeDriver(["failure"])
    clock = [100.0]
    leaf = NavigationExec(drv, arrive_tolerance=0.1, arrive_resend_sec=10,
                          arrive_timeout_sec=60, now_fn=lambda: clock[0],
                          recovery_retry_max=0)
    seed(**DRIVING)
    leaf.setup()
    leaf.initialise()

    assert leaf.update() == Status.RUNNING      # goal 발행
    assert drv.started and not drv.stopped

    clock[0] += 1.0                             # 재전송 주기 전 → poll() 분기로 간다
    assert leaf.update() == Status.FAILURE
    assert drv.stopped, "주행이 실패했는데 목표를 안 끊었다 — 다음 명령과 /cmd_vel 을 다툰다"
