#!/usr/bin/env python3
"""가짜 주행 로봇 N대 — fleet_node 를 실제로 물려 CBS 교통관제를 시험한다.

## 왜 Gazebo 를 안 쓰나

검증 대상은 **교통관제가 로봇을 제때 세우고 보내는가**지 물리엔진이 아니다.
Gazebo 를 로봇 수만큼 띄우면(도메인 분리 + GZ_PARTITION) 기동만 몇 분이고, 실패하면
그게 물리 문제인지 관제 문제인지 갈라내기 어렵다. 여기서는 nav2 자리에 **운동학 모델**을
놓아, fleet_node·CbsTraffic·navgraph 는 실물과 **완전히 같은 코드**로 돌린다.

    fleet_node ──/robot_path_requests(PathRequest)──▶ 이 스크립트(로봇 대역)
        ▲                                                    │
        └──────────────/robot_state(RobotState)──────────────┘

즉 robot_state_adapter + nav2 + 로봇을 합친 자리를 대신한다.

## 운동 모델

실물 nav2 파라미터를 그대로 쓴다(pinky_navigation/params/nav2_params.yaml):
    desired_linear_vel 0.07 m/s, rotate_to_heading_angular_vel 0.15 rad/s,
    rotate_to_heading_min_angle 0.35 rad
목표 방향과의 각 오차가 min_angle 을 넘으면 **먼저 제자리 회전**하고, 그 뒤 직진한다.
CbsTraffic 의 시간 모델과 같은 가정이라, 계획과 실행이 어긋나면 그건 모델 탓이 아니라
관제 탓이다.

## 지연 주입

`--delay <로봇>:<시작초>:<지속초>` 로 특정 로봇을 특정 시간 동안 멈춰 세운다.
장애물을 만나 늦는 상황이고, **재계획이 실제로 도는지** 보려면 이게 필요하다.

    ./sim_robots.py --robots Pinky-1:0 Pinky-2:15 --delay Pinky-1:12:20
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from libi_fleet_msgs.msg import TaskState
from rmf_fleet_msgs.msg import Location, PathRequest, RobotMode, RobotState
from std_msgs.msg import String

# 실물 nav2 파라미터 (nav2_params.yaml)
LINEAR_VEL = 0.07
ANGULAR_VEL = 0.15
ROTATE_MIN_ANGLE = 0.35
ARRIVE_EPS = 0.02          # 이 거리 안이면 그 점 도달로 본다(fleet_node 판정과 별개)

TICK = 0.05                # 50ms 마다 적분


def norm_angle(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


class FakeRobot:
    """PathRequest 를 받아 그 점들을 차례로 따라가는 운동학 로봇."""

    def __init__(self, name: str, x: float, y: float, yaw: float = 0.0):
        self.name = name
        self.x, self.y, self.yaw = x, y, yaw
        self.queue: list[tuple[float, float]] = []
        self.delay_until = 0.0

    def set_path(self, pts: list[tuple[float, float]]) -> None:
        # fleet_node 는 보통 다음 한 노드만 보낸다(full_path=false).
        self.queue = list(pts)

    def step(self, dt: float, now: float) -> None:
        if now < self.delay_until:
            return                      # 지연 주입 — 멈춰 있는다
        if not self.queue:
            return
        tx, ty = self.queue[0]
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist < ARRIVE_EPS:
            self.queue.pop(0)
            return
        want = math.atan2(dy, dx)
        err = norm_angle(want - self.yaw)
        if abs(err) > ROTATE_MIN_ANGLE:
            # 제자리 회전이 먼저 — 실물 use_rotate_to_heading 과 같다.
            step = min(abs(err), ANGULAR_VEL * dt) * (1.0 if err > 0 else -1.0)
            self.yaw = norm_angle(self.yaw + step)
            return
        self.yaw = norm_angle(self.yaw + max(-0.5 * dt, min(0.5 * dt, err)))
        move = min(dist, LINEAR_VEL * dt)
        self.x += math.cos(self.yaw) * move
        self.y += math.sin(self.yaw) * move


class SimNode(Node):
    def __init__(self, robots: list[FakeRobot], record_path: str | None):
        super().__init__("cbs_sim_robots")
        self.robots = {r.name: r for r in robots}
        self.t0 = time.monotonic()
        self.record_path = record_path
        self.frames: list[dict] = []
        self.events: list[dict] = []

        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        self.state_pub = self.create_publisher(RobotState, "/robot_state", 10)
        self.create_subscription(PathRequest, "/robot_path_requests", self.on_path, qos)
        self.create_subscription(String, "/fms/occupancy", self.on_occ, 10)
        self.create_subscription(TaskState, "/fms/task_states", self.on_task, 10)
        # 시간표. 재계획 때마다 새 seq 로 들어온다 — 같은 경로라도 arrive 가 밀렸는지
        # 여기서 확인한다. 이게 "지연이 관제에 반영됐다"는 증거다.
        self.create_subscription(String, "/fms/plan", self.on_plan, 10)

        self.occupancy: dict = {}
        self.plans: list[dict] = []
        self.create_timer(TICK, self.on_tick)

    # ── 입력 ────────────────────────────────────────────────────────────
    def on_path(self, msg) -> None:
        r = self.robots.get(msg.robot_name)
        if r is None:
            return
        pts = [(p.x, p.y) for p in msg.path]
        r.set_path(pts)
        self.events.append({
            "t": self.elapsed(), "kind": "path", "robot": msg.robot_name,
            "n": len(pts), "task": msg.task_id,
        })

    def on_occ(self, msg: String) -> None:
        try:
            self.occupancy = json.loads(msg.data)
        except Exception:
            self.occupancy = {}

    def on_plan(self, msg: String) -> None:
        try:
            plan = json.loads(msg.data)
        except Exception:
            return
        plan["t"] = self.elapsed()
        self.plans.append(plan)
        who = ", ".join(
            f"{n}:{d['arrive'][-1]}틱" for n, d in plan.get("robots", {}).items())
        self.get_logger().info(f"[plan #{plan.get('seq')}] {who}")

    def on_task(self, msg) -> None:
        self.events.append({
            "t": self.elapsed(), "kind": "task", "robot": msg.robot_id,
            "task": msg.task_id, "state": msg.state,
        })
        self.get_logger().info(f"[task] {msg.task_id} {msg.state} {msg.robot_id}")

    # ── 적분 + 발행 + 기록 ───────────────────────────────────────────────
    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def on_tick(self) -> None:
        now = time.monotonic()
        for r in self.robots.values():
            r.step(TICK, now)
            m = RobotState()
            m.name = r.name
            m.battery_percent = 100.0
            m.mode = RobotMode()
            m.mode.mode = RobotMode.MODE_MOVING if r.queue else RobotMode.MODE_IDLE
            loc = Location()
            loc.x, loc.y, loc.yaw = r.x, r.y, r.yaw
            loc.level_name = "L1"
            m.location = loc
            self.state_pub.publish(m)

        self.frames.append({
            "t": self.elapsed(),
            "p": {r.name: [round(r.x, 4), round(r.y, 4), round(r.yaw, 3)]
                  for r in self.robots.values()},
            "occ": self.occupancy,
        })

    def save(self) -> None:
        if not self.record_path:
            return
        with open(self.record_path, "w", encoding="utf-8") as f:
            json.dump({"frames": self.frames, "events": self.events,
                       "plans": self.plans}, f)
        print(f"[sim] 기록 저장: {self.record_path} ({len(self.frames)} 프레임)", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot", action="append", required=True,
                    metavar="이름:x:y", help="예: Pinky-1:-0.0007:-0.0333")
    ap.add_argument("--delay", action="append", default=[],
                    metavar="이름:시작초:지속초", help="장애물 지연 주입")
    ap.add_argument("--record", default=None)
    ap.add_argument("--seconds", type=float, default=120.0)
    args = ap.parse_args()

    robots = []
    for spec in args.robot:
        name, x, y = spec.split(":")
        robots.append(FakeRobot(name, float(x), float(y)))

    rclpy.init()
    node = SimNode(robots, args.record)

    delays = []
    for spec in args.delay:
        name, start, dur = spec.split(":")
        delays.append((name, float(start), float(dur)))

    start = time.monotonic()
    applied: set[str] = set()
    try:
        while rclpy.ok() and time.monotonic() - start < args.seconds:
            rclpy.spin_once(node, timeout_sec=0.02)
            el = time.monotonic() - start
            for name, s, d in delays:
                key = f"{name}:{s}"
                if key not in applied and el >= s:
                    applied.add(key)
                    r = node.robots.get(name)
                    if r:
                        r.delay_until = time.monotonic() + d
                        node.events.append({"t": node.elapsed(), "kind": "delay",
                                            "robot": name, "dur": d})
                        node.get_logger().warn(f"[delay] {name} {d}초 정지(장애물 가정)")
    except KeyboardInterrupt:
        pass
    finally:
        node.save()
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
