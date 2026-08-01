# follower_perception

YOLO 검출 · ReID · 추적 파이프라인. 서비스 전체 구성·실행 방법은 상위
`aba_ai_service/README.md` 참고.

## ReID 백엔드 폴백

`ReIDEngine`(`follower_perception/reid_engine.py`)은 설치된 패키지에 따라
`torchreid`(OSNet, osnet_x0_25) → `torchvision` MobileNetV3-Small → 6-float
컬러 통계, 세 단계로 자동 폴백한다. 이 배포에는 `torchreid` 가 없어 실제로는
**MobileNetV3-Small** 로 돈다 — ImageNet 분류용으로 학습된 특징이지, 사람
재식별(person-ReID) 전용 임베딩이 아니다.

그 위에 얹은 `constants.py` 의 `REID_THRESHOLD = 0.68` 은 이 폴백 상태 기준으로는
경험적 근거가 없다. 같은 owner crop 쌍의 실측 ReID 코사인 유사도 분포(중앙값
0.70~0.75, p5 는 0.54 대 — 0.68 임계값을 사이에 두고 넓게 퍼짐)는
`reports/owner_separability_20260801.md` 에 있다. `pip install torchreid` 를
설치하면 OSNet 으로 전환된다.
