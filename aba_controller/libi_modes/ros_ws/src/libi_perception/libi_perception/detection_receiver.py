from .detection import detection_from_dict


class DetectionReceiver:
    """Holds the latest owner Detection parsed from incoming JSON dicts.

    `source.poll()` returns a list of payloads received since last poll;
    each payload is a Detection dict, or None meaning 'no owner this frame'.
    Concrete TCP socket wraps this small interface (integration-tested)."""

    def __init__(self, source):
        self._source = source
        self._latest = None

    def update(self):
        for payload in self._source.poll():
            self._latest = detection_from_dict(payload)

    def latest(self):
        return self._latest
