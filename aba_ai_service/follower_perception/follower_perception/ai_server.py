from .pipeline import FollowerPerception


def detection_to_dict(det, frame=None):
    """검출 → 전송용 dict.

    ## [2026-07-30] `image_width` 를 반드시 실어 보낸다

    `cx`·`area` 는 **이 프레임의 픽셀 좌표**다. 받는 쪽(로봇 `libi_perception`)의 PID 는
    그 값을 기준 폭으로 환산해야 하는데, 해상도를 모르면 환산을 못 해 `config.IMAGE_WIDTH`
    (=640)라고 **가정**한다. 그 가정이 틀리면:

      · 조향: 화면 중심을 640/2 로 잡는다 → 320 폭에서는 실제 중심(160)과 어긋나
        사람이 늘 한쪽에 있다고 판단해 계속 헛돈다.
      · 거리: `sqrt(area)` 가 선형으로 작아진다 → 목표치(`TARGET_SIZE`)에 영영 못 닿아
        **전진을 멈추지 않는다**(2026-07-28 "그냥 들이박네"와 같은 조건).

    로봇 `config.py:30-35` 가 "소스가 해상도를 안 실어 보내는 동안에는 폴백이 쓰이므로
    **소스를 고치는 것이 우선**"이라고 적어 둔 그 소스가 여기다. 2026-07-30 에 카메라를
    320x240 으로 내리면서 실제로 물렸다.

    `frame` 을 안 주면 키를 안 넣는다 — 받는 쪽이 폴백(640 가정)으로 떨어진다.
    """
    if det is None:
        return None
    out = {
        "cx": det.cx, "cy": det.cy, "area": det.area, "bbox": list(det.bbox),
        "track_id": det.track_id, "is_owner": det.is_owner,
        "confidence": det.confidence, "is_predicted": det.is_predicted,
    }
    if frame is not None:
        h, w = frame.shape[:2]
        out["image_width"] = int(w)
        out["image_height"] = int(h)
    return out


class AiServer:
    """Thin adapter around per-source FollowerPerception instances.

    Transports are injected. Each is a tiny object:
      frame_source.next()   -> (source_id, frame) or None
      command_source.poll() -> list[{"cmd","source"}]
      result_sink.send(source_id, payload_dict_or_none)
    """

    def __init__(self, frame_source, result_sink, command_source,
                 make_perception=FollowerPerception):
        self._frames = frame_source
        self._sink = result_sink
        self._commands = command_source
        self._make = make_perception
        self._perceptions = {}
        self._last_frame = {}

    def _perc(self, source_id):
        if source_id not in self._perceptions:
            self._perceptions[source_id] = self._make()
        return self._perceptions[source_id]

    def process_once(self):
        # 1. Record the freshest incoming frame for its source, if any.
        item = self._frames.next()
        if item is not None:
            source_id, frame = item
            self._last_frame[source_id] = frame
        # 2. Apply pending commands (register/reset) from ABA, using the
        #    freshest frame recorded for their source.
        for cmd in self._commands.poll():
            source = cmd.get("source")
            perc = self._perc(source)
            if cmd.get("cmd") == "register" and source in self._last_frame:
                perc.register(self._last_frame[source])
            elif cmd.get("cmd") == "reset":
                perc.reset()
        # 3. Run perception on the frame just received and emit a result.
        if item is None:
            return
        perc = self._perc(source_id)
        perc.run(frame)
        # 방금 추론한 그 프레임을 같이 넘긴다 — cx/area 가 이 프레임의 픽셀 좌표라
        # 받는 쪽이 해상도를 알아야 환산한다(detection_to_dict 주석).
        self._sink.send(source_id, detection_to_dict(perc.get_latest(), frame))
