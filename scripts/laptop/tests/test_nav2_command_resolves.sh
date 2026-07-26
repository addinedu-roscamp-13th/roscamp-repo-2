#!/usr/bin/env bash
# robot_agent 의 nav2 기동 명령이 실제로 파라미터 파일을 찾아내는지.
#
# 보는 것은 결과다: driving.py 가 만드는 ROS 환경으로 pinky_navigation 이 해석되는가.
# 명령 문자열의 모양이 아니라 **해석 결과**를 본다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DRIVING="$REPO/aba_controller/libi_drive_controller/robot_agent/app/routers/driving.py"
ROS_SETUP=/opt/ros/jazzy/setup.bash
WS_SETUP="$REPO/aba_controller/libi_drive_controller/ros_ws/install/setup.bash"

[ -f "$ROS_SETUP" ] || { echo "SKIP: ROS2 Jazzy 없음"; exit 0; }
[ -f "$WS_SETUP" ]  || { echo "SKIP: ros_ws 미빌드 ($WS_SETUP)"; exit 0; }

FAILED=0
pass_case() { echo "  ✅ $1"; }
fail_case() { echo "  ❌ $1"; FAILED=1; }

ROS_ENV="$REPO/aba_controller/libi_drive_controller/robot_agent/app/core/ros_env.py"

echo "[test] ROS 환경 해석 모듈이 존재하는가"
if [ -f "$ROS_ENV" ]; then
  pass_case "app/core/ros_env.py 있음"
else
  fail_case "app/core/ros_env.py 가 없다 — overlay 해석이 웹 스택에 묶여 테스트 불가"
fi

echo "[test] overlay 경로가 하드코딩돼 있지 않은가 (실배포 대응)"
if [ -f "$ROS_ENV" ] && grep -q "LIBI_ROS_WS_SETUP" "$ROS_ENV" && grep -q "pinky_pro/install" "$ROS_ENV"; then
  pass_case "환경변수 주입 + 실배포 후보 경로 둘 다 있음"
else
  fail_case "환경변수 주입 또는 실배포 후보(/home/pinky/pinky_pro/install)가 없다"
fi

echo "[test] 모듈이 웹 스택 없이 import 되는가 (테스트 가능성의 전제)"
if python3 -c "
import importlib.util,sys
spec=importlib.util.spec_from_file_location('t','$ROS_ENV')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert callable(m.env_lines) and callable(m.overlay_setup) and callable(m.overlay_candidates)
" 2>/dev/null; then
  pass_case "표준 라이브러리만으로 import 됨"
else
  fail_case "ros_env.py 를 표준 파이썬으로 import 하지 못한다 — 무거운 의존성이 딸려온다"
fi

echo "[test] 프로덕션 모듈이 만드는 환경 줄로 pinky_navigation 이 해석되는가"
# ⚠️ 소스 텍스트를 잘라 exec 하지 않는다. 그렇게 하면 프로덕션이 아니라 **사본**을 검증하게
#    되고, 무해한 리팩터에도 테스트가 깨지거나 반대로 프로덕션이 갈라져도 통과한다.
#    ros_env.py 는 표준 라이브러리만 쓰므로 FastAPI 없이 그대로 import 된다.
ENV_LINES="$(python3 - "$REPO" <<'PY' 2>/dev/null
import importlib.util, pathlib, sys
repo = pathlib.Path(sys.argv[1])
mod_path = repo / "aba_controller/libi_drive_controller/robot_agent/app/core/ros_env.py"
spec = importlib.util.spec_from_file_location("libi_ros_env_under_test", mod_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)          # 패키지 import 를 타지 않으므로 의존성이 딸려오지 않는다
print("\n".join(m.env_lines()))
PY
)"
if [ -z "$ENV_LINES" ]; then
  fail_case "app/core/ros_env.py 에서 env_lines() 를 가져오지 못했다"
else
  RESOLVED="$(bash -c "$ENV_LINES
ros2 pkg prefix --share pinky_navigation 2>/dev/null" 2>/dev/null | tail -1)"
  if [ -n "$RESOLVED" ] && [ -f "$RESOLVED/params/nav2_params.yaml" ]; then
    pass_case "해석됨: $RESOLVED/params/nav2_params.yaml"
  else
    fail_case "ros_env.env_lines() 환경으로는 pinky_navigation/params/nav2_params.yaml 을 못 찾는다 (got '$RESOLVED')"
  fi
fi

echo "[test] driving.py 의 nav2 명령이 그 모듈을 실제로 쓰는가 (사본으로 갈라지지 않았는가)"
# 모듈만 맞고 라우터가 옛 경로를 쓰면 프로덕션은 여전히 깨져 있다. 둘의 연결을 확인한다.
if grep -q "ros_env.env_lines()" "$DRIVING" && grep -q "from app.core import ros_env" "$DRIVING"; then
  pass_case "driving.py 가 ros_env.env_lines() 를 사용"
else
  fail_case "driving.py 가 ros_env 를 import 해서 쓰지 않는다 — 모듈과 라우터가 갈라졌다"
fi

echo "[test] 명시 지정(LIBI_ROS_WS_SETUP)이 폴백에 밀리지 않는가"
# 오타 난 경로를 지정했는데 조용히 다른 overlay 가 쓰이면, 운영자는 자기 지정이 먹은 줄 안다.
# 명시 지정이 있으면 후보는 그것 하나여야 하고, 없으면 '못 찾음' 으로 크게 실패해야 한다.
OVERRIDE_OUT="$(LIBI_ROS_WS_SETUP=/definitely/not/here/setup.bash python3 - "$ROS_ENV" <<'PY' 2>/dev/null
import importlib.util, sys
spec = importlib.util.spec_from_file_location("t", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print("CANDS=" + str(len(m.overlay_candidates())))
print("SETUP=" + str(m.overlay_setup()))
PY
)"
if echo "$OVERRIDE_OUT" | grep -q "^CANDS=1$" && echo "$OVERRIDE_OUT" | grep -q "^SETUP=None$"; then
  pass_case "명시 지정이 유일 후보가 되고, 없으면 폴백 없이 실패한다"
else
  fail_case "명시 지정이 조용히 무시된다 — 오타 난 경로를 줘도 다른 overlay 가 선택됨 ($OVERRIDE_OUT)"
fi

echo "[test] bash 로 넘기는 경로가 인용되는가 (\$·공백 안전)"
# ⚠️ 브리핑 원문은 파일명에 임시디렉토리 절대경로(d)를 그대로 이어붙였다
#    (name = "setup$(touch " + d + "/PWNED).bash"). 그런데 POSIX 파일명은 '/'를
#    포함할 수 없다 — pathlib 는 인자를 몇 개로 나눠 넘기든 각 인자 내부의 '/'도
#    전부 경로 구분자로 다시 쪼갠다. 즉 그 이름으로는 실제 파일을 만들 수 없고
#    write_text() 가 항상 FileNotFoundError 로 죽는다(실측: 아래 참고, 수정 전/후
#    동일하게 크래시 — 이 코드가 맞는지 여부와 무관하게 항상 실패해 아무것도
#    증명하지 못한다). '/' 없이 같은 취약점을 재현하도록 상대경로로 바꿨다:
#    이름에는 '/' 없는 "$(touch PWNED)" 만 담고, subprocess 의 cwd 를 d 로 줘서
#    그 상대 touch 가 d/PWNED 를 만들게 한다.
QUOTE_OUT="$(python3 - "$ROS_ENV" <<'PY' 2>/dev/null
import importlib.util, os, pathlib, subprocess, sys, tempfile
spec = importlib.util.spec_from_file_location("t", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d = tempfile.mkdtemp()
# 리터럴로 $(...) 를 이름에 담은 파일. 인용이 없으면 source 줄에서 명령이 실행된다.
# ('/' 없는 상대경로 touch — cwd=d 로 실행해 d/PWNED 를 만든다)
name = "setup$(touch PWNED).bash"
pathlib.Path(d, name).write_text("")
os.environ["LIBI_ROS_WS_SETUP"] = str(pathlib.Path(d, name))
script = "\n".join(m.env_lines()[1:])          # 시스템 ROS 줄은 제외
subprocess.run(["bash", "-c", script], cwd=d, capture_output=True)
print("PWNED=" + str(pathlib.Path(d, "PWNED").exists()))
PY
)"
if [ "$QUOTE_OUT" = "PWNED=False" ]; then
  pass_case "경로가 인용돼 명령 치환이 일어나지 않는다"
else
  fail_case "경로가 인용되지 않아 bash 가 경로 안의 명령을 실행했다 ($QUOTE_OUT)"
fi

echo "[test] 시스템 ROS 만으로는 못 찾는다는 것(=수정이 필요했던 이유) 확인"
ONLY_SYS="$(bash -c "source '$ROS_SETUP' >/dev/null 2>&1 && ros2 pkg prefix --share pinky_navigation 2>/dev/null")"
if [ -z "$ONLY_SYS" ]; then
  pass_case "시스템 ROS 단독으로는 해석 불가 — overlay 가 반드시 필요"
else
  echo "  ℹ️  시스템 ROS 에도 pinky_navigation 이 있다($ONLY_SYS). 이 환경에선 원래 버그가 안 났을 수 있다."
fi

echo
[ "$FAILED" = "0" ] && echo "[test] 전부 통과" || echo "[test] 실패 있음"
exit "$FAILED"
