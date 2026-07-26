"""ROS2 환경(시스템 + 이 프로젝트 워크스페이스 overlay)을 해석한다.

## 왜 이 모듈이 따로 있나

`pinky_navigation` 같은 이 프로젝트의 패키지는 `/opt/ros` 가 아니라 워크스페이스
overlay 아래에 있다. 그런데 프로세스 기동 라우터는 `/opt/ros/jazzy/setup.bash` 만
source 했다. 그래서 `ros2 pkg prefix --share pinky_navigation` 이 **항상** 실패하고
nav2 기동이 통째로 죽었다 (2026-07-26 실측: Package not found → exit 1).

**overlay 경로를 하나로 하드코딩하면 안 된다** — 실배포와 개발 트리가 다르다:

    실배포 로봇 : /home/pinky/pinky_pro/install
                  (robot_agent/scripts/service_run.sh:12, ros_ws/ros_source.sh:13)
    개발 트리   : <repo>/aba_controller/libi_drive_controller/ros_ws/install

배포가 그 밖의 위치를 쓰면 LIBI_ROS_WS_SETUP 환경변수로 직접 지정한다.

## 왜 웹 스택과 분리돼 있나

표준 라이브러리만 쓴다. 그래야 FastAPI 없이 import 되고, 테스트가 **이 코드 자체**를
검증할 수 있다. 라우터 안에 두면 테스트가 소스를 잘라 흉내내게 되고, 그건 프로덕션이
아니라 사본을 검증하는 것이다.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

#: 시스템 ROS2.
SYSTEM_SETUP = Path("/opt/ros/jazzy/setup.bash")

#: 배포가 overlay 위치를 직접 지정할 때 쓰는 환경변수.
OVERLAY_ENV = "LIBI_ROS_WS_SETUP"

#: robot_agent 패키지 루트 (= 이 파일의 app/core 에서 두 단계 위).
_AGENT_ROOT = Path(__file__).resolve().parents[2]


def overlay_candidates() -> list[Path]:
    """overlay setup 파일 후보 — 앞에서부터 존재하는 첫 번째를 쓴다.

    ⚠️ **명시 지정(OVERLAY_ENV)이 있으면 그것 하나만 후보다.** 폴백을 붙이지 않는다.
       예전엔 후보 목록의 맨 앞에만 넣었는데, 그러면 오타 난 경로를 지정했을 때
       그 항목이 존재하지 않는다는 이유로 **조용히 건너뛰고 다른 overlay 를 쓴다.**
       운영자는 자기가 지정한 것이 쓰이는 줄 알고, 실제로는 엉뚱한 워크스페이스로
       nav2 가 뜬다 — 이번 사건 전체가 바로 그 "조용한 오설정" 부류였다.
       하나만 두면 못 찾았을 때 오류 메시지에 그 경로가 그대로 찍혀 오타가 바로 보인다.
    """
    injected = os.environ.get(OVERLAY_ENV)
    if injected:
        return [Path(injected)]

    out: list[Path] = []
    for prefix in (Path("/home/pinky/pinky_pro/install"),
                   _AGENT_ROOT.parent / "ros_ws" / "install"):
        out.append(prefix / "setup.bash")
        out.append(prefix / "local_setup.bash")
    return out


def overlay_setup() -> Path | None:
    """존재하는 첫 overlay setup 파일. 없으면 None."""
    for c in overlay_candidates():
        if c.is_file():
            return c
    return None


def env_lines() -> list[str]:
    """ROS 환경을 만드는 bash 줄들 — 시스템 ROS + 프로젝트 overlay.

    overlay 는 빌드 전이면 없을 수 있다. 없으면 건너뛰고, 그 사실은 호출부가
    오류 메시지에 실어 보낸다 — 배포 문제가 '패키지를 못 찾음' 으로 위장되면 안 된다.

    ⚠️ 경로는 **반드시 shlex.quote 로 감싼다.** 이 문자열은 bash 로 넘어가고,
       경로는 환경변수에서 온다. 큰따옴표로만 감싸면 `$`·백틱이 그 안에서 전개돼
       공백 있는 경로가 깨지는 정도가 아니라 명령이 실행될 수 있다.
    """
    lines = [f"source {shlex.quote(str(SYSTEM_SETUP))} || exit 1"]
    overlay = overlay_setup()
    if overlay is not None:
        lines.append(f"source {shlex.quote(str(overlay))} || exit 1")
    return lines


def describe_candidates() -> str:
    """오류 메시지에 실을 후보 목록 — 사람이 읽을 한 줄.

    ⚠️ 여기서 인용하지 않는다. 호출부가 **메시지 전체를 한 셸 워드로** 인용해서
       `printf '%s\\n' <quoted>` 로 넘기기 때문이다. 여기서 한 번 더 인용하면
       따옴표가 겹쳐 보기만 나빠진다. (예전엔 여기서 인용했는데, 호출부가 그 결과를
       `echo "...{...}..."` 로 큰따옴표 안에 다시 넣어 `$(...)` 가 전개됐다 —
       인용은 **최종적으로 셸에 넘기는 지점 한 곳에서만** 해야 한다.)
    """
    return " | ".join(str(c) for c in overlay_candidates())
