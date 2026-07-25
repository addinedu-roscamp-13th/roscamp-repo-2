"""명령 링크는 **로봇마다** 갈려야 한다 — IP 로 갈리면 sim 에서 무너진다.

## 왜 이 파일이 생겼나

sim 로봇 2대(`Pinkysim`, `Pinkysim2`)를 동시에 배차했더니 한 대가 아예 안 움직였다.

    [nav] Pinkysim 주행 명령 전송 실패 (명령 링크 없음)

원인은 `FLEET_ROBOTS` 와 `_cmd_pubs` 가 **IP 로 키를 잡고** 있었다는 것이다.
sim 로봇은 전부 `127.0.0.1` 이라, 두 번째를 등록하는 순간 첫 번째가 조용히 덮였다.
로봇 이름은 유일하므로 거기서 유도한 브릿지 키를 쓴다.

실물에서는 IP 가 달라 안 드러난다 — **2대 시험이 아니었으면 못 찾았다.**
"""
import time

import app.fleet_telemetry as ft


def _two_sims_on_one_ip():
    return {
        "pinkysim": {"key": "pinkysim", "prefix": "/pinkysim",
                     "name": "Pinkysim", "ip": "127.0.0.1"},
        "pinkysim2": {"key": "pinkysim2", "prefix": "/pinkysim2",
                      "name": "Pinkysim2", "ip": "127.0.0.1"},
    }


def test_robots_sharing_an_ip_keep_separate_links(monkeypatch):
    monkeypatch.setattr(ft, "FLEET_ROBOTS", _two_sims_on_one_ip())
    assert ft.key_of("Pinkysim") == "pinkysim"
    assert ft.key_of("Pinkysim2") == "pinkysim2"


def test_key_lookup_absorbs_naming_differences(monkeypatch):
    monkeypatch.setattr(ft, "FLEET_ROBOTS", {
        "pinky3": {"key": "pinky3", "prefix": "/pinky3", "name": "Pinky-3", "ip": "192.168.0.42"},
    })
    for spelling in ("Pinky-3", "pinky3", "PINKY_3", "pinky 3"):
        assert ft.key_of(spelling) == "pinky3", spelling


def test_unknown_robot_has_no_link(monkeypatch):
    monkeypatch.setattr(ft, "FLEET_ROBOTS", _two_sims_on_one_ip())
    assert ft.key_of("없는로봇") is None
    assert ft.key_of("") is None


def test_ip_lookup_still_works_for_the_http_fallback(monkeypatch):
    monkeypatch.setattr(ft, "FLEET_ROBOTS", _two_sims_on_one_ip())
    assert ft.ip_of("Pinkysim2") == "127.0.0.1"


def test_send_command_by_ip_refuses_when_the_ip_is_ambiguous(monkeypatch):
    """IP 만으로는 누구에게 보낼지 정할 수 없다 — 엉뚱한 로봇을 움직이느니 안 보낸다."""
    monkeypatch.setattr(ft, "FLEET_ROBOTS", _two_sims_on_one_ip())
    monkeypatch.setattr(ft, "_cmd_pubs", {"pinkysim": object(), "pinkysim2": object()})
    assert ft.send_command("127.0.0.1", "goal", {}) is None


def test_shared_ip_sims_keep_pose_cache_separate(monkeypatch):
    """sim2 pose callback must never overwrite sim1's cached pose."""
    monkeypatch.setattr(ft, "FLEET_ROBOTS", _two_sims_on_one_ip())
    now = time.time()
    monkeypatch.setattr(ft, "_cache", {
        "pinkysim": {**ft._empty_entry(), "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0}, "_last_ros_at": now},
        "pinkysim2": {**ft._empty_entry(), "pose": {"x": 9.0, "y": 8.0, "yaw": 0.0}, "_last_ros_at": now},
    })

    assert ft.get_state_for_robot("Pinkysim")["pose"]["x"] == 1.0
    assert ft.get_state_for_robot("Pinkysim2")["pose"]["x"] == 9.0
    # Legacy IP-only lookup is intentionally refused when it cannot identify a sim.
    assert ft.get_state("127.0.0.1") is None
