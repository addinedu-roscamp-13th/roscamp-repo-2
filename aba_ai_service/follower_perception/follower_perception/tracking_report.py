"""Turn a stream of frames into a persistent-ReID report. Pure w.r.t. IO:
`track_frames` takes any iterable of BGR frames and a perception object, so it
is unit-testable with a MockDetector and no video codec."""


def summarize(records):
    total = len(records)
    owner = sum(1 for r in records if r["owner"])
    predicted = sum(1 for r in records if r["owner"] and r["predicted"])
    max_miss = streak = 0
    for r in records:
        if r["owner"]:
            streak = 0
        else:
            streak += 1
            max_miss = max(max_miss, streak)
    return {
        "frames": total,
        "owner_frames": owner,
        "owner_hold_ratio": (owner / total) if total else 0.0,
        "predicted_frames": predicted,
        "max_miss_streak": max_miss,
    }


def track_frames(frames, perception, *, log=None):
    records = []
    for i, frame in enumerate(frames):
        perception.run(frame)
        det = perception.get_latest()
        is_owner = det is not None and det.is_owner
        is_pred = bool(det.is_predicted) if is_owner else False
        records.append({"owner": is_owner, "predicted": is_pred})
        if log is not None:
            if is_owner:
                rs = perception.matcher.last_reid_sim
                hs = perception.matcher.last_hsv_sim
                rs = f"{rs:.2f}" if rs is not None else "n/a"
                hs = f"{hs:.2f}" if hs is not None else "n/a"
                log(f"frame {i}: owner track_id={det.track_id} "
                    f"reid_sim={rs} hsv_sim={hs} predicted={is_pred}")
            else:
                log(f"frame {i}: owner not found")
    return summarize(records)
