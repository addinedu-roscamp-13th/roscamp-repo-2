"""추종·길잡이·감시 세션의 수명. ROS 를 모른다.

## 세 가지 역할

    follow   관리자 추종. 앞캠. 제어 루프가 /cmd_vel 을 만든다
    guide    길잡이 감시. 뒷캠. 주행은 nav2 가 하고 여기는 눈만 된다
    watch    등록 화면 감시. 패널이 직접 연다. 주행 없음

`watch` 가 왜 필요한가 — **등록 데드락** 때문이다. 등록하려면 카메라 영상이 필요한데,
카메라는 세션이 켜고, 세션은 등록이 끝나야 시작된다. 게다가 등록 시점의 미션 상태는
`INTERACTING` 이라 `WORKING` 브랜치의 `GuideExec` 은 tick 되지도 않는다. 그래서 패널이
`/fleet_cmd{watch}` 로 감시 세션을 **직접** 연다.

## 왜 session id 로 닫는가

`/fleet_cmd` 의 `stop` 은 공용 명령이라 누가 보냈는지 알 수 없다. 패널이 등록 화면을
벗어나며 보낸 `stop` 이 관리자 추종까지 끊으면 안 된다. 그래서 id 가 맞을 때만 닫는다.

## 왜 lease 가 필요한가

패널 프로세스가 죽으면 `stop` 이 영영 안 온다. 그러면 카메라가 계속 켜져 있고 아무도
그 사실을 모른다. 갱신이 끊기면 스스로 닫힌다.
"""
from dataclasses import dataclass

FOLLOW, GUIDE, WATCH = "follow", "guide", "watch"
NONE = "none"

#: 역할별 정위치 카메라. 추종은 앞(제어 입력), 길잡이는 뒤(따라오는 사람).
ROLE_CAMERA = {FOLLOW: "front", GUIDE: "back"}

#: 주행을 만드는 역할. `watch` 는 눈만 되므로 제어 루프를 켜지 않는다.
DRIVING_ROLES = (FOLLOW,)


@dataclass
class Session:
    session_id: str
    role: str
    camera: str
    touched_at: float


class SessionManager:
    """한 번에 하나의 세션만 산다. 새 세션은 이전 것을 대체한다."""

    def __init__(self, lease_sec: float):
        self.lease_sec = float(lease_sec)
        self._cur = None

    # ── 수명 ────────────────────────────────────────────────────────────
    def start(self, session_id, role: str, now: float, camera=None) -> Session:
        """세션을 연다. **같은 id 로 다시 부르면 lease 갱신이 된다** — 패널이 살아 있는
        동안 주기적으로 같은 `watch` 를 재발행하는 것이 곧 갱신이다. 갱신 전용 경로를
        따로 두면 "무엇이 세션을 살려두는가"가 두 곳으로 갈린다."""
        cam = camera if camera in ("front", "back") else ROLE_CAMERA.get(role, NONE)
        self._cur = Session(str(session_id), role, cam, now)
        return self._cur

    def stop(self, session_id, now: float = 0.0) -> bool:
        """id 가 맞을 때만 닫는다. 닫았으면 True."""
        if self._cur is None or self._cur.session_id != str(session_id):
            return False
        self._cur = None
        return True

    def expired(self, now: float) -> bool:
        """lease 는 **패널이 연 `watch` 세션에만** 적용된다.

        ⚠️ 여기에 `follow`/`guide` 까지 넣으면 안 된다. 그 둘은 BT 가 열고 BT 가 닫으며,
        수십 분씩 이어지는 것이 정상이다. lease 를 걸면 갱신 경로가 없어 **정확히
        lease_sec 만에 강제 종료**된다 — 멀쩡히 따라가던 추종이 60초에 실패한다.

        `watch` 만 다른 이유는 그것을 여는 주체가 **패널 프로세스**이기 때문이다.
        패널이 죽으면 `stop` 이 영영 안 오고, 아무도 안 보는데 카메라가 계속 켜져 있다.
        패널이 살아 있는 동안에는 같은 `watch` 를 주기적으로 재발행해 갱신한다.
        """
        if self._cur is None or self.lease_sec <= 0:
            return False
        if self._cur.role != WATCH:
            return False
        return (now - self._cur.touched_at) > self.lease_sec

    def sweep(self, now: float) -> bool:
        """만료된 세션을 닫는다. 닫았으면 True."""
        if self.expired(now):
            self._cur = None
            return True
        return False

    # ── 조회 ────────────────────────────────────────────────────────────
    @property
    def current(self):
        return self._cur

    @property
    def role(self):
        return self._cur.role if self._cur else None

    @property
    def session_id(self):
        return self._cur.session_id if self._cur else None

    @property
    def driving(self) -> bool:
        return self._cur is not None and self._cur.role in DRIVING_ROLES

    def camera_for(self) -> str:
        return self._cur.camera if self._cur else NONE

    # ── 회복 BT 가 탐색 중 반대 캠을 요청할 때 ─────────────────────────────
    def override_camera(self, camera: str) -> None:
        """역할은 그대로 두고 보는 캠만 바꾼다. 세션이 없으면 무시한다.

        정위치 캠으로 되돌리는 것도 이 함수로 한다(`AlignHeading` 이 회전을 마치며
        `ctx.home_camera` 를 요청한다). 되돌리기 전용 함수를 따로 두지 않는 이유는,
        그러면 "누가 되돌릴 책임인가"가 두 곳으로 갈리기 때문이다.
        """
        if self._cur is not None and camera in ("front", "back"):
            self._cur.camera = camera



def target_session_id(cmd_id, args=None) -> str:
    """`stop` 명령이 가리키는 세션 id.

    ⚠️ 원 id 를 그대로 비교하면 **BT 가 연 세션이 영영 안 닫힌다.**
    `FleetCmdDriver.stop()` 은 원 id 가 아니라 `stop-<원래id>` 를 보낸다
    (`libi_modes/ros/fleet_cmd_driver.py`). 그래서 접두어를 벗겨서 비교한다.

    패널처럼 어느 세션인지 명시할 수 있는 쪽은 `args.session_id` 를 쓴다(그게 이긴다).
    """
    if args:
        explicit = args.get("session_id")
        if explicit:
            return str(explicit)
    s = str(cmd_id or "")
    return s[len("stop-"):] if s.startswith("stop-") else s
