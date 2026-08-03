import numpy as np

from .color_hist import hsv_hist, hist_similarity
from .profile import save_profile, load_profile
from .constants import (
    REID_THRESHOLD, HSV_THRESHOLD,
    CALIBRATION_ADD_THRESHOLD, MAX_GALLERY_SIZE, HSV_UPDATE_ALPHA,
)


def reid_backend_name(reid):
    """Best-effort backend label for compatibility checks."""
    return getattr(reid, "_backend", reid.__class__.__name__)


class TargetMatcher:
    """Identifies the owner among tracked candidates via a ReID + HSV dual gate,
    re-verified EVERY frame (no permanent lock) so the owner is re-identified by
    appearance after occlusion / track-id changes and impostors are rejected.

    reid_threshold / hsv_threshold: per-gate floors. hsv_threshold=None disables
    the HSV gate (ReID only) — useful when a single-photo HSV template is fragile
    under new lighting.
    """

    def __init__(self, reid, hsv_threshold=HSV_THRESHOLD, reid_threshold=REID_THRESHOLD):
        self.reid = reid
        self.hsv_threshold = hsv_threshold
        self.reid_threshold = reid_threshold
        self.template_reid = None
        self.template_hsv = None
        self.gallery = []
        self.safe_id = None            # current owner track_id this frame, or None
        self.last_reid_sim = None
        self.last_hsv_sim = None

    @property
    def is_registered(self):
        return self.template_reid is not None

    def register(self, roi_bgr):
        self.template_reid = self.reid.extract(roi_bgr)
        self.template_hsv = hsv_hist(roi_bgr)
        self.gallery = [self.template_reid]
        self.safe_id = None

    def reset(self):
        self.template_reid = None
        self.template_hsv = None
        self.gallery = []
        self.safe_id = None

    def save(self, dir, *, crop_bgr, meta):
        if not self.is_registered:
            raise ValueError("cannot save: matcher is not registered")
        full_meta = dict(meta)
        full_meta["reid_backend"] = reid_backend_name(self.reid)
        full_meta["feat_dim"] = int(getattr(self.reid, "feat_dim", len(self.template_reid)))
        save_profile(
            dir,
            crop_bgr=crop_bgr,
            reid_vec=self.template_reid,
            hsv_vec=self.template_hsv,
            gallery=self.gallery,
            meta=full_meta,
        )

    def load(self, dir, *, strict=False):
        data = load_profile(dir)
        meta = data["meta"]
        cur_backend = reid_backend_name(self.reid)
        cur_dim = int(getattr(self.reid, "feat_dim", len(data["reid"])))
        matched_backend = (meta.get("reid_backend") == cur_backend
                           and int(meta.get("feat_dim", -1)) == cur_dim)
        self.template_hsv = np.asarray(data["hsv"], dtype=np.float32)
        if matched_backend:
            self.template_reid = np.asarray(data["reid"], dtype=np.float32)
            gal = data["gallery"]
            self.gallery = [np.asarray(g, dtype=np.float32) for g in gal] \
                if len(gal) else [self.template_reid]
        else:
            if strict:
                raise ValueError(
                    f"profile backend {meta.get('reid_backend')}"
                    f"({meta.get('feat_dim')}) != current {cur_backend}({cur_dim})")
            crop = data["crop"]
            if crop is None or not getattr(crop, "size", 0):
                raise ValueError("backend mismatch and no usable crop to re-extract")
            # Portability: re-extract the embedding with the current backend.
            print(f"[TargetMatcher] backend mismatch "
                  f"({meta.get('reid_backend')} -> {cur_backend}); "
                  f"re-extracting embedding from crop.jpg")
            self.template_reid = self.reid.extract(crop)
            self.gallery = [self.template_reid]
        self.safe_id = None

    def match(self, cands, frame):
        """Re-run the ReID+HSV AND gate on every candidate, every frame. The
        best gate-passing candidate becomes the owner; returns its track_id, or
        None if none passes (owner not visible this frame). Re-acquisition after
        occlusion / track-id change is automatic. `last_reid_sim`/`last_hsv_sim`
        reflect the strongest candidate seen (for on-screen tuning)."""
        if not self.is_registered:
            return None
        owner_tid = None
        owner_score = -1.0
        best_rs = best_hs = None
        best_seen = -1.0
        for c in cands:
            roi = self._crop(frame, c.bbox)
            if roi is None:
                continue          # 빈 crop 인 후보는 owner 후보에서 제외한다
            reid_vec = self.reid.extract(roi)
            hsv_vec = hsv_hist(roi)
            reid_sim = max(self.reid.similarity(g, reid_vec) for g in self.gallery)
            hsv_sim = hist_similarity(self.template_hsv, hsv_vec)
            if reid_sim > best_seen:            # strongest candidate (for overlay)
                best_seen = reid_sim
                best_rs, best_hs = reid_sim, hsv_sim
            hsv_ok = self.hsv_threshold is None or hsv_sim >= self.hsv_threshold
            if reid_sim >= self.reid_threshold and hsv_ok and reid_sim > owner_score:
                owner_tid = c.track_id
                owner_score = reid_sim
        if best_rs is not None:
            self.last_reid_sim = best_rs
            self.last_hsv_sim = best_hs
        self.safe_id = owner_tid
        return owner_tid

    def calibrate(self, owner_roi):
        # ReID: append a novel view to the gallery (discrete angles).
        reid_vec = self.reid.extract(owner_roi)
        best = max((self.reid.similarity(g, reid_vec) for g in self.gallery),
                   default=0.0)
        if best < CALIBRATION_ADD_THRESHOLD and len(self.gallery) < MAX_GALLERY_SIZE:
            self.gallery.append(reid_vec)
        # HSV: EMA the template toward current lighting (continuous drift).
        if HSV_UPDATE_ALPHA > 0:
            cur = hsv_hist(owner_roi)
            self.template_hsv = ((1.0 - HSV_UPDATE_ALPHA) * self.template_hsv
                                 + HSV_UPDATE_ALPHA * cur).astype(np.float32)

    @staticmethod
    def _crop(frame, bbox):
        """bbox 로 잘라낸 영역. **비어 있으면 `None`** — 프레임 전체로 대체하지
        않는다.

        ⚠️ 예전엔 빈 ROI 일 때 프레임 전체를 돌려줬다. 그 프레임에 진짜 owner가
        있으면 이 '크롭'이 owner 템플릿과 맞아, 화면 밖으로 잘린 엉뚱한 후보가
        owner 로 이긴다 — "이 후보가 맞나?"가 "이 화면 어딘가에 맞는 사람이
        있나?"로 바뀌는 범주 오류였다(2026-08-01 codex 지적).
        """
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]
