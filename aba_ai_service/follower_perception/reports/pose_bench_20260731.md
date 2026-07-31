# pose 모델 벤치

⚠️ 대조군은 비대칭 실험이다 — `rtmpose-m-body7` 승은 도메인 효과인지
데이터 양 효과인지 구분할 수 없다(모듈 머리말 참고).

## front (`pi_20260731_122344.mp4`, rotate=180, crop=460/684)

| model | torso4_pass | jitter_torso4 | accuracy | undecided_st | undecided_ly | recall_st | recall_ly | fatal_frames | fatal_max_run | ms/frame |
|---|---|---|---|---|---|---|---|---|---|---|
| yolo11n-pose | 0.687 | 0.185 | 0.197 | 0.700 | 0.810 | 0.200 | 0.190 | 16 | 16 | 15.3 |
| rtmpose-m-humanart | 0.811 | 0.144 | 0.381 | 0.525 | 0.841 | 0.469 | 0.159 | 47 | 16 | 36.8 |
| rtmpose-m-body7 | 0.841 | 0.141 | 0.372 | 0.537 | 0.841 | 0.463 | 0.143 | 53 | 16 | 21.9 |
| yolo26m-pose | 0.539 | 0.175 | 0.081 | 0.950 | 0.841 | 0.050 | 0.159 | 48 | 16 | 15.5 |

## back (`usb_20260731_122555.mp4`, rotate=0, crop=654/937)

| model | torso4_pass | jitter_torso4 | accuracy | undecided_st | undecided_ly | recall_st | recall_ly | fatal_frames | fatal_max_run | ms/frame |
|---|---|---|---|---|---|---|---|---|---|---|
| yolo11n-pose | 0.422 | 0.144 | 0.263 | 0.646 | 0.938 | 0.314 | 0.047 | 16 | 16 | 11.2 |
| rtmpose-m-humanart | 0.604 | 0.120 | 0.382 | 0.458 | 0.828 | 0.446 | 0.109 | 35 | 16 | 18.4 |
| rtmpose-m-body7 | 0.700 | 0.083 | 0.421 | 0.387 | 0.766 | 0.494 | 0.109 | 33 | 16 | 19.0 |
| yolo26m-pose | 0.385 | 0.266 | 0.191 | 0.742 | 0.922 | 0.225 | 0.047 | 30 | 16 | 11.6 |
