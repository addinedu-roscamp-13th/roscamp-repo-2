# All values are reference starting points for tuning — not hard requirements.

# Detection
MIN_CONFIDENCE = 0.42          # YOLO person confidence floor

# Owner identification (dual gate)
REID_THRESHOLD = 0.68          # cosine similarity floor
HSV_THRESHOLD = 0.30           # histogram correlation floor ([0,1])
VERIFY_FRAMES = 5              # consecutive passes to lock safe_id

# Online gallery
CALIBRATION_INTERVAL = 15      # frames between online updates (ReID gallery + HSV EMA); ~1s @15fps
CALIBRATION_ADD_THRESHOLD = 0.85  # append only if best gallery sim < this
MAX_GALLERY_SIZE = 50
HSV_UPDATE_ALPHA = 0.1            # online HSV template EMA rate (adapt to lighting; 0 = off)

# Registration
REGISTRATION_STABLE_FRAMES = 3    # consecutive frames the central target must persist
REGISTRATION_MIN_AREA_RATIO = 0.01  # min bbox area / frame area to register

# Smoothing / coasting
SMOOTHER_ALPHA = 0.45
SMOOTHER_BETA = 0.15
FRAME_DT = 0.05                # nominal seconds per frame (20 FPS)
PREDICT_DT = 0.05             # latency-compensation lookahead
COAST_LIMIT = 30              # max consecutive missed frames still output (predicted; ~2s @15fps)

# HSV histogram
HSV_BINS = 16                 # per channel; total 48-d (H+S+V)
