import hashlib
import hmac

from app.tokens import (StudentTokenResolver, decode_payload, encode_marker_payload, encode_student_payload,
                        marker_token, student_token, timestamp_ok, verify_marker_token, window_index)

SECRET = "000102030405060708090a0b0c0d0e0f"


def test_known_answer_vectors_for_android_and_esp32():
    """Other implementations must reproduce these exactly."""
    # independent derivation of the documented construction
    for w in (0, 1, 59_000_000, -1):
        mac = hmac.new(bytes.fromhex(SECRET), b"cp-student/v1|" + w.to_bytes(8, "big", signed=True), hashlib.sha256)
        assert student_token(SECRET, w) == mac.digest()[:8].hex()
    for idx, w in ((1, 0), (204, 59_000_000), (65535, -1)):
        msg = b"cp-marker/v1|" + idx.to_bytes(2, "big") + w.to_bytes(8, "big", signed=True)
        assert marker_token(SECRET, idx, w) == hmac.new(bytes.fromhex(SECRET), msg, hashlib.sha256).digest()[:8].hex()


def test_tokens_differ_by_secret_window_and_marker():
    assert student_token(SECRET, 1) != student_token(SECRET, 2)
    assert student_token(SECRET, 1) != student_token("11" * 16, 1)
    assert marker_token(SECRET, 1, 5) != marker_token(SECRET, 2, 5)
    assert student_token(SECRET, 5) != marker_token(SECRET, 1, 5)          # domain separated


def test_payload_roundtrip_and_rejects_foreign_data():
    tok = student_token(SECRET, 7)
    raw = encode_student_payload(tok)
    assert len(raw) == 9 and decode_payload(raw) == {"type": "student", "token": tok}
    mt = marker_token(SECRET, 204, 7)
    raw = encode_marker_payload(204, mt)
    assert len(raw) == 11 and decode_payload(raw) == {"type": "marker", "marker_idx": 204, "token": mt}
    assert decode_payload(b"\x09" + bytes(8)) is None
    assert decode_payload(b"\x01\x02") is None


def test_payloads_fit_in_a_legacy_ble_advertisement():
    # flags(3) + manufacturer AD (len+type+company 2 + payload) must be <= 31 bytes
    assert 3 + 2 + 2 + len(encode_marker_payload(1, "00" * 8)) <= 31


def test_marker_verification_window():
    ts = 30 * 1000 + 3
    w = window_index(ts, 30)
    assert verify_marker_token(SECRET, 5, marker_token(SECRET, 5, w), ts, 30, 1)
    assert verify_marker_token(SECRET, 5, marker_token(SECRET, 5, w - 1), ts, 30, 1)
    assert not verify_marker_token(SECRET, 5, marker_token(SECRET, 5, w - 2), ts, 30, 1)
    assert not verify_marker_token(SECRET, 6, marker_token(SECRET, 5, w), ts, 30, 1)      # wrong marker idx


def test_resolver_expiry_and_invalidation():
    secrets = {}
    r = StudentTokenResolver(lambda: dict(secrets), 30, 1)
    ts = 90_000.0
    tok = student_token(SECRET, window_index(ts, 30))
    assert r.resolve(tok, ts) is None
    secrets["abc"] = SECRET
    assert r.resolve(tok, ts) is None             # cached empty table until invalidated
    r.invalidate()
    assert r.resolve(tok, ts) == "abc"
    assert r.resolve(tok, ts + 3600) is None      # a captured token is useless an hour later


def test_timestamp_window():
    assert timestamp_ok(1000, 1100, 120) and not timestamp_ok(1000, 1200, 99)


def test_literal_vectors_to_port_to_kotlin_and_c():
    s = "000102030405060708090a0b0c0d0e0f"
    assert student_token(s, 0) == "8bdd878956cddb95"
    assert student_token(s, 1) == "16df7955866e124e"
    assert student_token(s, 59_000_000) == "bc65de6d870ec36a"
    assert student_token(s, -1) == "f73a220ac6e2c585"
    assert marker_token(s, 1, 0) == "074eac309307cde8"
    assert marker_token(s, 204, 59_000_000) == "bb99608351456553"
    assert marker_token(s, 65535, -1) == "2663935f3f961da9"
