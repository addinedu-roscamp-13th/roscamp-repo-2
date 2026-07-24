from follower_perception.bbox_smoother import BBoxSmoother


def test_first_update_sets_state():
    s = BBoxSmoother()
    s.update(100.0, 50.0, 400.0, dt=0.05)
    cx, cy, area = s.predict(0.0)
    assert abs(cx - 100.0) < 1e-6
    assert abs(area - 400.0) < 1e-6


def test_predict_none_before_any_update():
    assert BBoxSmoother().predict(0.05) is None


def test_extrapolates_constant_velocity():
    s = BBoxSmoother()
    # cx moves +10 per step at dt=1.0; feed several steps to build velocity
    for i in range(10):
        s.update(100.0 + 10.0 * i, 0.0, 400.0, dt=1.0)
    cx_next, _, _ = s.predict(1.0)
    # last measurement was 190; one more step should land near 200
    assert 197.0 < cx_next < 203.0


def test_reset_clears_state():
    s = BBoxSmoother()
    s.update(1.0, 2.0, 3.0, dt=0.05)
    s.reset()
    assert s.predict(0.05) is None
