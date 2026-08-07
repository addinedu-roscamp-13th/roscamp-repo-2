"""초록 표식 중점. 합성 이미지로 색 임계만 본다."""
import numpy as np
import pytest

from app.shelf.green_marker import GreenConfig, centroid_u, centroid_uv, detect_candidates

WHITE_BGR = (230, 230, 230)   # 저채도·고명도 — _white_bordered 의 "흰색" 기준을 통과


def _canvas(h=240, w=320):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _paint(img, x0, x1, y0=100, y1=140, bgr=(120, 200, 40)):
    img[y0:y1, x0:x1] = bgr
    return img


def test_blank_frame_finds_nothing():
    assert centroid_u(_canvas()) is None


def test_none_frame_finds_nothing():
    assert centroid_u(None) is None


def test_green_bar_centroid_is_its_middle():
    u = centroid_u(_paint(_canvas(), 140, 180))
    assert u is not None
    assert 158.0 <= u <= 162.0


def test_offset_bar_moves_the_centroid():
    left = centroid_u(_paint(_canvas(), 40, 80))
    right = centroid_u(_paint(_canvas(), 240, 280))
    assert left is not None and right is not None
    assert left < 80.0 < 240.0 < right


def test_red_object_is_ignored():
    assert centroid_u(_paint(_canvas(), 140, 180, bgr=(20, 20, 220))) is None


def test_white_shelf_is_ignored():
    assert centroid_u(_paint(_canvas(), 0, 320, y0=0, y1=240,
                             bgr=(245, 245, 245))) is None


def test_speck_below_the_minimum_area_is_ignored():
    img = _paint(_canvas(), 160, 162, y0=120, y1=122)
    assert centroid_u(img) is None


def test_minimum_area_is_configurable():
    img = _paint(_canvas(), 160, 162, y0=120, y1=122)
    assert centroid_u(img, GreenConfig(min_area_px=1)) is not None


def test_largest_blob_wins():
    """둘 다 최소 면적을 넘긴 상태에서 큰 쪽이 이겨야 한다.

    ⚠️ 두 함정을 다 피해야 한다:
    1) 작은 쪽이 min_area_px 에 걸려 탈락하면 이 시험은 최소면적 필터만 재검증한다.
    2) 큰 쪽을 항상 나중에(오른쪽에) 두면, "가장 큰 것" 대신 "마지막으로 본 것"을
       고르는 결함도 같은 값을 내놓아 안 걸린다(연결요소 라벨이 스캔 순서라 오른쪽이
       늘 나중 라벨이라서다). 그래서 **큰 덩어리를 왼쪽(먼저 스캔되는 라벨)에** 둔다 —
       "마지막 라벨이 이긴다"는 결함이면 이 시험이 오른쪽(작은 쪽)을 골라 빨개진다.
    """
    img = _canvas()
    _paint(img, 20, 100, y0=100, y1=140)     # 80x40 = 3200px (큼, 왼쪽 → 라벨이 먼저)
    _paint(img, 200, 240, y0=100, y1=140)    # 40x40 = 1600px (작음, 오른쪽 → 라벨이 나중)
    u = centroid_u(img)
    assert u is not None and u < 100.0


# ── 흰-초록-흰 테두리 검증 (2026-08-05, 실무 shape/edge 매칭 방식) ─────────────

def _white_canvas(h=240, w=320):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = WHITE_BGR
    return img


def test_prefers_white_bordered_blob_over_a_bigger_unbordered_one():
    """실측(2026-08-05): "제일 큰 덩어리"만 보면 옆 서가 반사(흰 테두리 없음)가 진짜
    마커(흰 선반 판 사이, 흰 테두리 있음)보다 크게 찍혔을 때 반사를 고른다. 진짜
    마커는 작아도, 흰-초록-흰 구조를 만족하는 쪽을 우선해야 한다."""
    img = _white_canvas()
    # 진짜 마커: 흰 배경(선반 판) 안에 작게 — 위/아래가 이미 흰 배경이라 테두리 있음.
    _paint(img, 60, 90, y0=100, y1=120)                    # 30x20=600px, 흰 테두리 O
    # 가짜(반사): 검은 배경 위의 훨씬 큰 초록 — 흰 테두리 없음.
    dark_x0, dark_x1 = 180, 280
    img[60:180, dark_x0:dark_x1] = (10, 10, 10)
    _paint(img, 200, 260, y0=100, y1=140)                  # 60x40=2400px, 훨씬 큼

    uv = centroid_uv(img)
    assert uv is not None
    u, _v = uv
    assert u < 100.0   # 작아도 흰 테두리 있는 진짜 마커(x=60~90) 쪽을 골라야 한다


def test_falls_back_to_largest_when_nothing_is_white_bordered():
    """후보가 있는데 아무도 흰 테두리를 못 채우면(예: 마커가 화면 가장자리에 걸침)
    안전망으로 예전처럼 가장 큰 덩어리로 떨어뜨린다 — 완전히 검출 실패하면 안 된다."""
    img = _canvas()   # 검은 배경 — 어떤 초록도 흰 테두리를 못 채운다
    _paint(img, 20, 100, y0=100, y1=140)
    _paint(img, 200, 240, y0=100, y1=140)
    uv = centroid_uv(img)
    assert uv is not None
    assert uv[0] < 100.0   # 여전히 큰 쪽(왼쪽)을 고른다 — test_largest_blob_wins 와 동치


def test_white_border_check_respects_configured_thresholds():
    """GreenConfig 로 흰색 판정 기준을 밖에서 조정할 수 있어야 한다(임계가 실제
    조명에서 견디는지는 실기에서만 판정된다는 모듈 머리말 원칙 그대로)."""
    img = _canvas()
    _paint(img, 140, 180, y0=100, y1=140)
    # 배경이 검은색이라 기본 기준(v_min=110)으로는 흰 테두리 없음 → 그래도 검출은 됨
    # (안전망), 근데 white_v_min 을 0으로 낮추면 검은 배경도 "흰색"으로 쳐서
    # 테두리 판정 경로 자체가 달라진다 — 크래시 없이 여전히 같은 중점을 낸다.
    uv_default = centroid_uv(img)
    uv_loose = centroid_uv(img, GreenConfig(white_v_min=0, white_s_max=255))
    assert uv_default is not None and uv_loose is not None
    assert uv_default == uv_loose


# ── hint_u 연속성 (2026-08-05, 실측: 후보 갈아타서 stable_frames 가 22→0으로 튐) ──

def test_hint_u_prefers_the_candidate_close_to_the_previous_position():
    """후보가 둘 다 흰 테두리를 채워도(둘 다 "합격") hint 없이는 더 큰 쪽이 이긴다 —
    직전 위치에 있던 작은 쪽이 실제 마커라도 큰 반사가 새로 나타나면 그쪽으로
    순간이동한다. hint_u 를 주면 계속 추적하던 쪽을 유지해야 한다."""
    img = _white_canvas()
    _paint(img, 60, 90, y0=100, y1=120)     # 추적 중이던 작은 마커, u≈75
    _paint(img, 220, 260, y0=100, y1=140)   # 새로 나타난 더 큰 흰 테두리 블롭, u≈240

    # hint 없음(cold start) — 예전처럼 큰 쪽(240 근처)이 이긴다.
    assert centroid_uv(img)[0] > 200.0
    # hint_u=75(직전 프레임 위치) — 작아도 계속 추적하던 쪽을 유지해야 한다.
    uv = centroid_uv(img, hint_u=75.0)
    assert uv[0] < 100.0


# ── detect_candidates (2026-08-05, 뷰어가 흰테두리 후보를 박스로 그리기 위한 노출) ──

def test_detect_candidates_reports_box_and_bordered_flag_per_candidate():
    """centroid_uv 가 내부에서 고르는 것과 같은 후보 목록을 밖에서도 볼 수 있어야
    한다 — 뷰어가 이걸로 박스를 그리면 실제 판정과 절대 어긋나지 않는다."""
    img = _white_canvas()
    _paint(img, 60, 90, y0=100, y1=120)     # 흰 배경 위 → bordered=True
    dark_x0, dark_x1 = 180, 280
    img[60:180, dark_x0:dark_x1] = (10, 10, 10)
    _paint(img, 200, 260, y0=100, y1=140)   # 검은 배경 위 → bordered=False

    cands = detect_candidates(img)
    assert len(cands) == 2
    small = min(cands, key=lambda c: c["area"])
    big = max(cands, key=lambda c: c["area"])
    assert small["bordered"] is True
    assert big["bordered"] is False
    assert small["x"] == 60 and small["w"] == 30


# ── 테두리는 **좌우**로 본다 (실제 서가에서는 위가 책등이라 위쪽 기준이 깨진다) ──

def test_white_border_is_checked_on_the_sides_not_above():
    """위만 흰색이고 좌우가 어두우면 **떨어져야** 한다.

    ⚠️ 되돌림 감지용 시험이다 — 위쪽 밴드를 보던 옛 구현이면 이게 True 로 나와 빨개진다.
    """
    img = _canvas()                       # 검은 배경
    img[80:100, 140:180] = WHITE_BGR      # 덩어리 바로 '위'만 희게
    _paint(img, 140, 180, y0=100, y1=140)

    cands = detect_candidates(img)
    assert len(cands) == 1
    assert cands[0]["bordered"] is False


def test_both_visible_sides_must_be_white():
    """한쪽만 희고 반대쪽이 보이는데 어두우면 떨어진다 — 반사에는 엄격하게."""
    img = _canvas()
    img[100:140, 120:140] = WHITE_BGR     # 왼쪽만 희게
    _paint(img, 140, 180, y0=100, y1=140)

    assert detect_candidates(img)[0]["bordered"] is False


def test_side_cut_off_by_the_frame_is_skipped_not_failed():
    """화면 가장자리에 걸려 한쪽 밴드가 통째로 없으면, 그쪽은 검사에서 빼고
    남은 쪽만 본다 — 잘림에는 관대하게(안 그러면 가장자리 마커가 영영 못 붙는다)."""
    img = _canvas()
    _paint(img, 0, 40, y0=100, y1=140)    # 왼쪽 끝에 붙어 왼쪽 밴드가 없다
    img[100:140, 40:60] = WHITE_BGR       # 오른쪽 밴드만 희게

    assert detect_candidates(img)[0]["bordered"] is True


def test_hint_u_ignored_when_only_one_candidate():
    """후보가 하나뿐이면 hint 값과 상관없이 그걸 낸다 — 엉뚱한 hint 때문에
    유일한 진짜 후보를 버리면 안 된다."""
    img = _white_canvas()
    _paint(img, 140, 180, y0=100, y1=140)
    uv_no_hint = centroid_uv(img)
    uv_far_hint = centroid_uv(img, hint_u=0.0)   # 엉뚱하게 먼 hint
    assert uv_no_hint == uv_far_hint
