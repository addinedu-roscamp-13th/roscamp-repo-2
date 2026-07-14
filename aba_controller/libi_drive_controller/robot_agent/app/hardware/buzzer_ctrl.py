#!/usr/bin/env python3
"""Buzzer control helper called via sudo from the FastAPI server."""
import argparse
import signal
import sys
import time

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

_running = True


def _handle_stop(_signum, _frame):
    global _running
    _running = False


def _open_buzzer():
    from pinkylib import Buzzer
    return Buzzer()


def play_beep(count: int, freq: int, duration: float):
    bz = _open_buzzer()
    try:
        bz.buzzer_start(freq=freq)
        bz.buzzer(cnt=count, duration=duration, duty=50)
        bz.buzzer_stop()
        print(f"OK: {count} beep(s) at {freq}Hz")
    finally:
        try:
            bz.buzzer_stop()
        finally:
            bz.close()


def play_melody(name: str):
    if name not in MELODIES:
        raise ValueError(f"unknown melody: {name}")

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    bz = _open_buzzer()
    try:
        for note, duration in MELODIES[name]:
            if not _running:
                break
            if note is None:
                time.sleep(duration)
                continue
            bz.buzzer_start(freq=NOTE_FREQ[note])
            bz.buzzer(cnt=1, duration=duration, duty=50)
            bz.buzzer_stop()
            time.sleep(0.035)
        print(f"OK: melody {name} {'stopped' if not _running else 'finished'}")
    finally:
        try:
            bz.buzzer_stop()
        finally:
            bz.close()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    beep = sub.add_parser("beep")
    beep.add_argument("count", type=int, nargs="?", default=1)
    beep.add_argument("freq", type=int, nargs="?", default=1000)
    beep.add_argument("duration", type=float, nargs="?", default=0.25)

    melody = sub.add_parser("melody")
    melody.add_argument("name", choices=sorted(MELODIES))

    parser.add_argument("legacy_count", type=int, nargs="?")
    parser.add_argument("legacy_freq", type=int, nargs="?")
    parser.add_argument("legacy_duration", type=float, nargs="?")
    args = parser.parse_args()

    try:
        if args.command == "melody":
            play_melody(args.name)
        elif args.command == "beep":
            play_beep(args.count, args.freq, args.duration)
        else:
            play_beep(args.legacy_count or 1, args.legacy_freq or 1000, args.legacy_duration or 0.25)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
