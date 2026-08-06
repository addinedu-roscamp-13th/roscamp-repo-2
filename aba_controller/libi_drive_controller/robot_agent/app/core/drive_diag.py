"""왜 바퀴가 안 도는가 — **ROS 없이 도는 순수 판정.**

## 왜 있나

"명령을 줬는데 안 움직인다"의 원인이 여럿인데 증상이 하나였다. `nav_phase` 가
목표 쪽 원인(물림·죽은목표·회복반복)을 갈랐고, 여기가 **나머지 하나**를 가른다:

    nav2 는 정상 주행 중이고 `cmd_vel_nav_out` 도 나오는데 `/cmd_vel` 만 침묵한다.

twist_mux 의 `fsm_motion_lock`(우선순위 150)이 navigation(50)·recovery(60)·
follow(100)를 **막을 뿐 0 을 만들지 않기** 때문이다. 상위층은 거리로만 판단하므로
이것을 "정체"로 오인하고 재시도를 소진한다.

`scripts/state_drive_verify.py` 가 같은 판정을 이미 갖고 있지만 **수동 스크립트**다 —
사람이 돌려야 하고 돌리는 동안만 본다. 사고는 아무도 안 볼 때 난다.

## 무엇을 답하나

`blocked` 한 필드가 이유를 이름으로 말한다:

    None                  막힌 것 없음(정상이거나, 애초에 아무도 안 낸다)
    "motion_lock"         FSM 이 잠갔다 — 자율 제어 전부가 막힌다
    "outranked:dock"      더 높은 우선순위 입력이 이기고 있다
    "no_input"            아무 입력도 신선하지 않다 — 낸 사람이 없다

⚠️ 우선순위와 timeout 은 `pinky_bringup/config/twist_mux.yaml` 과 **같은 값이어야
   한다.** 두 곳에 적힌 값이라 한쪽만 고치면 화면이 조용히 거짓말을 한다.
   그쪽을 고치면 여기도 같이 고칠 것.
"""

#: (이름, 토픽, 우선순위) — twist_mux.yaml 과 같은 값. 높을수록 이긴다.
MUX_INPUTS = (
    ("stop",       "cmd_vel_stop",     255),
    ("hold",       "cmd_vel_hold",     160),
    ("dock",       "cmd_vel_dock",     120),
    ("follow",     "cmd_vel_follow",   100),
    ("recovery",   "cmd_vel_recovery",  60),
    ("navigation", "cmd_vel_nav_out",   50),
)

#: twist_mux 가 입력을 "없는 것"으로 치는 시간(초). yaml 의 `timeout` 과 같아야 한다.
MUX_TIMEOUT_SEC = 0.5

#: 잠금이 막는 우선순위 상한. yaml 의 `fsm_motion_lock.priority` 와 같아야 한다.
#: 이보다 **낮은** 입력이 막힌다.
LOCK_PRIORITY = 150

_PRIORITY = {name: prio for name, _topic, prio in MUX_INPUTS}


def winner(fresh_names) -> str | None:
    """신선한 입력 중 우선순위가 가장 높은 것. 없으면 None."""
    best, best_prio = None, -1
    for name in fresh_names:
        prio = _PRIORITY.get(name)
        if prio is not None and prio > best_prio:
            best, best_prio = name, prio
    return best


def blocked_reason(fresh_names, motion_lock: bool | None,
                   subject: str = "navigation") -> str | None:
    """`subject` 가 바퀴에 못 닿는 이유. 닿고 있으면 None.

    `subject` 가 애초에 신선하지 않으면 **막힌 게 아니다** — 낸 사람이 없는 것이고,
    그건 목표 쪽 문제라 `nav_phase` 가 답한다. 둘을 섞으면 원인을 또 뭉갠다.
    """
    fresh = set(fresh_names)
    if subject not in fresh:
        return "no_input" if not fresh else None
    if motion_lock and _PRIORITY.get(subject, 0) < LOCK_PRIORITY:
        return "motion_lock"
    top = winner(fresh)
    if top is not None and top != subject:
        return f"outranked:{top}"
    return None


def snapshot(ages: dict, motion_lock: bool | None, cmd_vel_age: float | None,
             cmd_vel_moving_age: float | None, subject: str = "navigation") -> dict:
    """`/fleet_status` 에 실린다. `ages` 는 입력 이름 → 마지막 수신 후 경과(초)."""
    fresh = [n for n, age in ages.items()
             if age is not None and age <= MUX_TIMEOUT_SEC]
    return {
        "motion_lock": motion_lock,
        "winner": winner(fresh),
        "blocked": blocked_reason(fresh, motion_lock, subject),
        "fresh": sorted(fresh, key=lambda n: -_PRIORITY.get(n, 0)),
        # `/cmd_vel` 자체의 나이와 **마지막으로 0 이 아니었던** 때의 나이.
        # ⚠️ 무발행으로 정지를 판정하면 안 된다 — 정지도 0 을 발행하는 정상 동작이다
        #    (state_drive_verify.py 머리말과 같은 이유).
        "cmd_vel_age": cmd_vel_age,
        "cmd_vel_moving_age": cmd_vel_moving_age,
    }
