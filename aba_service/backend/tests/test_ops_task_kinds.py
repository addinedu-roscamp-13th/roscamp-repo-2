"""R14 — 작업 종류별로 맞는 입력 + 「복귀」·「순찰」은 모드 변경.

못박는 것:
1. 종류마다 필요한 입력이 다르고, 없는 값은 백엔드가 채운다(진열의 목적지 = 도서 zone).
2. 필요 없는 값을 안 넣어도 접수된다(정리·파견은 목적지만).
3. 종류별 다리 구성이 FMS 로 넘어간다(`kind`: delivery 4다리 / navigate 1다리).
4. 「복귀」·「순찰」은 주문을 만들지 않고 **로봇 모드 변경**으로 나간다.
"""

import pytest

from app.routers import ops
from tests.conftest import make_book

TASKS = "/api/admin/ops/tasks"


class FakeOps:
    """`ops` 가 보는 FMS 대역 — 주문과 전이 요청을 따로 센다."""

    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.transitions: list[dict] = []
        self.assigns: list[tuple[str, str]] = []
        self.order_ok = True
        self.transition_ok = True
        self.accepted = True
        self.reason = ""

    def submit_order(self, **kw) -> tuple[bool, str]:
        if not self.order_ok:
            return False, "FMS 연결 실패: 대역"
        self.orders.append(kw)
        return True, f"t{len(self.orders)}"

    def request_transition(self, robot, target_state, force=False) -> tuple[bool, dict]:
        if not self.transition_ok:
            return False, {"reason": "FMS 연결 실패: 대역"}
        self.transitions.append(
            {"robot": robot, "target_state": target_state, "force": force}
        )
        return True, {
            "accepted": self.accepted,
            "current_state": "PATROL",
            "reason": self.reason,
        }

    def assign_order(self, task_id, robot) -> tuple[bool, str]:
        self.assigns.append((task_id, robot))
        return True, "{}"


@pytest.fixture()
def fake_fms(monkeypatch) -> FakeOps:
    fake = FakeOps()
    monkeypatch.setattr(ops.fms_client, "submit_order", fake.submit_order)
    monkeypatch.setattr(ops.fms_client, "request_transition", fake.request_transition)
    monkeypatch.setattr(ops.fms_client, "assign_order", fake.assign_order)
    return fake


# ── 종류 정의가 화면과 백엔드에서 같은 표를 본다 ─────────────────────────────


def test_종류_정의는_폼_명세를_포함한다():
    keys = [k["key"] for k in ops.TASK_KINDS]
    assert keys == [
        "transfer", "shelve", "sort", "tidy", "porter", "dispatch", "return", "patrol",
    ]
    for spec in ops.TASK_KINDS:
        assert spec["mode"] in ("order", "state")
        assert isinstance(spec["fields"], list)
        if spec["mode"] == "order":
            assert spec["order_kind"] in ("delivery", "navigate")
        else:
            assert spec["target_state"] in ("RETURNING", "PATROL")


def test_복귀와_순찰만_모드_변경이다():
    modes = {k["key"]: k["mode"] for k in ops.TASK_KINDS}
    assert [k for k, m in modes.items() if m == "state"] == ["return", "patrol"]


def test_주행만_하는_종류는_navigate_로_나간다():
    by_key = {k["key"]: k for k in ops.TASK_KINDS if k["mode"] == "order"}
    assert by_key["tidy"]["order_kind"] == "navigate"
    assert by_key["dispatch"]["order_kind"] == "navigate"
    assert by_key["transfer"]["order_kind"] == "delivery"
    assert by_key["shelve"]["order_kind"] == "delivery"
    assert by_key["sort"]["order_kind"] == "delivery"
    assert by_key["porter"]["order_kind"] == "delivery"


# ── 배달형 ───────────────────────────────────────────────────────────────────


def test_이송은_대상_출발_목적지를_그대로_보낸다(client, admin_auth, fake_fms):
    res = client.post(
        TASKS,
        json={
            "kind": "transfer",
            "book": "어린 왕자",
            "pickup": "문학-1",
            "dropoff": "안네데스크",
        },
        headers=admin_auth,
    )
    assert res.status_code == 201, res.text
    assert res.json()["mode"] == "order"
    assert fake_fms.orders == [
        {
            "kind": "delivery",
            "book": "어린 왕자",
            "pickup": "문학-1",
            "dropoff": "안네데스크",
            "requester": "사서:transfer",
            "priority": 0,
        }
    ]


def test_진열의_목적지는_도서의_서가에서_나온다(
    client, admin_auth, fake_fms, db_session
):
    make_book(db_session, title="코스모스", zone="과학-1")
    res = client.post(
        TASKS,
        # 목적지를 아예 보내지 않는다 — 책이 정하기 때문이다.
        json={"kind": "shelve", "book": "코스모스", "pickup": "안네데스크"},
        headers=admin_auth,
    )
    assert res.status_code == 201, res.text
    assert res.json()["dropoff"] == "과학-1"
    assert fake_fms.orders[0]["dropoff"] == "과학-1"


def test_진열은_카탈로그에_없는_제목이면_거절(client, admin_auth, fake_fms):
    res = client.post(
        TASKS,
        json={"kind": "shelve", "book": "없는 책", "pickup": "안네데스크"},
        headers=admin_auth,
    )
    assert res.status_code == 400
    assert fake_fms.orders == []


def test_분류는_대상을_안_받고_반납_도서_일괄로_고정(client, admin_auth, fake_fms):
    res = client.post(
        TASKS,
        json={"kind": "sort", "pickup": "안네데스크", "dropoff": "입구"},
        headers=admin_auth,
    )
    assert res.status_code == 201, res.text
    assert fake_fms.orders[0]["book"] == ops.SORT_CARGO


def test_짐꾼은_도서_목록에_없는_짐_이름을_받는다(client, admin_auth, fake_fms):
    res = client.post(
        TASKS,
        json={
            "kind": "porter",
            "cargo": "행사용 의자",
            "pickup": "입구",
            "dropoff": "화장실",
        },
        headers=admin_auth,
    )
    assert res.status_code == 201, res.text
    assert fake_fms.orders[0]["book"] == "행사용 의자"


def test_짐꾼은_짐_이름이_없으면_거절(client, admin_auth, fake_fms):
    res = client.post(
        TASKS,
        json={"kind": "porter", "pickup": "입구", "dropoff": "화장실"},
        headers=admin_auth,
    )
    assert res.status_code == 400
    assert fake_fms.orders == []


# ── 주행형 ───────────────────────────────────────────────────────────────────


def test_정리는_목적지만_있으면_접수된다(client, admin_auth, fake_fms):
    res = client.post(TASKS, json={"kind": "tidy", "dropoff": "문학-1"}, headers=admin_auth)
    assert res.status_code == 201, res.text
    order = fake_fms.orders[0]
    assert order["kind"] == "navigate"
    assert order["dropoff"] == "문학-1"
    # 필요 없는 값은 아예 안 실린다 — 책을 집으러 가면 안 된다.
    assert order["book"] == ""
    assert order["pickup"] == ""


def test_파견도_목적지만_있으면_접수된다(client, admin_auth, fake_fms):
    res = client.post(
        TASKS, json={"kind": "dispatch", "dropoff": "입구"}, headers=admin_auth
    )
    assert res.status_code == 201, res.text
    assert fake_fms.orders[0]["kind"] == "navigate"


def test_목적지가_없으면_거절(client, admin_auth, fake_fms):
    res = client.post(TASKS, json={"kind": "tidy"}, headers=admin_auth)
    assert res.status_code == 400
    assert fake_fms.orders == []


# ── 모드 변경(복귀·순찰) ─────────────────────────────────────────────────────


def test_복귀는_주문을_만들지_않고_RETURNING_으로_보낸다(
    client, admin_auth, fake_fms
):
    res = client.post(
        TASKS, json={"kind": "return", "robot": "pinky1"}, headers=admin_auth
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["mode"] == "state"
    assert body["state"] == "RETURNING"
    assert body["accepted"] is True

    # ★ 핵심: 배달 주문이 하나도 안 나간다.
    assert fake_fms.orders == []
    assert fake_fms.transitions == [
        {"robot": "pinky1", "target_state": "RETURNING", "force": False}
    ]


def test_순찰은_주문을_만들지_않고_PATROL_로_보낸다(client, admin_auth, fake_fms):
    res = client.post(
        TASKS, json={"kind": "patrol", "robot": "pinky1"}, headers=admin_auth
    )
    assert res.status_code == 201, res.text
    assert res.json()["state"] == "PATROL"
    assert fake_fms.orders == []
    assert fake_fms.transitions[0]["target_state"] == "PATROL"


def test_모드_변경은_로봇_지정이_필수(client, admin_auth, fake_fms):
    res = client.post(TASKS, json={"kind": "return"}, headers=admin_auth)
    assert res.status_code == 400
    assert fake_fms.transitions == []


def test_로봇이_거부하면_이유가_그대로_전달된다(client, admin_auth, fake_fms):
    """전이표에 없는 간선 등 — FMS 는 200 이지만 accepted=False 로 온다."""
    fake_fms.accepted = False
    fake_fms.reason = "'WORKING' 에서 'RETURNING' 로 가는 간선이 전이 박스에 없습니다."
    res = client.post(
        TASKS, json={"kind": "return", "robot": "pinky1"}, headers=admin_auth
    )
    assert res.status_code == 201
    assert res.json()["accepted"] is False
    assert "간선" in res.json()["reason"]


def test_FMS를_못_부르면_503(client, admin_auth, fake_fms):
    fake_fms.transition_ok = False
    res = client.post(
        TASKS, json={"kind": "return", "robot": "pinky1"}, headers=admin_auth
    )
    assert res.status_code == 503


# ── 공통 ─────────────────────────────────────────────────────────────────────


def test_모르는_종류는_거절(client, admin_auth, fake_fms):
    res = client.post(TASKS, json={"kind": "댄스", "dropoff": "입구"}, headers=admin_auth)
    assert res.status_code == 400
    assert fake_fms.orders == []


def test_로봇을_지정하면_배차까지_한다(client, admin_auth, fake_fms):
    res = client.post(
        TASKS,
        json={"kind": "dispatch", "dropoff": "입구", "robot": "pinky2"},
        headers=admin_auth,
    )
    assert res.json()["assigned"] == "pinky2"
    assert fake_fms.assigns == [("t1", "pinky2")]


def test_인증_없이는_지시할_수_없다(client, fake_fms):
    assert client.post(TASKS, json={"kind": "tidy", "dropoff": "입구"}).status_code == 401
    assert fake_fms.orders == []
