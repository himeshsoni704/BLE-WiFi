import pytest

from app.dwell import DwellConfig, aggregate
from app.fusion import Evidence, FusionConfig, State, Verdict, fuse, risk_score

CFG = FusionConfig(owner_threshold=0.5, stationary_limit_s=3600)
HOUR = 3600


def ev(**kw):
    base = dict(ble_zone="r101", wifi_zone="r101", owner_score=0.9, stationary_s=10.0)
    base.update(kw)
    return Evidence(**base)


def test_verified_when_zones_agree_owner_high_and_moving():
    v = fuse(ev(), CFG)
    assert v.state is State.VERIFIED
    assert v.zone == "r101"
    assert v.risk == pytest.approx(0.1, abs=0.01)


def test_suspect_when_owner_score_low():
    v = fuse(ev(owner_score=0.1), CFG)
    assert v.state is State.SUSPECT
    assert any("does not match owner" in r for r in v.reasons)
    assert v.risk > 0.8


def test_suspect_when_stationary_past_limit():
    v = fuse(ev(stationary_s=HOUR + 1), CFG)
    assert v.state is State.SUSPECT
    assert any("left behind" in r for r in v.reasons)


def test_stationary_just_under_limit_is_not_suspect():
    assert fuse(ev(stationary_s=HOUR - 1), CFG).state is State.VERIFIED


def test_stationary_but_screen_in_use_is_not_left_behind():
    v = fuse(ev(stationary_s=5 * HOUR, interaction_recent=True), CFG)
    assert v.state is State.VERIFIED


def test_handoff_flag_beats_interaction():
    # someone is using the phone, but it is not the owner
    v = fuse(ev(owner_score=0.05, stationary_s=5 * HOUR, interaction_recent=True), CFG)
    assert v.state is State.SUSPECT


def test_single_signal_is_uncertain():
    ble_only = fuse(Evidence(ble_zone="r101"), CFG)
    wifi_only = fuse(Evidence(wifi_zone="r101", owner_score=0.9, stationary_s=1.0), CFG)
    assert ble_only.state is State.UNCERTAIN and ble_only.zone == "r101"
    assert wifi_only.state is State.UNCERTAIN and wifi_only.zone == "r101"
    assert any("Bluetooth" in r for r in wifi_only.reasons)


def test_single_signal_stays_uncertain_even_when_owner_low_but_reports_risk():
    v = fuse(Evidence(wifi_zone="r101", owner_score=0.0), CFG)
    assert v.state is State.UNCERTAIN
    assert v.risk == 1.0


def test_zone_mismatch_is_uncertain():
    v = fuse(ev(wifi_zone="r102"), CFG)
    assert v.state is State.UNCERTAIN
    assert v.zone == "r101"
    assert "r102" in v.reasons[0]


def test_nothing_is_not_detected():
    v = fuse(Evidence(), CFG)
    assert v.state is State.NOT_DETECTED
    assert v.zone is None and v.risk is None


def test_no_owner_score_is_verified_by_presence_unless_required():
    e = ev(owner_score=None)
    assert fuse(e, CFG).state is State.VERIFIED
    strict = FusionConfig(owner_threshold=0.5, stationary_limit_s=3600, require_owner_score=True)
    v = fuse(e, strict)
    assert v.state is State.UNCERTAIN
    assert "no owner score" in v.reasons[0]


def test_risk_is_none_without_indicators():
    assert risk_score(Evidence(ble_zone="a", wifi_zone="a"), CFG) is None


def test_risk_stationary_ramps_from_half_limit():
    r = lambda s: risk_score(Evidence(stationary_s=s), CFG)
    assert r(0.4 * HOUR) == 0.0
    assert r(0.75 * HOUR) == pytest.approx(0.5)
    assert r(HOUR) == 1.0
    assert r(10 * HOUR) == 1.0


def test_risk_combines_indicators():
    e = Evidence(owner_score=0.5, stationary_s=0.75 * HOUR)
    assert risk_score(e, CFG) == pytest.approx(0.75)     # 1 - (0.5 * 0.5)


def test_config_validation():
    with pytest.raises(ValueError):
        FusionConfig(owner_threshold=1.5)
    with pytest.raises(ValueError):
        FusionConfig(stationary_limit_s=0)


# ---- dwell -----------------------------------------------------------------

def V(state, zone="r101"):
    return Verdict(state, None if state is State.NOT_DETECTED else zone, None, None, ())


def test_walk_by_is_not_counted():
    verdicts = [V(State.UNCERTAIN)] + [V(State.NOT_DETECTED)] * 49
    d = aggregate(verdicts)
    assert d.present == 1 and not d.counted and d.state is State.UNCERTAIN


def test_full_period_is_counted_and_verified():
    d = aggregate([V(State.VERIFIED)] * 50)
    assert d.counted and not d.flagged and d.state is State.VERIFIED and d.presence_ratio == 1.0


def test_seventy_percent_boundary():
    at = aggregate([V(State.VERIFIED)] * 7 + [V(State.NOT_DETECTED)] * 3)
    below = aggregate([V(State.VERIFIED)] * 6 + [V(State.NOT_DETECTED)] * 4)
    assert at.counted and not below.counted


def test_suspect_share_flags_student():
    d = aggregate([V(State.VERIFIED)] * 6 + [V(State.SUSPECT)] * 4)
    assert d.flagged and d.state is State.SUSPECT and d.counted


def test_occasional_suspect_slot_does_not_flag():
    d = aggregate([V(State.VERIFIED)] * 9 + [V(State.SUSPECT)])
    assert not d.flagged and d.state is State.VERIFIED


def test_zone_filter_ignores_other_zones():
    verdicts = [V(State.VERIFIED, "r101")] * 5 + [V(State.VERIFIED, "r102")] * 5
    d = aggregate(verdicts, DwellConfig(), zones={"r101"})
    assert d.present == 5 and d.presence_ratio == 0.5 and not d.counted


def test_empty_series():
    d = aggregate([])
    assert d.state is State.NOT_DETECTED and not d.counted and d.presence_ratio == 0.0
