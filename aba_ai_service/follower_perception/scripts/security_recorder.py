"""야간 보안 — 침입 판정·클립 녹화·관제 보고.

## 이 모듈이 하는 일

`perception_server` 의 프레임 루프가 `feed()` 를 부르면, 그 안에서
"사람이 연속 N초 보였나 → 클립을 열고 관제에 알린다 → 안 보인 지 M초 → 닫는다"
를 판정한다. 호출자는 결과를 안 본다.

## 절대 예외를 올리지 않는다

`feed()` 가 도는 자리는 `perception_server` 의 **메인 스레드**이고, 그 루프가
YOLO·로봇 검출 송출·추종 제어의 심장이다. 여기서 예외가 새면 **밤에 사람을 쫓던
로봇이 그 자리에 선다.** 그래서 모든 외부 호출을 삼킨다.

## 시계·저장·보고를 전부 주입받는다

실물(ffmpeg·HTTP·카메라) 없이 타임라인 전체를 시험할 수 있다.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from collections import deque

#: 화면에 뜨는 문구. 데모 시드(`seed_demo_data.py`)와 같은 문장이라 목록이 일관된다.
NOTE = "야간 순찰 중 인기척 감지"


@dataclass
class SecurityParams:
    """숫자는 전부 근거가 있다 — 설계문서 §10 참고.

    ⚠️ `trigger_sec` 은 로봇 BT 의 `intruder_sustain`(1.5)보다 **짧아야 한다.**
       그래야 등록이 추종보다 먼저 일어난다(설계문서 §18.2). 두 값은 프로세스가
       달라 서로 신호를 안 주고받고, **순서만으로** 맞춘다.
    """
    trigger_size: float = 100.0   # sqrt(area) @320. ⚠️ 리허설 실측으로 확정할 잠정값
    trigger_sec: float = 1.0      # 연속 지속 = 침입 확정. 로봇(1.5)보다 짧아야 한다
    preroll_sec: float = 3.0      # 링버퍼. 없으면 "들어오는 순간"이 클립에 안 담긴다
    postroll_sec: float = 20.0    # 마지막 목격 이후. 목격할 때마다 갱신(슬라이딩)
    max_clip_sec: float = 120.0   # 파일 길이 상한
    cooldown_sec: float = 30.0    # 클립 종료 후 새 이벤트 억제
    fps: int = 17                 # 링버퍼 길이 계산용


class SecurityRecorder:
    def __init__(self, *, robot_name, media_dir, sink=None, register_fn=None,
                 reset_fn=None, now_fn=None, params=None):
        import time as _time
        self.p = params or SecurityParams()
        self._robot = robot_name
        self._dir = media_dir
        self._sink = sink
        self._register_fn = register_fn
        self._reset_fn = reset_fn
        self._now = now_fn or _time.monotonic
        self._armed = False
        self._want_armed = False     # 폴링 워커가 쓰고 메인 스레드가 읽는 유일한 값
        #: 로봇이 야간 순찰 중인가. **모름은 True** — 상태를 못 받는 배포에서 야간
        #: 녹화가 통째로 사라지면 안 된다(`set_patrol` 머리말).
        self._in_patrol = True
        self._ring = deque(maxlen=max(1, int(self.p.preroll_sec * self.p.fps)))
        self._seen_since = None      # 연속 검출이 시작된 시각
        self._recording = False
        self._clip_start = None
        self._last_seen = None
        self._clip_name = None
        self._cooldown_until = 0.0
        #: 상한으로 강제 종료한 뒤, 대상이 사라지는 것을 계속 지켜보는 상태.
        #: 이게 없으면 등록이 **영영 안 풀린다** — `_tick` 의 그 분기 주석 참고.
        self._pending_release = False

    # ── 외부에서 부르는 것 ────────────────────────────────────────────────

    def arm(self, on: bool) -> None:
        """야간 모드 on/off — **플래그만 세운다.**

        ⚠️ 이 메서드는 `ModePoller` **워커 스레드**에서 불린다. 여기서 상태기계
        (`_ring`·`_recording`·타이머·`_stop()`)를 직접 만지면 메인 프레임 루프와
        동시에 건드리는 것이 되어 경합이 난다. 실제 전이는 `feed()` 가 도는
        **단일 스레드**에서만 한다(`_tick` 첫머리).

        `bool` 대입은 GIL 로 원자적이라 락이 필요 없다.
        """
        self._want_armed = bool(on)

    def set_patrol(self, on: bool) -> None:
        """로봇이 지금 **야간 순찰(SECURITY_PATROL)** 인가.

        `/libi/fsm_state` 의 `current_state` 에서 온다(`fsm_state_source.py`).

        ## 왜 운영 모드만으로는 부족한가

        `arm()` 이 보는 관제 모드는 **"지금이 밤이다"**이지 "이 로봇이 순찰 중이다"가
        아니다. 밤에 로봇이 충전 중이든, 배달을 돌든, 사서가 패널로 추종을 걸든 무장이
        그대로라 **사람만 보이면 클립이 열렸다.** 둘을 AND 로 묶는다.

        ⚠️ **메인 프레임 루프에서만 부른다** — `arm()` 과 달리 `wants_armed` 를 통해
           상태기계 전이에 곧바로 반영된다. 실제 전이는 여전히 `_tick` 안에서만 난다.

        ⚠️ 기본값이 `True` 인 이유: 상태를 못 받는 배포(ROS 옵트인 없음·도메인 불일치)
           에서 야간 녹화가 **조용히 사라지면 안 된다.** 모름은 예전 동작으로 떨어진다.
        """
        self._in_patrol = bool(on)

    @property
    def wants_armed(self) -> bool:
        """관제가 「야간」을 원하고 **로봇도 순찰 중인가.**

        `_armed`(실제 전이 완료)가 아니라 이쪽을 노출한다. 전이는 `feed()` 안에서만
        일어나는데, 호출자(`perception_server.serve_loop`)는 **feed 를 부를지 말지**를
        정하려고 이 값을 읽기 때문이다. `_armed` 를 보면 "무장하려면 feed 가 필요한데
        feed 를 하려면 무장돼 있어야 하는" 교착이 된다.

        ⚠️ 순찰에서 빠지면 여기가 False 로 떨어지고, `_tick` 첫머리의 **기존 해제
           경로**가 그대로 돈다 — 진행 중 클립을 닫고 등록을 푼다. 새 상태기계를
           만들지 않는 이유가 그것이다(그 경로는 이미 시험이 붙어 있다).
        """
        return self._want_armed and self._in_patrol

    def end_chase(self) -> None:
        """로봇이 추격을 접었다 — `postroll` 을 기다리지 않고 **지금** 닫는다.

        호출자는 `/libi/perception_role` 이 `security` 에서 빠지는 순간을 본다
        (`perception_server._chase_over`). 그 전이는 `IntruderChase._release` 가
        `follow_stop` 을 낸 결과라, **회복 BT 가 끝난 바로 그 시점**이다.

        ## 왜 시계(`postroll_sec`)로 안 하나

        두 프로세스가 같은 숫자를 각자 들고 있으면 한쪽만 고쳤을 때 조용히 어긋난다
        (`sustain_sec` 머리말의 그 문제). 로봇이 이미 "끝났다"고 말해 주므로 그 말을
        듣는 편이 정확하다. `postroll_sec` 은 **역할을 못 받을 때의 폴백**으로 남는다
        (ROS 옵트인이 꺼졌거나 도메인이 안 맞으면 `role_source` 가 아예 없다).

        ⚠️ **메인 프레임 루프에서만 부른다.** `arm()` 과 달리 여기서는 상태기계를
           직접 만진다 — 워커 스레드에서 부르면 `feed()` 와 경합한다.

        ⚠️ **녹화 중이 아니면 아무것도 안 한다.** `_stop()` 이 쿨다운을 새로 거므로,
           유휴 상태에서 불리면 쿨다운이 계속 밀려 **새 클립이 영영 안 열린다.**
        """
        if self._recording:
            self._stop(self._now(), release=True)
        elif self._pending_release:
            # 상한으로 파일은 이미 닫혔고 등록만 남아 있었다. 여기서 푼다 —
            # `_stop` 을 또 부르면 닫힌 sink 를 다시 닫는다.
            self._release_registration()
            self._pending_release = False

    def feed(self, jpeg, front_person_size, frame=None, cands=None) -> None:
        """프레임 하나. `jpeg=None` 은 **심장박동**(영상이 안 오는 동안 시계만 돌린다).

        심장박동이 없으면 카메라가 꺼졌을 때 상태기계가 tick 을 못 받아
        **파일이 영영 안 닫히고 `clip_path` 도 안 붙는다.**
        """
        try:
            self._tick(jpeg, float(front_person_size or 0.0), frame, cands)
        except Exception:                                       # noqa: BLE001
            # 여기서 새면 perception_server 메인 루프가 죽는다 — 머리말 참고.
            pass

    def close(self) -> None:
        """프로세스 종료 경로. `atexit`/`SIGTERM` 에서 부른다 — 안 부르면 미완성 mp4 가
        남고 `clip_path` PATCH 도 안 나간다."""
        try:
            if self._recording:
                self._stop(self._now(), release=True)
        except Exception:                                       # noqa: BLE001
            pass

    # ── 내부 ─────────────────────────────────────────────────────────────

    def _tick(self, jpeg, size, frame, cands=None):
        now = self._now()

        # 모드 전이는 **여기서만** 한다 — 워커 스레드가 아니라 이 단일 스레드에서.
        # ⚠️ `_want_armed`(관제 모드)가 아니라 **`wants_armed`(모드 AND 순찰)** 를 본다.
        #    순찰에서 빠지는 것도 여기서 해제로 읽혀야 클립이 닫힌다.
        want = self.wants_armed
        if want != self._armed:
            if not want:
                # 「주간」 전환 또는 순찰 이탈 — 진행 중 클립을 즉시 닫는다. 안 그러면
                # 미완성 mp4 가 남아 영영 재생이 안 된다(파일 핸들도 안 닫힌다).
                if self._recording:
                    self._stop(now, release=True)
                elif self._pending_release:
                    # 상한 종료 뒤 소실을 기다리던 중 주간으로 꺼졌다 — 여기서
                    # 안 풀면 다음 재시작까지 영영 안 풀린다(위 `_pending_release`
                    # 필드 주석·`_release_registration` 머리말 참고).
                    self._release_registration()
                self._ring.clear()
                self._seen_since = None
                self._pending_release = False
            self._armed = want

        if not self._armed:
            return
        if jpeg is not None:
            self._ring.append(jpeg)

        seen = size >= self.p.trigger_size

        # 상한으로 강제 종료한 뒤 — 파일은 닫혔지만 등록은 아직 살아 있다.
        # 대상이 사라지는 것을 계속 지켜봐야 한다. 안 지켜보면 `reset_fn` 이 **영영 안
        # 불려** 다음 밤의 침입자가 어제 갤러리에 매칭된다.
        if self._pending_release:
            if seen:
                self._last_seen = now
            elif now - self._last_seen >= self.p.postroll_sec:
                self._release_registration()
                self._pending_release = False

        if self._recording:
            if jpeg is not None:
                self._call(self._sink.write, jpeg)
            if seen:
                self._last_seen = now
            if now - self._clip_start >= self.p.max_clip_sec:
                # 사람은 아직 보인다 — 파일만 닫고 **등록은 유지한다.**
                # 여기서 지우면 로봇이 한창 쫓는 중에 대상이 증발한다.
                # 대신 `_pending_release` 로 넘겨 소실을 계속 지켜본다.
                self._stop(now, release=not seen)
                if seen:
                    self._pending_release = True
                return
            if now - self._last_seen >= self.p.postroll_sec:
                # 정의상 "이만큼 아무도 안 보였다" — 로봇도 이미 포기했다. 안전하다.
                self._stop(now, release=True)
            return

        if not seen:
            self._seen_since = None
            return
        if self._seen_since is None:
            self._seen_since = now
            return
        # ponytail: 1e-9 관용도 — `clock.t += 0.1` 을 10번 누적하면 부동소수점
        # 오차로 1.0 에 미세하게 못 미쳐(0.9999999999999999) 정확히 trigger_sec
        # 프레임 수만큼만 보낸 시험에서 트리거가 영영 안 붙는다. 실사용(진짜
        # time.monotonic())에서는 프레임이 계속 오므로 안 드러났을 문제.
        if now - self._seen_since < self.p.trigger_sec - 1e-9:
            return
        if now < self._cooldown_until:
            return
        self._start(now, frame, cands)

    def _start(self, now, frame, cands=None):
        self._clip_name = f"{uuid.uuid4()}.mp4"
        self._call(self._sink.open_clip, self._clip_name)
        for buf in self._ring:                 # 프리롤을 먼저 흘려넣는다
            self._call(self._sink.write, buf)
        if self._register_fn is not None and frame is not None:
            # 이미 뽑아 둔 후보를 그대로 넘긴다 — 트리거 프레임에서 YOLO 가 두 번
            # 돌면 메인 루프가 그만큼 멈춘다(register_nearest 머리말).
            self._call(self._register_fn, frame, cands)
        # `clip_name` 이 키다 — 응답을 기다리지 않는다(머리말의 동기 HTTP 금지).
        self._call(self._sink.report, self._clip_name, NOTE)
        self._recording = True
        self._pending_release = False
        self._clip_start = now
        self._last_seen = now

    def _stop(self, now, *, release):
        ok = self._call(self._sink.close_clip)
        if ok and self._clip_name is not None:
            self._call(self._sink.attach_clip, self._clip_name,
                       f"/api/admin/ops/security/clips/{self._clip_name}")
        if release:
            self._release_registration()
        self._recording = False
        self._clip_name = None
        self._seen_since = None
        self._cooldown_until = now + self.p.cooldown_sec

    def _release_registration(self):
        """ReID 등록을 푼다. **여기 한 곳에서만** 부른다 — 정상 만료(`_stop`)와
        상한 종료 뒤 소실(`_pending_release`) 두 경로가 같은 자리로 모여야
        두 번 불리거나 한 번도 안 불리는 일이 없다."""
        if self._reset_fn is not None:
            self._call(self._reset_fn)

    @staticmethod
    def _call(fn, *args):
        """어떤 sink 예외도 상태기계를 멈추지 못하게 한다."""
        if fn is None:
            return None
        try:
            return fn(*args)
        except Exception:                                       # noqa: BLE001
            return None


def resolve_ffmpeg():
    """`SECURITY_FFMPEG` → `PATH` 순. 없으면 `None`.

    실측 2026-08-03: 이 머신의 ffmpeg 은 `/home/ane/.local/bin/ffmpeg`(사용자 홈
    static 빌드)라 pm2/systemd PATH 에 안 잡힐 수 있다. 그래서 환경변수를 먼저 본다.
    """
    env = os.environ.get("SECURITY_FFMPEG")
    if env:
        return env
    return shutil.which("ffmpeg")


class FfmpegClipWriter:
    """JPEG 를 그대로 파이프로 흘려 H.264 mp4 를 만든다.

    ## 왜 별도 스레드인가 — 녹화가 제어를 죽이면 안 된다

    `stdin.write()` 는 파이프 버퍼(리눅스 64KB)가 차면 **블록한다.** 그 자리가
    `perception_server` 메인 스레드라, 막히면 YOLO·검출 송출·추종이 통째로 멈춘다
    (밤에 사람 쫓던 로봇이 그 자리에 선다). 그래서 **유한 큐 + writer 스레드**로
    떼어내고, **큐가 차면 프레임을 버린다.** 녹화 품질 < 제어 안전.

    ## 왜 재인코딩이 없나

    `perception_server` 가 뷰어용으로 이미 JPEG 을 만든다. `-f image2pipe` 로 그
    바이트를 그대로 먹인다.
    """

    def __init__(self, media_dir, *, fps=17, ffmpeg_path=None, queue_max=60):
        self._dir = media_dir
        self._fps = int(fps)
        self._bin = ffmpeg_path if ffmpeg_path is not None else resolve_ffmpeg()
        self._q = queue.Queue(maxsize=queue_max)
        self._proc = None
        self._thread = None
        self._path = None
        self.dropped = 0

    def open_clip(self, name):
        if self._bin is None:
            return
        os.makedirs(self._dir, exist_ok=True)
        self._path = os.path.join(str(self._dir), name)
        proc = subprocess.Popen(
            [self._bin, "-hide_banner", "-loglevel", "error",
             "-f", "image2pipe", "-vcodec", "mjpeg",
             "-framerate", str(self._fps), "-i", "-",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-y", self._path],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        # ⚠️ 새 Queue/proc 를 만들고 스레드 인자로 넘긴다 — `_pump` 가 `self.*` 를
        #    돌면서 읽으면, close_clip() 의 join(timeout=5) 이 만료돼도 옛 스레드가
        #    살아 있을 수 있고, 그 스레드가 **다음** open_clip() 이 갈아 끼운
        #    self._proc/self._q 로 쓰기 시작해 새 클립이 조용히 망가진다.
        q = queue.Queue(maxsize=self._q.maxsize)
        self._proc, self._q = proc, q
        self._thread = threading.Thread(target=self._pump, args=(proc, q), daemon=True)
        self._thread.start()

    def write(self, jpeg):
        """**절대 블록하지 않는다.** 큐가 차면 버리고 센다."""
        if self._proc is None:
            return
        try:
            self._q.put_nowait(jpeg)
        except queue.Full:
            self.dropped += 1

    def close_clip(self):
        if self._proc is None:
            return False
        # ⚠️ `put(None)` 은 큐가 차 있으면 **블록한다** — 여기도 메인 스레드다.
        #    자리가 없으면 오래된 프레임을 버려서라도 자리를 만든다.
        while True:
            try:
                self._q.put_nowait(None)
                break
            except queue.Full:
                try:
                    self._q.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    continue
        if self._thread is not None:
            self._thread.join(timeout=5)
        proc, self._proc, self._thread = self._proc, None, None
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:                       # noqa: BLE001
            proc.kill()                         # 좀비를 남기지 않는다
            return False
        return proc.returncode == 0 and os.path.exists(self._path)

    def _pump(self, proc, q):
        while True:
            item = q.get()
            if item is None:
                return
            try:
                proc.stdin.write(item)
            except (BrokenPipeError, ValueError, AttributeError, OSError):
                # 디스크가 찼거나 ffmpeg 이 죽었다 — 이 클립만 실패로 끝난다.
                return


def _default_post(url, json, timeout):
    import requests
    r = requests.post(url, json=json, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _default_patch(url, json, timeout):
    import requests
    requests.patch(url, json=json, timeout=timeout).raise_for_status()


def _default_get(url, timeout):
    import requests
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


class OpsSink:
    """클립 저장은 `writer` 에 맡기고, 관제 보고를 담당한다.

    ## HTTP 는 워커 하나 + 유한 큐다

    호출마다 `Thread()` 를 만들면 관제가 느려질 때 스레드가 쌓인다. 그리고
    호출부(메인 루프)는 **결과를 안 기다린다** — 기다리면 그만큼 추종 반응이 늦는다.
    보고 자체(`report`)만은 `event_id` 가 필요해 동기로 부르되, 짧은 타임아웃을 건다.
    """

    #: PATCH 재시도 상한. 관제가 잠깐 재시작해도 영상이 붙게 한다.
    MAX_ATTACH_RETRY = 3

    def __init__(self, base_url, robot_name, writer, *,
                 post_fn=None, patch_fn=None, queue_max=32):
        self._base = base_url.rstrip("/")
        self._robot = robot_name
        self._w = writer
        self._post = post_fn or _default_post
        self._patch = patch_fn or _default_patch
        self._q = queue.Queue(maxsize=queue_max)
        #: clip_name → event_id. 워커 스레드만 읽고 쓴다.
        self._ids = {}
        self._worker = threading.Thread(target=self._pump, daemon=True)
        self._worker.start()

    # sink 프로토콜 — 클립
    def open_clip(self, name):
        self._w.open_clip(name)

    def write(self, jpeg):
        self._w.write(jpeg)

    def close_clip(self):
        return bool(self._w.close_clip())

    # sink 프로토콜 — 보고. **둘 다 큐에 넣기만 하고 즉시 돌아온다.**
    def report(self, clip_name, note):
        """`clip_name` 을 키로 침입을 보고한다.

        ⚠️ **`event_id` 를 돌려주지 않는다.** 돌려받으려면 메인 루프에서 HTTP 를 최대
        3초 기다려야 하는데, 그 자리가 YOLO·검출송출·추종의 심장이다. 워커가 응답의
        id 를 `clip_name` 에 저장해 두고, 뒤이은 `attach_clip` 이 꺼내 쓴다 —
        **워커가 하나라 순서는 FIFO 로 보장된다.**

        `zone` 은 일부러 안 싣는다. AI 서버는 로봇이 어느 정점에 있는지 모른다 —
        관제(`ops_extra._zone_from_fleet`)가 플릿 스냅샷에서 채운다.
        """
        self._enqueue(("report", clip_name, note))

    def attach_clip(self, clip_name, clip_path):
        self._enqueue(("attach", clip_name, clip_path, 0))

    def shutdown(self):
        """워커를 비우고 끝낸다 — 프로세스 종료 시 마지막 PATCH 가 나가야 한다.

        ⚠️ 종료 신호(`None`)를 넣기 **전에** `_q.join()` 으로 재시도까지 전부
        빠지길 기다린다. 먼저 넣으면 실패한 PATCH 가 다시 큐에 넣은 재시도가
        그 `None` 뒤에 줄을 서고, 워커는 `None` 을 먼저 만나 빠져나가 재시도가
        영영 안 나간다 — `queue.Queue` 는 FIFO 라 순서를 안 봐주지 않는다.
        """
        self._q.join()
        self._enqueue(None)
        self._worker.join(timeout=5)

    def _enqueue(self, item):
        try:
            self._q.put_nowait(item)
        except queue.Full:
            pass                    # 큐가 차면 버린다. 메인 루프를 막지 않는 게 우선이다

    def _pump(self):
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                return
            kind = item[0]
            try:
                if kind == "report":
                    _, clip_name, note = item
                    got = self._post(
                        f"{self._base}/api/admin/ops/security/events",
                        json={"source": self._robot, "note": note},
                        timeout=(1, 3))
                    self._ids[clip_name] = (got or {}).get("id")
                else:
                    _, clip_name, clip_path, tries = item
                    event_id = self._ids.get(clip_name)
                    if event_id is None:
                        continue    # 보고 자체가 실패했다 — 붙일 곳이 없다
                    self._patch(
                        f"{self._base}/api/admin/ops/security/events/{event_id}/clip",
                        json={"clip_path": clip_path}, timeout=(1, 3))
                    self._ids.pop(clip_name, None)
            except Exception:                                   # noqa: BLE001
                # PATCH 만 재시도한다 — 관제가 잠깐 재시작하면 영상이 영원히 안 붙는다.
                if kind == "attach" and item[3] < self.MAX_ATTACH_RETRY:
                    self._enqueue(("attach", item[1], item[2], item[3] + 1))
            finally:
                self._q.task_done()


class ModePoller:
    """운영 모드를 주기 폴링해 `on_change(True|False)` 로 알린다.

    ⚠️ **어떤 예외로도 이 루프가 끝나면 안 된다.** 끝나면 마지막으로 읽은 모드에
    영원히 고정된다 — 주간인데 계속 녹화하거나, 야간인데 무장 안 된 채로 남는다.
    초기값은 `day`(무장 안 함)다.
    """

    def __init__(self, base_url, on_change, *, get_fn=None, interval=10.0):
        self._base = base_url.rstrip("/")
        self._cb = on_change
        self._get = get_fn or _default_get
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                got = self._get(f"{self._base}/api/admin/ops/security/mode", timeout=3)
                self._cb((got or {}).get("mode") == "night")
            except Exception:                                   # noqa: BLE001
                pass                                            # 마지막 모드 유지
            self._stop.wait(self._interval)
