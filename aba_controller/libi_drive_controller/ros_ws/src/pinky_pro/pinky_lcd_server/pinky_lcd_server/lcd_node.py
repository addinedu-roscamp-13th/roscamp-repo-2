"""LCD 텍스트 ROS2 서비스 노드.

서비스:
  /lcd_text  (pinky_interfaces/srv/LcdText)  — 텍스트 표시 (정적/스크롤)
  /lcd_stop  (std_srvs/srv/Trigger)           — LCD 지우기 + 표시 중지

/set_emotion (pinky_bringup) 과의 공존:
  - lcd_ctrl.py 가 사용하는 PID 파일(/tmp/pinky_lcd_ctrl.pid)을 공유 키로 삼아
    새 표시 요청 시 기존 프로세스(emotion GIF 등)를 먼저 종료한다.
  - 반대로 /set_emotion 이 호출되면 lcd_ctrl.py 가 이 노드의 스레드를 멈추지 못하므로
    두 표시가 겹칠 수 있다 → 운영상 한 번에 하나만 사용 권장.
"""
import os
import signal
import threading
import time

import rclpy
from rclpy.node import Node

from pinky_interfaces.srv import LcdText
from std_srvs.srv import Trigger

PID_FILE = "/tmp/pinky_lcd_ctrl.pid"
LCD_W, LCD_H = 240, 240


def _kill_pid_file() -> None:
    """lcd_ctrl.py fork 프로세스(emotion GIF 등) 종료."""
    if not os.path.exists(PID_FILE):
        return
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.2)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def _hex_to_rgb(s: str, fallback: tuple) -> tuple:
    s = str(s).lstrip("#")
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


class LcdNode(Node):
    def __init__(self) -> None:
        super().__init__("pinky_lcd_server")
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.create_service(LcdText, "/lcd_text", self._on_lcd_text)
        self.create_service(Trigger, "/lcd_stop", self._on_lcd_stop)
        self.get_logger().info("pinky_lcd_server 노드 시작 (/lcd_text, /lcd_stop)")

    # ── /lcd_text ────────────────────────────────────────────
    def _on_lcd_text(self, req: LcdText.Request, res: LcdText.Response):
        try:
            self._stop_display()
            _kill_pid_file()
            cfg = {
                "text":         req.text,
                "font_path":    req.font_path,
                "font_size":    req.font_size if req.font_size > 0 else 24,
                "color":        req.color or "#ffffff",
                "bg_color":     req.bg_color or "#000000",
                "align":        req.align or "center",
                "scroll":       req.scroll,
                "scroll_speed": req.scroll_speed if req.scroll_speed > 0 else 3,
            }
            target = self._scroll_loop if cfg["scroll"] else self._static_loop
            self._start_thread(target, cfg)
            res.success = True
            res.message = "텍스트 표시 시작"
        except Exception as exc:
            res.success = False
            res.message = str(exc)
        return res

    # ── /lcd_stop ────────────────────────────────────────────
    def _on_lcd_stop(self, req: Trigger.Request, res: Trigger.Response):
        try:
            self._stop_display()
            _kill_pid_file()
            try:
                from pinky_lcd import LCD
                lcd = LCD()
                lcd.clear()
            except Exception:
                pass
            res.success = True
            res.message = "LCD 중지"
        except Exception as exc:
            res.success = False
            res.message = str(exc)
        return res

    # ── 내부 헬퍼 ────────────────────────────────────────────
    def _stop_display(self) -> None:
        with self._lock:
            self._stop_event.set()
            t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        with self._lock:
            self._stop_event.clear()

    def _start_thread(self, target, *args) -> None:
        with self._lock:
            self._thread = threading.Thread(
                target=target, args=args, daemon=True)
            self._thread.start()

    # ── 정적 텍스트 루프 ──────────────────────────────────────
    def _static_loop(self, cfg: dict) -> None:
        try:
            from PIL import Image, ImageDraw, ImageFont
            from pinky_lcd import LCD

            color    = _hex_to_rgb(cfg["color"],    (255, 255, 255))
            bg_color = _hex_to_rgb(cfg["bg_color"], (0, 0, 0))
            font_size = cfg["font_size"]
            font_path = cfg["font_path"]
            align     = cfg["align"]
            text      = cfg["text"]

            try:
                font = (ImageFont.truetype(font_path, font_size)
                        if font_path and os.path.exists(font_path)
                        else ImageFont.load_default(size=font_size))
            except Exception:
                font = ImageFont.load_default()

            img  = Image.new("RGB", (LCD_W, LCD_H), bg_color)
            draw = ImageDraw.Draw(img)
            lines  = text.replace("\r\n", "\n").split("\n")
            line_h = font_size + 6
            y = max(4, (LCD_H - len(lines) * line_h) // 2)
            for line in lines:
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    tw = bbox[2] - bbox[0]
                except Exception:
                    tw = len(line) * font_size // 2
                if align == "right":
                    x = LCD_W - tw - 6
                elif align == "center":
                    x = max(0, (LCD_W - tw) // 2)
                else:
                    x = 6
                draw.text((x, y), line, font=font, fill=color)
                y += line_h

            lcd = LCD()
            lcd.img_show(img.convert("RGB").resize((LCD_W, LCD_H)))
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=5.0)
        except Exception as exc:
            self.get_logger().error(f"LCD 정적 텍스트 오류: {exc}")

    # ── 스크롤 텍스트 루프 ────────────────────────────────────
    def _scroll_loop(self, cfg: dict) -> None:
        try:
            from PIL import Image, ImageDraw, ImageFont
            from pinky_lcd import LCD

            color    = _hex_to_rgb(cfg["color"],    (255, 255, 255))
            bg_color = _hex_to_rgb(cfg["bg_color"], (0, 0, 0))
            font_size = cfg["font_size"]
            font_path = cfg["font_path"]
            speed     = cfg["scroll_speed"]
            text      = cfg["text"]

            try:
                font = (ImageFont.truetype(font_path, font_size)
                        if font_path and os.path.exists(font_path)
                        else ImageFont.load_default(size=font_size))
            except Exception:
                font = ImageFont.load_default()

            dummy = Image.new("RGB", (1, 1))
            draw  = ImageDraw.Draw(dummy)
            try:
                bbox   = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except Exception:
                text_w = len(text) * font_size
                text_h = font_size

            y   = max(0, (LCD_H - text_h) // 2)
            x   = LCD_W
            lcd = LCD()
            while not self._stop_event.is_set():
                frame = Image.new("RGB", (LCD_W, LCD_H), bg_color)
                ImageDraw.Draw(frame).text((x, y), text, font=font, fill=color)
                lcd.img_show(frame)
                x -= speed
                if x < -text_w:
                    x = LCD_W
                self._stop_event.wait(timeout=0.04)
        except Exception as exc:
            self.get_logger().error(f"LCD 스크롤 오류: {exc}")


def main(args=None):
    rclpy.init(args=args)
    node = LcdNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
