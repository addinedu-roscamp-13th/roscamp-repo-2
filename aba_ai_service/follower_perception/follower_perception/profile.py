"""Self-contained on-disk person profile: crop.jpg + features.npz + meta.json.

A profile folder is portable — copy it anywhere and it still loads. No absolute
paths are stored. `crop.jpg` is the source of truth so backend-specific
embeddings can be re-extracted on a different machine/backend.
"""
import json
import os

import cv2
import numpy as np

CROP_NAME = "crop.jpg"
FEATURES_NAME = "features.npz"
META_NAME = "meta.json"


def save_profile(dir, *, crop_bgr, reid_vec, hsv_vec, gallery, meta):
    os.makedirs(dir, exist_ok=True)
    cv2.imwrite(os.path.join(dir, CROP_NAME), crop_bgr)
    gallery_arr = np.stack([np.asarray(g, dtype=np.float32) for g in gallery]) \
        if len(gallery) else np.zeros((0, len(reid_vec)), dtype=np.float32)
    np.savez(
        os.path.join(dir, FEATURES_NAME),
        reid=np.asarray(reid_vec, dtype=np.float32),
        hsv=np.asarray(hsv_vec, dtype=np.float32),
        gallery=gallery_arr,
    )
    with open(os.path.join(dir, META_NAME), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def load_profile(dir):
    crop_path = os.path.join(dir, CROP_NAME)
    feats_path = os.path.join(dir, FEATURES_NAME)
    meta_path = os.path.join(dir, META_NAME)
    for p in (crop_path, feats_path, meta_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"profile file missing: {p}")
    crop = cv2.imread(crop_path)
    if crop is None:
        raise FileNotFoundError(f"unreadable crop: {crop_path}")
    feats = np.load(feats_path)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    return {
        "crop": crop,
        "reid": feats["reid"],
        "hsv": feats["hsv"],
        "gallery": feats["gallery"],
        "meta": meta,
    }
