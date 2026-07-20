class MockDetector:
    """Test double for Detector. Yields scripted TrackedBox lists per frame."""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def detect(self, frame):
        out = self.script[self.i] if self.i < len(self.script) else []
        self.i += 1
        return out

    def reset(self):
        self.i = 0
