#!/usr/bin/env python3
"""LCD 직접 제어 헬퍼 스크립트 (root 권한 필요).

Usage:
  sudo python3 lcd_ctrl.py emotion <name>
  sudo python3 lcd_ctrl.py image <path>
  sudo python3 lcd_ctrl.py text <json_file_path>
  sudo python3 lcd_ctrl.py stop
"""
import json
import os
import pwd
import signal
import sys
import time

# sudo 실행 시 유저 사이트 패키지 경로 추가 (pinky_lcd 위치)
_sudo_user = os.environ.get("SUDO_USER")
if _sudo_user:
    try:
        _user_home = pwd.getpwnam(_sudo_user).pw_dir
        _user_site = (
            f"{_user_home}/.local/lib/"
            f"python{sys.version_info.major}.{sys.version_info.minor}/"
            "site-packages"
        )
        if _user_site not in sys.path:
            sys.path.insert(0, _user_site)
    except KeyError:
        pass

EMOTION_PATH = "/home/pinky/pinky_pro/install/pinky_emotion/share/pinky_emotion/emotion"
PID_FILE = "/tmp/pinky_lcd_ctrl.pid"
LCD_W, LCD_H = 240, 240


# ── 데몬 공통 ─────────────────────────────────────────────────────────────────

def _kill_existing() -> None:
    if not os.path.exists(PID_FILE):
        return
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.3)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def _daemonize() -> None:
    """현재 프로세스를 데몬화 — 상속받은 PIPE FD를 닫아 부모가 즉시 반환되게 함."""
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)
    sys.stdin = open(os.devnull)
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")


def _fork_and_run(target_fn, *args) -> None:
    """부모: PID 파일 기록 후 즉시 반환 / 자식: target_fn 실행."""
    pid = os.fork()
    if pid > 0:
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        return
    _daemonize()
    try:
        target_fn(*args)
    finally:
        sys.exit(0)


def _load_lcd():
    from pinky_lcd import LCD
    return LCD()


# ── 루프 함수 (자식 데몬에서 실행) ───────────────────────────────────────────

def _loop_gif(path: str) -> None:
    from PIL import Image, ImageSequence
    lcd = _load_lcd()
    img = Image.open(path)
    frames = [
        frame.copy().convert("RGB").resize((LCD_W, LCD_H))
        for frame in ImageSequence.Iterator(img)
    ]
    while True:
        for frame in frames:
            lcd.img_show(frame)
            time.sleep(0.08)


def _loop_static(pil_img) -> None:
    """정적 이미지: 한 번 표시 후 LCD 객체를 살려둠 (소멸자 clear 방지)."""
    lcd = _load_lcd()
    lcd.img_show(pil_img.convert("RGB").resize((LCD_W, LCD_H)))
    while True:
        time.sleep(60)


def _loop_scroll(cfg: dict) -> None:
    """텍스트를 오른쪽→왼쪽으로 흘리는 마퀴 애니메이션."""
    from PIL import Image, ImageDraw, ImageFont

    text = cfg.get("text", "")
    font_path = cfg.get("font_path", "")
    font_size = int(cfg.get("font_size", 28))
    speed = int(cfg.get("scroll_speed", 3))   # 픽셀/프레임

    def _hex(s, fallback):
        s = str(s).lstrip("#")
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return fallback

    color = _hex(cfg.get("color", "#ffffff"), (255, 255, 255))
    bg_color = _hex(cfg.get("bg_color", "#000000"), (0, 0, 0))

    try:
        font = ImageFont.truetype(font_path, font_size) if font_path and os.path.exists(font_path) \
            else ImageFont.load_default(size=font_size)
    except Exception:
        font = ImageFont.load_default()

    # 텍스트 너비 계산
    dummy = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy)
    try:
        bbox = dummy_draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_w = len(text) * font_size
        text_h = font_size

    y = max(0, (LCD_H - text_h) // 2)
    x = LCD_W  # 화면 오른쪽 밖에서 시작

    lcd = _load_lcd()
    total = LCD_W + text_w + 20  # 한 사이클 픽셀 수

    while True:
        frame = Image.new("RGB", (LCD_W, LCD_H), bg_color)
        draw = ImageDraw.Draw(frame)
        draw.text((x, y), text, font=font, fill=color)
        lcd.img_show(frame)
        x -= speed
        if x < -text_w:
            x = LCD_W
        time.sleep(0.04)


def _loop_text_static(cfg: dict) -> None:
    """정적 텍스트를 화면에 표시하고 유지."""
    from PIL import Image, ImageDraw, ImageFont

    text = cfg.get("text", "")
    font_path = cfg.get("font_path", "")
    font_size = int(cfg.get("font_size", 28))
    align = cfg.get("align", "center")

    def _hex(s, fallback):
        s = str(s).lstrip("#")
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return fallback

    color = _hex(cfg.get("color", "#ffffff"), (255, 255, 255))
    bg_color = _hex(cfg.get("bg_color", "#000000"), (0, 0, 0))

    try:
        font = ImageFont.truetype(font_path, font_size) if font_path and os.path.exists(font_path) \
            else ImageFont.load_default(size=font_size)
    except Exception:
        font = ImageFont.load_default()

    img = Image.new("RGB", (LCD_W, LCD_H), bg_color)
    draw = ImageDraw.Draw(img)

    lines = text.replace("\r\n", "\n").split("\n")
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

    _loop_static(img)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("ERROR: command required", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    _kill_existing()

    if cmd == "stop":
        try:
            lcd = _load_lcd()
            lcd.clear()
        except Exception:
            pass
        print("OK: stopped")

    elif cmd == "emotion":
        emotion = sys.argv[2] if len(sys.argv) > 2 else "basic"
        gif_path = os.path.join(EMOTION_PATH, f"{emotion}.gif")
        if not os.path.exists(gif_path):
            print(f"ERROR: 감정 GIF 없음: {gif_path}", file=sys.stderr)
            sys.exit(1)
        _fork_and_run(_loop_gif, gif_path)
        print(f"OK: {emotion}")

    elif cmd == "image":
        if len(sys.argv) < 3:
            print("ERROR: image path required", file=sys.stderr)
            sys.exit(1)
        path = sys.argv[2]
        if not os.path.exists(path):
            print(f"ERROR: 파일 없음: {path}", file=sys.stderr)
            sys.exit(1)
        from PIL import Image
        img = Image.open(path)
        n_frames = getattr(img, "n_frames", 1)
        if n_frames > 1:
            _fork_and_run(_loop_gif, path)
        else:
            _fork_and_run(_loop_static, img.copy())
        print(f"OK: image {os.path.basename(path)}")

    elif cmd == "text":
        config_path = sys.argv[2] if len(sys.argv) > 2 else None
        if config_path and config_path != "-" and os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        else:
            cfg = json.load(sys.stdin)

        if cfg.get("scroll"):
            _fork_and_run(_loop_scroll, cfg)
        else:
            _fork_and_run(_loop_text_static, cfg)
        print("OK: text")

    else:
        print(f"ERROR: 알 수 없는 명령: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
