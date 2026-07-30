"""Fake hardware drivers. Real Nav2 / arm / dock clients are wired in at the node layer;
these let every branch be exercised without ROS2 or a robot."""


class FakeDriver:
    """Records start/stop; poll() walks a scripted result list, then repeats "running"."""

    def __init__(self, poll_sequence=("running",)):
        self._poll_sequence = list(poll_sequence)
        self.start_count = 0
        self.stop_count = 0

    @property
    def started(self):
        return self.start_count > 0

    @property
    def stopped(self):
        return self.stop_count > 0

    def start(self):
        self.start_count += 1

    def poll(self):
        return self._poll_sequence.pop(0) if self._poll_sequence else "running"

    def stop(self):
        self.stop_count += 1


class FakeArmDriver:
    def __init__(self):
        self.home_count = 0

    @property
    def went_home(self):
        return self.home_count > 0

    def go_home(self):
        self.home_count += 1


PARAMS = {
    "battery": {"ready": 40, "charged": 80, "low": 15},
    "interacting": {"ui_idle_timeout_sec": 20},
    # 도착 판정 값은 config/params.yaml 과 같은 뜻이지만, 시험에서는 계산이 눈에
    # 보이도록 딱 떨어지는 값을 쓴다 (0.1m / 10초 / 60초).
    "working": {"command_timeout_sec": 120, "arrive_tolerance_m": 0.1,
                "arrive_resend_sec": 10, "arrive_timeout_sec": 60,
                "guide_lost_grace_sec": 3, "guide_lost_timeout_sec": 45},
    "returning": {"dock_retry_max": 3},
}


def all_drivers():
    return {
        "patrol": FakeDriver(),
        "security_patrol": FakeDriver(),
        "nav": FakeDriver(),
        "arm": FakeDriver(),
        "follow": FakeDriver(),
        "guide": FakeDriver(),
        "guide_stop": FakeDriver(),
        # 복귀 5단계. `return_arm` 은 없앴다(이 로봇에 팔이 없다).
        "return_entrance": FakeDriver(),
        "return_dock": FakeDriver(),
        "return_rotate": FakeYawDriver(),
        "return_entrance_xy": (0.6, 0.0),
        "return_parking_xy": (0.0, 0.0),
        "guide_watch": FakeDriver(),
        "junctions": None,
    }


def all_providers(**overrides):
    base = {
        "battery_percent": lambda: 60.0,
        "is_docked": lambda: False,
        "fault": lambda: False,
        "last_command": lambda: None,
        "ui_last_touch_at": lambda: 0.0,
    }
    base.update(overrides)
    return base


class FakeYawDriver(FakeDriver):
    """`start(target_yaw)` 를 받는 회전 드라이버 대역. 마지막 목표 각도를 기록한다."""

    def __init__(self, poll_sequence=("running",)):
        super().__init__(poll_sequence)
        self.last_yaw = None

    def start(self, target_yaw=0.0):
        self.last_yaw = target_yaw
        self.start_count += 1
