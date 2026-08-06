"""라이다 노치 도킹 드라이버 — `/scan` 을 보고 `cmd_vel_dock` 을 낸다.

## 왜 BT tick 이 아니라 자기 타이머인가

BT 는 5Hz 로 돌고 이 로봇은 CPU 부하로 tick 이 밀린다(2026-07-30 실측: nav2 가 제어
주기를 놓칠 정도). tick 에 얹어 발행하면 부하가 튈 때 명령이 0.5초 이상 끊기고,
그러면 twist_mux 가 입력을 버리고 모터 워치독이 로봇을 세운다 — **도킹 거리가 부하에
따라 달라진다.** 같은 이유로 만들어진 `NudgeDriver` 의 구조를 그대로 따른다.

## 왜 `/cmd_vel` 이 아니라 `cmd_vel_dock` 인가

`/cmd_vel` 직접 발행은 twist_mux.yaml 이 금지한다 — 중재를 우회하면 "마지막에 도착한
명령이 이긴다"로 되돌아간다. `dock` 입력(priority 120)으로 보내면 비상정지(255)와
FSM 잠금(160)에는 지고 새어 나온 nav2 목표(50)에는 이긴다. 그게 맞는 서열이다.

## 스캔 콜백이 참조만 잡는 이유

이 구독은 `fsm_node` 에 **처음 생기는 것**이라 상시 비용이 붙는다. 콜백에서 매번
직교 변환을 돌리면 도킹을 안 하는 동안에도 초당 수천 번 파이썬 루프를 돈다
(`libi_perception/scan_provider.py` 가 정확히 그 문제로 코어의 14%를 먹었다).
그래서 콜백은 참조만 잡고, 검출은 도킹 중 타이머에서만 한다.

⚠️ 구독 자체는 만들고 없애지 않는다. 콜백 안 엔티티 생성·소멸은 실행기 상태를 흔든다.
"""
from __future__ import annotations

import time
from dataclasses import replace

from libi_modes.lidar.approach import LidarApproach
from libi_modes.lidar.config import LidarDockConfig
from libi_modes.lidar.detect import (detect, detect_near, fit_wall_near_bearing,
                                     min_range_m, sector_points)

#: 끝날 때 낼 0 의 **개수**. 시각이 아니라 개수라야 콜백 지연에 안 샌다
#: (`NudgeDriver` 가 같은 이유로 개수를 쓴다 — 시각으로 재면 밀린 콜백이 0 을
#:  한 번도 안 내고 빠져나간다).
ZERO_TICKS = 5


class NoopDriver:
    """아무 일도 하지 않는 드라이버. 절대 끝나지 않는다.

    라이다 경로에서 `BackCamOn` 에 주입한다. 라이다 도킹에는 뒷캠이 필요 없는데
    그 leaf 가 그대로 돌면 `camera_sender` 가 뒷캠을 1.9Hz → 15fps 로 올려 놓는다 —
    쓰지도 않는 캠에 Pi CPU 를 태운다.

    ⚠️ `poll()` 이 `"running"` 이어야 한다. `BackCamOn` 은 `Parallel(SuccessOnOne)`
       의 자식이라 SUCCESS 를 내면 그 순간 복귀가 통째로 끝나고, FAILURE 는 정책과
       무관하게 Parallel 을 죽인다.
    """

    def start(self) -> None:
        pass

    def poll(self) -> str:
        return "running"

    def stop(self) -> None:
        pass


class LidarDockDriver:
    """`DriverAction` 계약(`start`/`poll`/`stop`)을 만족하는 라이다 도킹 실행기."""

    def __init__(self, node, scan_topic: str, cmd_topic: str,
                 cfg: LidarDockConfig, rate_hz: float = 10.0, now=time.monotonic):
        from geometry_msgs.msg import Twist
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import LaserScan

        self._Twist = Twist
        self._cfg = cfg
        # SEARCH 국면 전용 — 사전 회전이 부정확해도 벽을 찾도록 창을 넓힌다
        # (config.py 의 "탐색" 섹션 참고). 매 tick 새로 만들지 않고 한 번만 만든다.
        self._search_cfg = replace(cfg, sector_half_deg=cfg.search_half_deg,
                                   wall_yaw_max_rad=cfg.search_wall_yaw_max_rad)
        self._now = now
        self._log = node.get_logger()
        self._pub = node.create_publisher(Twist, cmd_topic, 10)
        node.create_subscription(LaserScan, scan_topic, self._on_scan,
                                 qos_profile_sensor_data)
        node.create_timer(1.0 / float(rate_hz), self._tick)

        self._msg = None            # 콜백은 참조만 잡는다 (머리말 참고)
        self._msg_at = None
        self._started_at = None
        self._active = False
        self._result = "running"
        self._machine: LidarApproach | None = None
        self._zeroed = 0
        self._last_phase = None

    # ── ROS 콜백 ──────────────────────────────────────────────────────────
    def _on_scan(self, msg) -> None:
        self._msg = msg
        self._msg_at = self._now()

    def _publish(self, lin: float, ang: float) -> None:
        """⚠️ 전진을 여기서 막는다. 상태기계가 틀려도 도크를 들이받지 않게."""
        t = self._Twist()
        t.linear.x = min(float(lin), 0.0)
        t.angular.z = float(ang)
        self._pub.publish(t)

    def _finish(self, result: str, reason: str) -> None:
        for _ in range(ZERO_TICKS):
            self._publish(0.0, 0.0)
        self._active = False
        self._result = result
        self._machine = None
        self._log.info(f"라이다 도킹 종료: {result} ({reason})")

    # ── DriverAction 계약 ─────────────────────────────────────────────────
    def start(self) -> None:
        # ⚠️ **이전 시도의 스캔을 지운다** (codex P1 #4). 안 지우면 재시도가 몇 초 전
        #    프레임으로 즉시 ACQUIRE 를 시작한다 — 그 사이 로봇은 움직였다.
        self._msg = None
        self._msg_at = None
        # ⚠️ [2026-08-03 실기] 위에서 `_msg_at` 을 지우자마자 첫 tick 이 오면
        #    "스캔이 없다"가 즉시 참이 돼 콜백이 단 한 번도 못 온 채로 scan_timeout
        #    이 난다 — 실측: 재시작 직후 3연속 26~52ms 만에 실패. 콜백은 10Hz 로
        #    계속 도는데, tick 위상이 콜백보다 먼저 오면 지는 경합이었다.
        #    `_started_at` 을 따로 둬서 "시작 직후 scan_timeout_s 동안은 아직
        #    한 번도 안 왔어도 봐준다"를 명시적으로 만든다.
        self._started_at = self._now()
        self._machine = LidarApproach(self._cfg)
        self._active = True
        self._result = "running"
        self._zeroed = 0
        self._last_phase = None
        self._log.info(
            f"라이다 도킹 시작: stop={self._cfg.stop_m:.3f}m "
            f"steer_sign={self._cfg.steer_sign:+.0f} "
            f"섹터=±{self._cfg.sector_half_deg:.0f}도")

    def poll(self) -> str:
        return self._result

    def stop(self) -> None:
        if self._active:
            self._finish("failure", "cancelled")
        else:
            # 시작 전이거나 이미 끝났어도 0 을 낸다 — 중복 정지는 안전하다.
            for _ in range(ZERO_TICKS):
                self._publish(0.0, 0.0)

    # ── 제어 주기 ─────────────────────────────────────────────────────────
    def _tick(self) -> None:
        if not self._active or self._machine is None:
            return                          # 안 도는 동안은 침묵 — 입력 없음으로 친다
        now = self._now()

        if self._msg_at is None:
            # 시작 직후 그레이스 구간 — 콜백이 아직 한 번도 안 왔어도 곧바로
            # 실패시키지 않는다. 그레이스가 지났는데도 안 왔으면 진짜 문제다.
            if now - self._started_at > self._cfg.scan_timeout_s:
                self._finish("failure", "scan_timeout")
            return
        if now - self._msg_at > self._cfg.scan_timeout_s:
            self._finish("failure", "scan_timeout")
            return

        # ⚠️ **try/finally 로 감싼다** (codex P1 #5-추가). 여기서 예외가 나면 timer
        #    callback 만 죽고 마지막 **비영(非零) 명령이 그대로 남는다** — twist_mux
        #    (0.5s)·모터 워치독(0.5s)까지 계속 밀린다. 0.05m/s 면 2.5cm 다.
        msg = self._msg
        try:
            # ⚠️ [2026-08-04 실기 정정] SEARCH 국면에서는 `detect()`(노치까지
            #    확정)를 안 부른다 — 각도가 심하게 틀어지면 벽은 잡혀도 노치
            #    자체가 그 각도에서 인식이 안 돼(라이다가 노치 바닥 대신 옆벽을
            #    스치거나 깊이·폭이 찌그러져 찍힘) `obs` 가 계속 `None` 이라
            #    SEARCH 가 방향 신호를 영영 못 받았다. 벽만 넓힌 cfg 로 찾아
            #    `search_wall_yaw` 로 넘긴다 — 자세한 이유는 approach.py 의
            #    SEARCH 블록 주석.
            # ⚠️ [2026-08-04 실측 추가] `fit_wall` 이 아니라 `fit_wall_near_bearing`
            #    이다 — inlier 최다만 보면 도크와 무관한 방의 다른 벽을 우선할
            #    수 있다(실측: 200점 옆벽이 110점 도크벽을 이김, codex P1 확인).
            if self._machine.phase == "SEARCH":
                pts = sector_points(msg.ranges, msg.angle_min, msg.angle_increment,
                                    msg.range_min, self._search_cfg.range_max_m,
                                    self._search_cfg)
                wall = fit_wall_near_bearing(pts, self._search_cfg) if len(pts) else None
                # SEARCH에서 크게 틀어진 동안에는 벽 정렬만 한다. 그러나 벽이
                # 이미 정렬 허용치 안이면 이 프레임의 노치를 즉시 첫 확정 표본으로
                # 넘긴다. 연속 확인 전에는 여전히 0 속도다.
                search_obs = None
                if wall is not None and abs(wall.yaw) <= self._cfg.search_align_tol_rad:
                    search_obs = detect(msg.ranges, msg.angle_min, msg.angle_increment,
                                        msg.range_min, msg.range_max, self._cfg)
                cmd = self._machine.step(search_obs, now,
                                         search_wall_yaw=(wall.yaw if wall else None))
            else:
                obs = detect(msg.ranges, msg.angle_min, msg.angle_increment,
                             msg.range_min, msg.range_max, self._cfg)
                # FAR 가 실패하고 이미 FINAL 이면 NEAR 로 인수인계한다.
                # ⚠️ **FINAL 뿐이다 — APPROACH 는 아니다.** NEAR 의 `y` 는 근거리에서
                #    노치 가장자리가 창 밖으로 잘려 오차가 커진다(실측: 좌우 2cm→12mm,
                #    3cm→21mm). APPROACH 에서 받아들이면 그 값이 FINAL 진입 정렬 문턱
                #    (`y_max_enter_final_m`, ≤15mm)을 오염시킨다. ACQUIRE·ALIGN·APPROACH
                #    에서는 절대 NEAR 를 부르지 않는다 — 상태기계가 다시 한 번 막지만
                #    (near 관측 무시), 여기서도 안 부르는 것이 명시적이다.
                if obs is None and self._machine.phase == "FINAL":
                    obs = detect_near(msg.ranges, msg.angle_min, msg.angle_increment,
                                      msg.range_min, msg.range_max, self._cfg)
                # ⚠️ [2026-08-04, 사용자 요청] 노치 검출과 완전히 별개로, 원시
                #    최소거리를 매 tick 재서 안전 정지에 넘긴다 — `obs`/`d` 가
                #    틀려도 이건 라이다가 잰 그대로다(approach.py 안전 정지 참고).
                mr = min_range_m(msg.ranges, msg.angle_min, msg.angle_increment,
                                 msg.range_min, msg.range_max, self._cfg)
                cmd = self._machine.step(obs, now, min_range_m=mr)
        except Exception as exc:                                   # noqa: BLE001
            self._log.error(f"라이다 도킹 예외 — 정지한다: {exc}")
            self._finish("failure", "exception")
            return
        self._publish(cmd.linear, cmd.angular)
        if cmd.phase != self._last_phase:
            self._last_phase = cmd.phase
            self._log.info(f"라이다 도킹 국면: {cmd.phase} ({cmd.reason})")
        if cmd.done:
            self._finish("success" if cmd.phase == "DONE" else "failure", cmd.reason)
