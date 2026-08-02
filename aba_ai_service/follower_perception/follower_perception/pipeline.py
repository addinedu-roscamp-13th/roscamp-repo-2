import math
from .detection import Detection
from .detector import Detector
from .exit_direction import classify_exit, may_coast
from .posture_gate import PostureGate
from .reid_engine import ReIDEngine
from .target_matcher import TargetMatcher
from .bbox_smoother import BBoxSmoother
from .constants import (
    FRAME_DT, PREDICT_DT, COAST_LIMIT, CALIBRATION_INTERVAL,
    REGISTRATION_STABLE_FRAMES, REGISTRATION_LEARN_SEC, REGISTRATION_MIN_AREA_RATIO,
)


class FollowerPerception:
    """Facade: frame -> Detection. Detect -> track -> identify -> smooth.

    자세 판정(`pose`)은 **선택 사항**이다. 주입하지 않으면 `posture=None` 이 나가고
    소비자는 기존과 똑같이 동작한다 — 자세 모델 없는 배포에서 추종이 죽지 않게 한다.
    """

    def __init__(self, detector=None, reid=None, pose=None):
        self.detector = detector if detector is not None else Detector()
        self.reid = reid if reid is not None else ReIDEngine()
        self.matcher = TargetMatcher(self.reid)
        self.smoother = BBoxSmoother()
        #: 자세 추정기. None 이면 자세 판정을 하지 않는다.
        self.pose = pose
        self.posture_gate = PostureGate()
        self.last_cands = []          # raw YOLO detections from the last run()
        self._last_owner = None       # last TrackedBox seen as owner
        self._miss = 0
        self._frame_count = 0
        self._reg_id = None
        self._reg_streak = 0
        #: 등록 직후 **매 프레임** 갤러리를 채울 남은 프레임 수. 0 이면 평소 주기.
        #: 근거는 `constants.REGISTRATION_LEARN_SEC` 머리말.
        self._learn_frames = 0
        self._last_crop = None        # crop of the most recent registration
        self._last_bbox = None
        #: 마지막으로 판정한 자세. 소실 시 coast 허용 여부를 가른다.
        self._last_posture = None
        #: 놓친 **첫 순간**에 래치한 소실 방향. 매 tick 다시 재면 예측이 흘러가며 바뀐다.
        self._exit_dir = None
        self._frame_shape = None
        #: 이 프레임이 온 카메라와 그 세대. 소비자가 전환 순간의 옛 프레임을 버린다.
        self.camera = None
        self.camera_epoch = 0
        #: 지금 도는 세션 역할("follow"/"guide"/"watch"/"none"/None=모름).
        #: 로봇의 `/libi/perception_role` 이 알려준다(scripts/role_source.py).
        self.role = None

    #: 자세 추정을 실제로 돌릴 역할. **추종 전용**이다 — 근거는 `set_role` 머리말.
    POSE_ROLES = ("follow",)

    # ---- session role ------------------------------------------------
    def set_role(self, name):
        """로봇이 알려준 세션 역할. 자세 추정을 켜고 끄는 유일한 근거다."""
        if name == self.role:
            return
        self.role = name
        # 자세를 더 안 잴 것이면 남은 판정을 비운다. 게이트를 안 풀면 **직전 추종에서
        # 막힌 상태 그대로** 굳는다 — 카메라가 안 바뀌는 전환(watch→guide, 둘 다 뒷캠)
        # 에서는 `set_camera` 의 reset 이 안 돌아 아무도 안 풀어 준다.
        if not self.pose_active:
            self._last_posture = None
            self.posture_gate.reset()

    @property
    def pose_active(self):
        """이번 프레임에 자세를 재고 골격을 그릴 것인가.

        자세 게이트는 **사람에게 다가가는 추종 전용** 안전 규칙이고, 로봇 쪽
        `control_loop._is_guide()` 가 길잡이에서는 그 값을 이미 버린다. 그런데
        골격은 서버가 JPEG 에 구워 보내므로 패널이 끌 수 없다 — 여기서 안 그리면
        길잡이 화면에 안 나온다.

        ⚠️ 역할을 **한 번도 못 받았으면(None) 켠 채로 둔다.** ROS 옵트인 없이 도는
           배포(`--no-ros` 계열, 시험대)에서 역할은 영원히 None 인데, 거기서 꺼 버리면
           추종에서 자세 판정이 통째로 사라진다. 끄는 것은 "길잡이라고 **들었을** 때"
           뿐이다 — 모름과 아님을 구분한다.
        """
        return self.pose is not None and (self.role is None or self.role in self.POSE_ROLES)

    # ---- camera ------------------------------------------------------
    def set_camera(self, name):
        """카메라가 바뀌었다. epoch 를 올리고 **추적 상태만** 비운다.

        ⚠️ `matcher.reset()` 을 부르면 안 된다 — 그건 등록 템플릿까지 지운다.
        전환할 때마다 재등록해야 하는 꼴이 된다. 지울 것은 track_id 잠금과
        스무더뿐이다. 이전 카메라의 track_id 를 새 프레임에 끌고 가면 엉뚱한 사람이
        owner 로 잡힌다.
        """
        if name == self.camera:
            return
        self.camera = name
        self.camera_epoch += 1
        self.matcher.safe_id = None
        self.smoother.reset()
        self._last_owner = None
        self._miss = 0
        self._exit_dir = None
        self._last_posture = None
        self.posture_gate.reset()
        # ⚠️ 카메라가 바뀌면 **그 시점의 모습을 새로 배운다.**
        #
        #   등록은 앞캠에서 하고 길잡이는 뒷캠으로 본다. 같은 사람이라도 시점이 바뀌면
        #   ReID 벡터가 달라져 `REID_THRESHOLD` 를 못 넘는 일이 흔하다 — 그러면 owner 가
        #   안 잡혀 코스팅(주황 박스)만 나오고 길잡이가 영영 출발하지 못한다.
        #   전환 직후 잠깐 매 프레임 채워 새 시점을 갤러리에 넣는다.
        #
        #   ⚠️ `CALIBRATION_ADD_THRESHOLD` 가 그대로 걸리므로 **새로운 각도일 때만**
        #      추가된다. 같은 그림으로 갤러리가 차지 않는다.
        self._learn_frames = int(REGISTRATION_LEARN_SEC / FRAME_DT)

    # ---- registration -------------------------------------------------
    def register(self, frame):
        cands = self.detector.detect(frame)
        target = self._pick_central(cands, frame)
        if target is None:
            self._reg_id = None
            self._reg_streak = 0
            return False
        if target.track_id == self._reg_id:
            self._reg_streak += 1
        else:
            self._reg_id = target.track_id
            self._reg_streak = 1
        if self._reg_streak >= REGISTRATION_STABLE_FRAMES:
            roi = TargetMatcher._crop(frame, target.bbox)
            if roi is None:
                return False
            self.matcher.register(roi)
            # 등록 직후 짧게 **매 프레임** 갤러리를 채운다 — 근거는
            # `constants.REGISTRATION_LEARN_SEC` 머리말(한 장짜리 템플릿의 한계).
            self._learn_frames = int(REGISTRATION_LEARN_SEC / FRAME_DT)
            self.smoother.reset()
            self._last_owner = None
            self._miss = 0
            self._reg_id = None
            self._reg_streak = 0
            self._on_registered()
            return True
        return False

    def _on_registered(self):
        """등록 직후 공통 처리.

        자세 기준 비율을 **다시 잰다.** 기준은 체형뿐 아니라 카메라 높이·거리에 따라
        달라지는데(yolo_pose README: 같은 인형이 1.88 vs 1.66) 로봇은 매번 다른 자리에서
        등록한다. 재는 동안에는 "Calibrating" 이 나가 주행이 멈춘다 — 등록 순간은
        사람이 로봇 앞에 서 있는 시점이라 측정 조건이 마침 맞는다.
        """
        self._last_posture = None
        self._exit_dir = None
        self.posture_gate.reset()
        # 길잡이에서는 다시 재지 않는다 — 아무도 안 쓰는 값을 위해 "Calibrating" 이
        # 몇 초 뜨고, 그 사이 골격이 노란색으로 그려진다.
        if self.pose_active:
            self.pose.recalibrate()

    def _pick_central(self, cands, frame):
        if not cands:
            return None
        h, w = frame.shape[:2]
        frame_area = float(w * h)
        cx0 = w / 2.0
        viable = [c for c in cands
                  if c.area / frame_area >= REGISTRATION_MIN_AREA_RATIO]
        if not viable:
            return None
        # nearest to center; larger area breaks ties
        return min(viable, key=lambda c: (abs(c.cx - cx0), -c.area))

    def register_from_image(self, image_bgr):
        """Register the central person from a SINGLE image (bypasses the
        3-frame stability requirement). Returns the chosen TrackedBox or None."""
        cands = self.detector.detect(image_bgr)
        target = self._pick_central(cands, image_bgr)
        if target is None:
            return None
        roi = TargetMatcher._crop(image_bgr, target.bbox)
        if roi is None:
            return None
        self.matcher.register(roi)
        self.smoother.reset()
        self._last_owner = None
        self._miss = 0
        self._reg_id = None
        self._reg_streak = 0
        self._last_crop = roi
        self._last_bbox = list(target.bbox)
        self._on_registered()
        return target

    def save_profile(self, dir, *, name, source_image=None, registered_at=None):
        if self._last_crop is None:
            raise ValueError("no registered crop to save; call register_from_image first")
        meta = {
            "name": name,
            "registered_at": registered_at,
            "source_image": source_image,
            "bbox": self._last_bbox,
        }
        self.matcher.save(dir, crop_bgr=self._last_crop, meta=meta)

    def load_profile(self, dir, *, strict=False):
        """Load a saved profile into the matcher so tracking can resume without
        re-registration. Cross-backend safe (re-extracts from crop)."""
        self.matcher.load(dir, strict=strict)
        self.smoother.reset()
        self._last_owner = None
        self._miss = 0
        self._frame_count = 0
        # 저장된 프로필을 불러온 것도 '새 대상'이다. 기준 비율은 그때 그 카메라 배치의
        # 값이라 지금 배치에 안 맞는다 — 다시 잰다.
        self._on_registered()

    # ---- runtime ------------------------------------------------------
    def run(self, frame):
        self._frame_count += 1
        if frame is not None:
            self._frame_shape = frame.shape[:2]
        cands = self.detector.detect(frame)
        self.last_cands = cands
        owner_id = self.matcher.match(cands, frame)
        if owner_id is not None:
            owner = next(c for c in cands if c.track_id == owner_id)
            self.smoother.update(owner.cx, owner.cy, owner.area, FRAME_DT)
            self._last_owner = owner
            self._miss = 0
            self._exit_dir = None
            if self.pose_active:
                self._last_posture = self.pose.classify(frame, owner.bbox)
                self.posture_gate.update(self._last_posture)
            elif self.pose is not None:
                # 길잡이 — 자세를 재지 않는다. 남은 값을 **비워야** 한다. 안 비우면
                # 길잡이 직전 추종에서 잡힌 posture 가 그대로 payload 에 실려 나가고,
                # 화면은 이미 안 재는 값을 계속 띄운다(코드는 멀쩡한데 화면만 거짓).
                self._last_posture = None
            # 등록 직후 학습 창에서는 **매 프레임** 채운다. 그 뒤에는 평소 주기.
            #
            # ⚠️ `_crop` 은 빈 영역에서 **`None`** 을 돌려준다(그 함수 머리말 — 프레임
            #    전체로 대체하지 않는다). 그대로 `calibrate` 에 넘기면 `reid.extract(None)`
            #    에서 죽는다. 예전에는 15프레임마다라 드물게 터졌고, 매 프레임으로
            #    바꾸면서 드러났다. 크롭이 없으면 그 프레임은 그냥 건너뛴다 —
            #    학습은 다음 프레임에 하면 되고, 여기서 죽으면 추종 전체가 멈춘다.
            if self._learn_frames > 0 or self._frame_count % CALIBRATION_INTERVAL == 0:
                roi = TargetMatcher._crop(frame, owner.bbox)
                if roi is not None:
                    if self._learn_frames > 0:
                        self._learn_frames -= 1
                    self.matcher.calibrate(roi)
        else:
            if self._miss == 0 and self._last_owner is not None:
                # 놓친 **첫 순간**에만 방향을 래치한다. 매 tick 다시 재면 스무더의
                # 예측이 흘러가면서 분류가 바뀐다 — 옆으로 나갔던 것이 몇 프레임 뒤
                # 중앙으로 읽히는 식이다.
                h, w = self._frame_shape or (0, 0)
                self._exit_dir = classify_exit(
                    self._last_owner.bbox, self.smoother.velocity, w, h)
            self._miss += 1

    def get_latest(self):
        if self._last_owner is None:
            return None
        if self._miss == 0:
            pred = self.smoother.predict(PREDICT_DT)
            if pred is None:
                return None
            cx, cy, area = pred
            area = max(0.0, area)
            bbox = self._last_owner.bbox
            is_pred = False
        elif self._miss <= COAST_LIMIT and may_coast(self._exit_dir, self._last_posture):
            # ⚠️⚠️ [2026-08-02] **코스팅은 "마지막 박스를 그대로 든다" 이다. 외삽이 아니다.**
            #
            #   사용자 지시: "예측 느낌 말고, 유실된 그 시점의 박스를 유지하기만 하면 된다."
            #
            #   예전에는 `smoother.predict(miss * FRAME_DT)` 로 위치·면적을 계속 밀었다.
            #   그게 두 가지를 망가뜨렸다:
            #     ① **면적 붕괴** — 서가·문틀 뒤로 들어가면 사라지기 직전 bbox 가 잘려
            #        면적이 급감한다. 필터가 그 급감을 속도로 학습해 밀고 나가서
            #        실측 재현상 **3프레임 만에 area 가 0** 이 됐다(700 → -459/frame).
            #        화면의 주황 박스가 점으로 쪼그라들어 안 보였고(사용자 보고),
            #        `√area = 0` 이라 거리 PID 가 "아주 멀다"로 읽어 **전속 전진**했다.
            #     ② **위치 폭주** — 가려지기 직전 몇 프레임은 bbox 가 잘리며 중심도
            #        튄다. 그 튐이 속도로 학습돼 엉뚱한 데로 박스를 끌고 갔다.
            #
            #   가려짐이 바꾸는 것은 **겉보기**뿐이다 — 사람의 실제 위치·거리는 그대로다.
            #   1.4초 동안 진짜 이동량은 `LINEAR_X_MAX`(0.06 m/s) × 1.4s ≈ 8cm 로 작다.
            #   그래서 모르면 **안 바뀐 것으로 둔다**: 마지막으로 실제 본 박스를 그대로
            #   내보내고, 그 시간이 지나면 소실로 넘긴다. 화면에서도 박스가 그 자리에
            #   그대로 떠 있다가 사라진다 — 사용자가 원한 동작이다.
            #
            #   ⚠️ 스무더는 **여전히 필요하다.** `_miss == 0` 경로에서 검출 흔들림을
            #      깎고 한 스텝 지연을 보상한다. 여기서만 안 쓴다.
            cx = self._last_owner.cx
            cy = self._last_owner.cy
            area = max(0.0, float(self._last_owner.area))
            bbox = self._last_owner.bbox
            is_pred = True
        else:
            return None
        # ── 코스팅 중의 주행 가부 ─────────────────────────────────────────
        #
        # **마지막으로 실제 본 판정을 그대로 물려받는다.** 코스팅이 권한을 *올려주면*
        # 안 된다 — 안 보이는 동안 "그래도 가도 된다"고 새로 판단할 근거가 없다.
        # 사용자 규칙(2026-08-02): "마지막이 정상이었으면 가고, 아니면 말고."
        #
        # `posture_gate.update()` 는 owner 를 실제로 잡은 프레임에서만 불린다
        # (`run()`). 그래서 소실 중 `allowed` 는 **자동으로 마지막 실제 판정**이고,
        # 여기서 따로 얼려 둘 필요가 없다.
        #
        # ⚠️ 한때 `is_pred` 면 무조건 True 로 덮었다. 그건 진짜 문제를 엉뚱한 데서
        #    막은 것이었다 — 당시 코스팅이 안 걸리던 원인은 ①자세 허용목록이
        #    `Standing` 만 통과시킨 것과 ②`classify_exit` 임계 단위가 20배 예민했던
        #    것이고, 둘 다 각자 자리에서 고쳤다(`exit_direction.py`).
        #    여기를 덮는 것은 "누워 있던 사람도 코스팅 중엔 쫓아간다"가 되어 위험하다.
        #
        # 각 판정이 로봇에서 어떻게 쓰이는지(control_loop.tick):
        #   Standing      → allowed=True  → 그대로 추종
        #   Side          → allowed=False → **전진만 0, 방위는 유지**(2026-07-26 설계)
        #   Lying/Calib   → allowed=False → 완전 정지 (애초에 may_coast 가 먼저 막는다)
        #   Unknown 25연속 → allowed=False → 완전 정지
        motion_ok = self.posture_gate.allowed
        return Detection(
            cx=cx, cy=cy, area=area, bbox=bbox,
            track_id=self._last_owner.track_id, is_owner=True,
            confidence=self._last_owner.confidence, is_predicted=is_pred,
            posture=self._last_posture,
            motion_ok=motion_ok,
            camera=self.camera, camera_epoch=self.camera_epoch,
        )

    def reset(self):
        self.matcher.reset()
        self.smoother.reset()
        self.posture_gate.reset()
        self._last_owner = None
        self._miss = 0
        self._frame_count = 0
        self._reg_id = None
        self._reg_streak = 0
        self._last_posture = None
        self._exit_dir = None
