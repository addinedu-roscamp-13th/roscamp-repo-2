import time

import py_trees
from py_trees.common import Status


class CameraSelectRenew(py_trees.behaviour.Behaviour):
    """앞캠을 켜 둔다 — `/libi/camera_select` 에 `"front"` 를 주기 재발행한다.

    ## 왜 필요한가 — 빠뜨리면 야간 기능 전체가 조용히 죽는다

    야간 순찰 중 앞캠을 켜 두던 **유일한 통로**가 `PersonBlockGuard._request_camera`
    (`person_block.py:241-255`)였다. 이 설계는 야간에서 그 감시자를 빼므로, 그 20줄만
    떼어 여기로 옮긴다. 안 옮기면 `camera_select` 가 만료되어 프레임이 AI 서버로
    **아예 안 가고**, 감지·녹화·추종이 전부 죽는다 — 에러 한 줄 없이.

    ## 발행자 규칙과의 관계

    `camera_select` 발행자는 `follow_node` 하나라는 규칙이 있지만(`follow_node.py:239`),
    `PersonBlockGuard` 는 이미 그 규칙의 **기존 예외**다(`person_block.py:142` — 세션을
    열지 않고 직접 요청한다). 이 잎은 그 예외를 이름만 바꿔 잇는 것이지 새 예외가 아니다.

    추종이 시작되면 `follow_node` 가 세션 소유자로서 자기도 발행하는데, **둘 다 `"front"`
    를 내므로 결과가 같다**(길잡이는 뒷캠이라 겹치면 문제지만 여기는 아니다).
    """

    def __init__(self, camera_driver=None, renew_sec=1.0,
                 now_fn=time.monotonic, name="CameraSelectRenew"):
        super().__init__(name=name)
        self.camera_driver = camera_driver
        self.renew_sec = renew_sec
        self._now = now_fn
        self._sent_at = None

    def setup(self, **kwargs):
        return True

    def initialise(self):
        # 브랜치에 다시 들어오면 즉시 한 번 보낸다.
        self._sent_at = None

    def update(self) -> Status:
        if self.camera_driver is None:
            return Status.RUNNING
        now = self._now()
        if self._sent_at is None or now - self._sent_at >= self.renew_sec:
            self._sent_at = now
            self.camera_driver("front")
        return Status.RUNNING
