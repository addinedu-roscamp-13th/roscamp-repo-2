"""카메라로만 보이는 장애물을 nav2 에 알리는 통행 금지 마스크. **기본은 꺼져 있다.**

## 왜 obstacle_layer 에 직접 주입하면 안 되나

nav2 의 `obstacle_layer` 는 `clearing: True` 로 설정돼 있다 — 레이저가 통과한 칸을
비운다. 카메라로만 보이는 물체는 **스캔 평면에 없다**(그래서 카메라가 필요한 것이다).
그러면 레이저가 그 칸을 그냥 통과하고, 우리가 찍은 표시를 다음 스캔에 지워버린다.

`KeepoutFilter` 의 마스크는 레이트레이싱이 못 지운다. 그래서 이쪽을 쓴다.

## 왜 10초를 기다리나

지나가는 사람 때문에 지도가 더러워지는 것을 막는다. 근접하면 **즉시 멈추되**,
그 자리에 계속 있을 때만 마스크를 만든다.

## 로봇 자기 자리는 절대 막지 않는다

로봇이 마스크 안에 들어가면 컨트롤러가 **탈출 궤적까지 막아** 그 자리에 갇힌다.
부채꼴은 로봇 앞쪽에서 시작하되 footprint 반경만큼 띄운다.

## 조종하지 않는다

여기가 하는 일은 "여기 못 간다"를 지도에 적는 것뿐이다. 우회 경로는 nav2 가 만든다.
BT 가 직접 우회하면 제어 주체가 둘이 되어 어느 쪽이 이겼는지 로그로 못 가린다.
"""
import math

DRIVE, HALT, MASK = "drive", "halt", "mask"


class Fan:
    """로봇 앞 부채꼴. `contains(x, y)` 로 판정한다(마스크 래스터화 전 단계)."""

    def __init__(self, x, y, yaw, half_angle_rad, range_m, inner_radius):
        self.x, self.y, self.yaw = x, y, yaw
        self.half_angle = half_angle_rad
        self.range = range_m
        self.inner = inner_radius

    def contains(self, x, y) -> bool:
        dx, dy = x - self.x, y - self.y
        d = math.hypot(dx, dy)
        if d < self.inner or d > self.range:
            return False        # 로봇 자기 자리(inner)는 절대 막지 않는다
        rel = math.atan2(dy, dx) - self.yaw
        rel = (rel + math.pi) % (2 * math.pi) - math.pi
        return abs(rel) <= self.half_angle


class KeepoutPolicy:
    """`update(area, pose, now) -> (동작, 마스크)`.

    `near_area_max <= 0` 이면 통째로 꺼진다 — 그때는 첫 줄에서 끝난다.
    """

    def __init__(self, near_area_max, wait_sec, ttl_sec, fan_deg, fan_range_m,
                 footprint_radius):
        self.near_area_max = float(near_area_max)
        self.wait_sec = float(wait_sec)
        self.ttl_sec = float(ttl_sec)
        self.half_angle = math.radians(float(fan_deg)) / 2.0
        self.fan_range = float(fan_range_m)
        self.footprint_radius = float(footprint_radius)
        self._blocked_since = None
        self._mask = None
        self._mask_until = None

    @property
    def enabled(self) -> bool:
        return self.near_area_max > 0

    def update(self, area, pose, now):
        if not self.enabled:
            return DRIVE, None
        blocked = area is not None and area > self.near_area_max
        if not blocked:
            # 비켰다. 타이머를 되돌린다 — 지나가는 사람 때문에 지도가 더러워지지 않게.
            self._blocked_since = None
            return DRIVE, self.active_mask(now)
        if self._blocked_since is None:
            self._blocked_since = now
        if now - self._blocked_since < self.wait_sec:
            return HALT, self.active_mask(now)
        self._mask = self._fan(pose)
        self._mask_until = now + self.ttl_sec
        return MASK, self._mask

    def active_mask(self, now):
        """수명이 남은 마스크. 만료됐으면 None — 안 그러면 지도가 점점 막힌다."""
        if self._mask is None or self._mask_until is None or now >= self._mask_until:
            self._mask = None
            self._mask_until = None
            return None
        return self._mask

    def _fan(self, pose):
        x, y, yaw = _xy_yaw(pose)
        return Fan(x, y, yaw, self.half_angle, self.fan_range, self.footprint_radius)


def _xy_yaw(pose):
    if pose is None:
        return 0.0, 0.0, 0.0
    if isinstance(pose, dict):
        return pose.get("x", 0.0), pose.get("y", 0.0), pose.get("yaw", 0.0)
    x, y = pose[0], pose[1]
    yaw = pose[2] if len(pose) > 2 else 0.0
    return x, y, yaw


# ── ROS 노드 ────────────────────────────────────────────────────────────────
# 정책(위)은 ROS 를 모른다. 아래만 rclpy 를 쓴다 — 그래서 규칙은 로봇 없이 시험된다.

MASK_TOPIC = "/keepout_mask"
FILTER_INFO_TOPIC = "/costmap_filter_info"
REQUESTER_AREA_TOPIC = "/libi/requester_area"
POSE_TOPIC = "/amcl_pose"


def main(args=None):
    import math as _math

    import rclpy
    from geometry_msgs.msg import PoseWithCovarianceStamped
    from nav_msgs.msg import OccupancyGrid
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Float32

    try:
        from nav2_msgs.msg import CostmapFilterInfo
    except ImportError:
        CostmapFilterInfo = None

    latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)

    class KeepoutNode(Node):
        def __init__(self):
            super().__init__("libi_keepout_mask")
            self.declare_parameter("near_area_max", 0.0)
            self.declare_parameter("wait_sec", 10.0)
            self.declare_parameter("ttl_sec", 20.0)
            self.declare_parameter("fan_deg", 60.0)
            self.declare_parameter("fan_range_m", 0.5)
            self.declare_parameter("footprint_radius", 0.06)
            self.declare_parameter("resolution", 0.02)
            self.declare_parameter("frame_id", "map")
            g = lambda k: self.get_parameter(k).value      # noqa: E731

            self.policy = KeepoutPolicy(
                near_area_max=g("near_area_max"), wait_sec=g("wait_sec"),
                ttl_sec=g("ttl_sec"), fan_deg=g("fan_deg"),
                fan_range_m=g("fan_range_m"), footprint_radius=g("footprint_radius"))
            self.resolution = float(g("resolution"))
            self.frame_id = g("frame_id")
            self._pose = None
            self._area = None

            self._mask_pub = self.create_publisher(OccupancyGrid, MASK_TOPIC, latched)
            self._info_pub = None
            if CostmapFilterInfo is not None:
                self._info_pub = self.create_publisher(
                    CostmapFilterInfo, FILTER_INFO_TOPIC, latched)
                info = CostmapFilterInfo()
                info.type = 0                    # keepout / preferred lanes
                info.filter_mask_topic = MASK_TOPIC
                info.base = 0.0
                info.multiplier = 1.0
                self._info_pub.publish(info)
            else:
                self.get_logger().warn(
                    "nav2_msgs 가 없어 costmap_filter_info 를 못 냅니다 — "
                    "마스크만 발행합니다(nav2 는 무시합니다).")

            self.create_subscription(Float32, REQUESTER_AREA_TOPIC, self._on_area, 10)
            self.create_subscription(PoseWithCovarianceStamped, POSE_TOPIC,
                                     self._on_pose, latched)
            self.create_timer(0.5, self._tick)
            self.get_logger().info(
                f"통행 금지 마스크 {'ON' if self.policy.enabled else 'OFF'} — "
                f"near_area_max={g('near_area_max')} fan={g('fan_deg')}° "
                f"ttl={g('ttl_sec')}s")

        def _on_area(self, msg):
            self._area = float(msg.data)

        def _on_pose(self, msg):
            p, q = msg.pose.pose.position, msg.pose.pose.orientation
            yaw = _math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                              1.0 - 2.0 * (q.y * q.y + q.z * q.z))
            self._pose = {"x": float(p.x), "y": float(p.y), "yaw": yaw}

        def _tick(self):
            now = self.get_clock().now().nanoseconds / 1e9
            action, mask = self.policy.update(self._area, self._pose, now)
            if action == MASK:
                self._publish(mask)
            elif mask is None:
                self._publish(None)              # 만료 — 빈 마스크로 되돌린다

        def _publish(self, fan):
            grid = OccupancyGrid()
            grid.header.frame_id = self.frame_id
            grid.header.stamp = self.get_clock().now().to_msg()
            grid.info.resolution = self.resolution
            if fan is None:
                grid.info.width = grid.info.height = 1
                grid.data = [0]
                self._mask_pub.publish(grid)
                return
            side = int(_math.ceil(2 * fan.range / self.resolution)) + 1
            grid.info.width = grid.info.height = side
            grid.info.origin.position.x = fan.x - fan.range
            grid.info.origin.position.y = fan.y - fan.range
            data = []
            for j in range(side):
                wy = grid.info.origin.position.y + (j + 0.5) * self.resolution
                for i in range(side):
                    wx = grid.info.origin.position.x + (i + 0.5) * self.resolution
                    data.append(100 if fan.contains(wx, wy) else 0)
            grid.data = data
            self._mask_pub.publish(grid)

    rclpy.init(args=args)
    node = KeepoutNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
