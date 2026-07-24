"""부저 ROS2 서비스 노드.

서비스:
  /play_buzzer  (pinky_interfaces/srv/PlayBuzzer) — 단음 beep (블로킹, 짧음)
  /play_melody  (pinky_interfaces/srv/PlayMelody) — 멜로디 재생 (백그라운드 스레드)
  /stop_buzzer  (pinky_interfaces/srv/StopBuzzer)  — 멜로디 중지
"""
import threading
import time

import rclpy
from rclpy.node import Node

from pinky_interfaces.srv import PlayBuzzer, PlayMelody, StopBuzzer

NOTE_FREQ = {
    "C4": 262, "CS4": 277, "D4": 294, "DS4": 311, "E4": 330, "F4": 349,
    "FS4": 370, "G4": 392, "GS4": 415, "A4": 440, "AS4": 466, "B4": 494,
    "C5": 523, "CS5": 554, "D5": 587, "DS5": 622, "E5": 659, "F5": 698,
    "FS5": 740, "G5": 784, "GS5": 831, "A5": 880, "B5": 988,
}

MELODIES = {
    "fur_elise": [
        ("E5", 0.18), ("DS5", 0.18), ("E5", 0.18), ("DS5", 0.18), ("E5", 0.18),
        ("B4", 0.18), ("D5", 0.18), ("C5", 0.18), ("A4", 0.35), (None, 0.08),
        ("C4", 0.18), ("E4", 0.18), ("A4", 0.18), ("B4", 0.35), (None, 0.08),
        ("E4", 0.18), ("GS4", 0.18), ("B4", 0.18), ("C5", 0.35), (None, 0.08),
        ("E4", 0.18), ("E5", 0.18), ("DS5", 0.18), ("E5", 0.18), ("DS5", 0.18),
        ("E5", 0.18), ("B4", 0.18), ("D5", 0.18), ("C5", 0.18), ("A4", 0.45),
    ],
    "school_bell": [
        ("G4", 0.28), ("G4", 0.28), ("A4", 0.28), ("A4", 0.28), ("G4", 0.28),
        ("G4", 0.28), ("E4", 0.55), ("G4", 0.28), ("G4", 0.28), ("E4", 0.28),
        ("E4", 0.28), ("D4", 0.75), (None, 0.12),
        ("G4", 0.28), ("G4", 0.28), ("A4", 0.28), ("A4", 0.28), ("G4", 0.28),
        ("G4", 0.28), ("E4", 0.55), ("G4", 0.28), ("E4", 0.28), ("D4", 0.28),
        ("E4", 0.28), ("C4", 0.75),
    ],
}

PRESETS = {
    "bell":    (1, 1500, 0.2),
    "beep":    (1, 1000, 0.15),
    "alarm":   (3, 2000, 0.2),
    "success": (2, 1800, 0.15),
    "error":   (3,  800, 0.3),
}


class BuzzerNode(Node):
    def __init__(self) -> None:
        super().__init__("pinky_buzzer")
        self._melody_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.create_service(PlayBuzzer, "/play_buzzer", self._on_play_buzzer)
        self.create_service(PlayMelody, "/play_melody", self._on_play_melody)
        self.create_service(StopBuzzer, "/stop_buzzer", self._on_stop_buzzer)
        self.get_logger().info("pinky_buzzer 노드 시작 (/play_buzzer, /play_melody, /stop_buzzer)")

    # ── 단음 beep (동기) ──────────────────────────────────────
    def _on_play_buzzer(self, req: PlayBuzzer.Request, res: PlayBuzzer.Response):
        preset = req.preset or "bell"
        if preset not in PRESETS:
            res.success = False
            res.message = f"알 수 없는 프리셋: {preset}"
            return res
        cnt0, freq0, dur0 = PRESETS[preset]
        cnt  = req.count    if req.count    > 0   else cnt0
        freq = req.freq     if req.freq     > 0   else freq0
        dur  = req.duration if req.duration > 0.0 else dur0
        try:
            from pinkylib import Buzzer
            bz = Buzzer()
            bz.buzzer_start(freq=freq)
            bz.buzzer(cnt=cnt, duration=dur, duty=50)
            bz.buzzer_stop()
            bz.close()
            res.success = True
            res.message = f"{cnt}회 {freq}Hz beep 완료"
        except Exception as exc:
            res.success = False
            res.message = str(exc)
        return res

    # ── 멜로디 (비동기 스레드) ────────────────────────────────
    def _on_play_melody(self, req: PlayMelody.Request, res: PlayMelody.Response):
        if req.melody not in MELODIES:
            res.success = False
            res.message = f"알 수 없는 멜로디: {req.melody}"
            return res
        self._start_melody(req.melody)
        res.success = True
        res.message = f"멜로디 시작: {req.melody}"
        return res

    def _start_melody(self, name: str) -> None:
        with self._lock:
            self._stop_event.set()
            if self._melody_thread and self._melody_thread.is_alive():
                self._melody_thread.join(timeout=1.0)
            self._stop_event.clear()
            self._melody_thread = threading.Thread(
                target=self._melody_worker, args=(name,), daemon=True)
            self._melody_thread.start()

    def _melody_worker(self, name: str) -> None:
        try:
            from pinkylib import Buzzer
            bz = Buzzer()
            for note, duration in MELODIES[name]:
                if self._stop_event.is_set():
                    break
                if note is None:
                    self._stop_event.wait(timeout=duration)
                    continue
                bz.buzzer_start(freq=NOTE_FREQ[note])
                bz.buzzer(cnt=1, duration=duration, duty=50)
                bz.buzzer_stop()
                self._stop_event.wait(timeout=0.035)
            bz.buzzer_stop()
            bz.close()
        except Exception as exc:
            self.get_logger().error(f"멜로디 오류: {exc}")

    # ── 중지 ─────────────────────────────────────────────────
    def _on_stop_buzzer(self, req: StopBuzzer.Request, res: StopBuzzer.Response):
        self._stop_event.set()
        res.success = True
        res.message = "부저 중지"
        return res


def main(args=None):
    rclpy.init(args=args)
    node = BuzzerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
