from .pipeline import FollowerPerception


def detection_to_dict(det):
    if det is None:
        return None
    return {
        "cx": det.cx, "cy": det.cy, "area": det.area, "bbox": list(det.bbox),
        "track_id": det.track_id, "is_owner": det.is_owner,
        "confidence": det.confidence, "is_predicted": det.is_predicted,
    }


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
        self._sink.send(source_id, detection_to_dict(perc.get_latest()))
