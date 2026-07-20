import cv2
import numpy as np

from .constants import HSV_BINS


def hsv_hist(roi_bgr) -> np.ndarray:
    """48-d normalized HSV histogram (HSV_BINS per channel, concatenated)."""
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0], None, [HSV_BINS], [0, 180])
    s = cv2.calcHist([hsv], [1], None, [HSV_BINS], [0, 256])
    v = cv2.calcHist([hsv], [2], None, [HSV_BINS], [0, 256])
    hist = np.concatenate([h, s, v]).flatten().astype(np.float32)
    total = float(hist.sum())
    if total > 0:
        hist /= total
    return hist


def hist_similarity(a, b) -> float:
    """Per-channel (H, S, V) Pearson correlation mapped to [0, 1]; the overall
    similarity is the minimum across channels so a strong match in one channel
    (e.g. saturation/value) cannot mask a mismatch in another (e.g. hue)."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    sims = []
    for i in range(0, len(a), HSV_BINS):
        corr = cv2.compareHist(a[i:i + HSV_BINS], b[i:i + HSV_BINS], cv2.HISTCMP_CORREL)
        sims.append(max(0.0, min(1.0, (corr + 1.0) / 2.0)))
    return min(sims)
