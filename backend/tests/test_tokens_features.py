import numpy as np
import pytest

from app.features import (
    FEATURE_NAMES, MOTION_THRESHOLD, WALKING_THRESHOLD, extract_windows, motion_level, window_features,
)
from app.tokens import TOKEN_HEX_LEN, TokenResolver, new_secret, token_for, window_index
from simulator import synth

# ---- tokens ------------------------------------------------------------------


def test_token_shape_and_determinism():
    s = new_secret()
    t = token_for(s, 100)
    assert len(t) == TOKEN_HEX_LEN and int(t, 16) >= 0
    assert t == token_for(s, 100)


def test_token_changes_with_window_and_secret():
    a, b = new_secret(), new_secret()
    assert token_for(a, 1) != token_for(a, 2)
    assert token_for(a, 1) != token_for(b, 1)


def test_resolver_accepts_current_and_adjacent_windows_only():
    secret = new_secret()
    r = TokenResolver(lambda: {"asha": secret}, window_s=30, skew_windows=1)
    ts = 30 * 1000 + 5
    w = window_index(ts, 30)
    assert r.resolve(token_for(secret, w), ts) == "asha"
    assert r.resolve(token_for(secret, w - 1), ts) == "asha"
    assert r.resolve(token_for(secret, w + 1), ts) == "asha"
    assert r.resolve(token_for(secret, w - 2), ts) is None
    assert r.resolve(token_for(secret, w + 2), ts) is None


def test_resolver_rejects_unknown_token_and_replay_hours_later():
    secret = new_secret()
    r = TokenResolver(lambda: {"asha": secret}, window_s=30)
    ts = 1_000_000.0
    tok = token_for(secret, window_index(ts, 30))
    assert r.resolve("0" * 16, ts) is None
    assert r.resolve(tok, ts + 3600) is None


def test_resolver_picks_up_new_students_after_invalidate():
    secrets = {}
    r = TokenResolver(lambda: dict(secrets), window_s=30)
    ts = 5000.0
    s = new_secret()
    tok = token_for(s, window_index(ts, 30))
    assert r.resolve(tok, ts) is None
    secrets["ravi"] = s
    assert r.resolve(tok, ts) is None           # stale cache until invalidated
    r.invalidate()
    assert r.resolve(tok, ts) == "ravi"


def test_resolver_cache_is_bounded():
    r = TokenResolver(lambda: {"a": new_secret()}, window_s=30, max_cached_windows=4)
    for k in range(20):
        r.resolve("0" * 16, 30 * k)
    assert len(r._tables) <= 4


# ---- features ----------------------------------------------------------------

P = synth.Person.random(1)


def test_motion_levels_separate_desk_hand_and_walking():
    desk = motion_level(synth.on_desk(10)[0])
    hand = motion_level(synth.in_hand(10)[0])
    walking = motion_level(synth.walk(P, 10)[0])
    assert desk < MOTION_THRESHOLD < hand < WALKING_THRESHOLD < walking


def test_window_count_and_shape():
    a, g = synth.walk(P, 10, fs=50)
    X, motion = extract_windows(a, g, 50.0)
    assert X.shape == (9, len(FEATURE_NAMES)) and motion.shape == (9,)


def test_short_recording_yields_no_windows():
    a, g = synth.walk(P, 1.5, fs=50)
    X, motion = extract_windows(a, g, 50.0)
    assert X.shape == (0, len(FEATURE_NAMES)) and motion.shape == (0,)


@pytest.mark.parametrize("make", [synth.on_desk, synth.in_hand, lambda s: synth.walk(P, s)])
def test_features_are_finite(make):
    a, g = make(6)
    X, _ = extract_windows(a, g, 50.0)
    assert np.isfinite(X).all()


def test_constant_signal_does_not_produce_nan():
    a = np.tile([0.0, 0.0, 9.81], (100, 1))
    assert np.isfinite(window_features(a, np.zeros((100, 3)), 50.0)).all()


def test_step_frequency_is_recovered():
    person = synth.Person.random(3)
    a, g = synth.walk(person, 4, fs=50, seed=1)
    X, _ = extract_windows(a, g, 50.0)
    dom = X[:, FEATURE_NAMES.index("amag_domfreq")]
    # magnitude of a vertical-dominated signal peaks at the step rate or its double
    assert np.all(np.isclose(dom, person.step_hz, atol=0.5) | np.isclose(dom, 2 * person.step_hz, atol=0.5))


def test_known_answer_vectors_for_other_implementations():
    """The Android app must reproduce these exactly.

    token = hex(HMAC-SHA256(secret_bytes, b"ble-wifi/v1|" + int64_big_endian(window))[:8])
    window = floor(unix_seconds / 30)
    """
    secret = "000102030405060708090a0b0c0d0e0f"
    assert token_for(secret, 0) == "20fa59fd604ac7f7"
    assert token_for(secret, 1) == "0fcc0ce391839ac6"
    assert token_for(secret, 59_000_000) == "f603b385647a8b4f"
    assert token_for(secret, -1) == "072bd20ea61d0c14"
